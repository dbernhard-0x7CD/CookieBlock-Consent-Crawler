from __future__ import annotations

import re
from typing import Tuple, Optional, TYPE_CHECKING, cast

import traceback

from ast import literal_eval

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

from crawler.enums import CookieCategory, CrawlState, PageState
from crawler.utils import uuid_pattern, logger
from crawler.database import SiteVisit

if TYPE_CHECKING:
    from crawler.browser import CBConsentCrawlerBrowser

# url for the cookiebot consent CDN
cb_base_url = r"https://consent\.cookiebot\.(com|eu)/"

name_to_cat = {"Necessary": CookieCategory.ESSENTIAL,
               "Preference": CookieCategory.FUNCTIONAL,
               "Statistics": CookieCategory.ANALYTICAL,
               "Advertising": CookieCategory.ADVERTISING,
               "Unclassified": CookieCategory.UNCLASSIFIED}

# regex patterns for cookiebot urls
cb_base_pat = re.compile(cb_base_url)
cbid_variant2_pat = re.compile(cb_base_url + r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/cc\.js")
cbid_variant3_pat = re.compile(r"[&?]cbid=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")

# patterns to parse the final cc.js file, which is where the actual category data is stored
category_patterns = {CookieCategory.ESSENTIAL: re.compile(r"CookieConsentDialog\.cookieTableNecessary = (.*);"),
                     CookieCategory.FUNCTIONAL: re.compile(r"CookieConsentDialog\.cookieTablePreference = (.*);"),
                     CookieCategory.ANALYTICAL: re.compile(r"CookieConsentDialog\.cookieTableStatistics = (.*);"),
                     CookieCategory.ADVERTISING: re.compile(r"CookieConsentDialog\.cookieTableAdvertising = (.*);"),
                     CookieCategory.UNCLASSIFIED: re.compile(r"CookieConsentDialog\.cookieTableUnclassified = (.*);")}


def check_cookiebot_presence(webdriver: WebDriver) -> bool:
    """ Check whether Cookiebot is referenced on the website """
    psource = webdriver.page_source
    matchobj = cb_base_pat.search(psource, re.IGNORECASE)
    return matchobj is not None


def internal_cookiebot_scrape(url: str, visit: SiteVisit, webdriver: CBConsentCrawlerBrowser) -> Tuple[CrawlState, str]:
    """
    Cookiebot stores its cookie category data in a javascript file called cc.js
    The crawling process attempts to obtain this file and read the data from it.
    @param url: URL to crawl for the category data
    @param visit: Visit to the site
    @param webdriver: The browser instance used to crawl
    @return: A tuple consisting of 2 values:
        1. Resulting crawl state.
        2. Error report, or number of extracted cookies if successful.
    """
    assert webdriver.crawl

    browser_id = webdriver.crawl.browser_id

    # try to retrieve cookiebot ID required to access cc.js
    cbid, tld = cast(Tuple[str, str], _try_retrieve_cbid_all_variants(webdriver))

    if cbid is None:
        report = f"COOKIEBOT: Failed to find cbid on {url}"
        logger.error(report, browser_id)
        return CrawlState.PARSE_ERROR, report

    logger.info("COOKIEBOT: Cookiebot UUID = %s, TLD = %s (browser_id: %s)", cbid, tld, browser_id)

    # retrieve cc.js file from cookiebot cdn domain using the requests library
    referer = _try_find_correct_referer(webdriver.driver.page_source, browser_id, cbid, tld, url)

    cc_url = f"https://consent.cookiebot.{tld}/{cbid}/cc.js?referer={referer}"
    # r, crawlstate, report = simple_get_request(cc_url, browser_id=browser_id, timeout=(6, 30), headers={"Referer": url})
    # state, r = TODO (TODO: extend webdriver.get_content to set headers)
    state, response = webdriver.get_content(cc_url)
    
    if state != PageState.OK or response is None:
        logger.error(f"COOKIEBOT: Failed to retrieve cc.js for {cc_url} -- Details: response: {response}; state: {state} (browser_id, {browser_id})")
        return CrawlState.LIBRARY_ERROR, f"PageState of {cc_url} is {state}"

    # some required structural checks on the javascript file contents
    js_contents = response

    if "CookieConsent.setOutOfRegion" in js_contents:
        msg = f"COOKIEBOT: Received an out-of-region response from \"{cc_url}\""
        logger.error(msg + " (browser_id %s)", browser_id)
        return CrawlState.REGION_BLOCK, msg
    elif re.search("cookiedomainwarning='Error: .* is not a valid domain.", js_contents):
        msg = f"COOKIEBOT: Unrecognized referer: {referer}."
        logger.error(msg + f" (browser_id {browser_id})")
        return CrawlState.LIBRARY_ERROR, msg
    elif len(js_contents.strip()) == 0:
        msg = f"COOKIEBOT: Empty response when trying to retrieve \"{cc_url}\"."
        logger.error(msg + f" (browser_id {browser_id})")
        return CrawlState.MALFORM_RESP, msg

    logger.info(f"COOKIEBOT: Successfully accessed \"https://consent.cookiebot.{tld}/{cbid}/cc.js\" (browser_id: {browser_id})")

    # finally, if we arrived here we (most likely) found our cookie category data
    cookie_count = 0
    try:
        for cat_name in name_to_cat.keys():
            cat_id = name_to_cat[cat_name]
            matchobj = category_patterns[cat_id].search(js_contents)

            if not matchobj:
                logger.warning(f"COOKIEBOT: Could not find array for category {cat_name}")
                continue

            logger.info("matchobj: %s", matchobj)

            # transform the string arrays to python arrays
            # cookies = literal_eval(matchobj.group(1))

            # for c in cookies:
            #     cookie_count += 1

                # TODO store_consent_data
                # send_cookiedat_to_db(c[0], c[1], cat_id, cat_name, browser_id, visit_id, c[2], c[3], c[4], c[5])

    # format of the cookiebot data should be uniform, but in case this happens
    # to be violated, this try-except block catches it
    except Exception as ex:
        msg = f"COOKIEBOT: Failed to extract cookie data from {cc_url}: {type(ex)} {ex}"
        traceback.print_exc()
        logger.error(msg + f"(browser_id {browser_id}")
        return CrawlState.MALFORM_RESP, msg

    if cookie_count == 0:
        msg = f"COOKIEBOT: No cookies found in {cc_url}"
        logger.error(msg + f"(browser_id {browser_id}")
        return CrawlState.NO_COOKIES, msg

    logger.info(f"COOKIEBOT: Extracted {cookie_count} cookie entries. (browser_id {browser_id})")
    logger.info(f"COOKIEBOT: Extracted {cookie_count} cookie entries.")
    return CrawlState.SUCCESS, f"Extracted {cookie_count} cookie entries."


class _exists_script_tag_with_cbid():
    """
    Variant 1 Pattern: Utility class to check if there exists a script
    tag with the 'data-cbid' attribute. This attribute contains the
    desired cookiebot ID.
    :return WebElement: first matching script tag, or False otherwise
    """
    def __init__(self, browser_id):
        self.browser_id = browser_id

    def __call__(self, driver):
        elems = driver.find_elements(By.TAG_NAME, "script")
        for e in elems:
            try:
                cbid = e.get_attribute("data-cbid")
                if cbid and uuid_pattern.match(str(cbid)):
                    return e
            except StaleElementReferenceException:
                continue
        return False


def _find_cbid_script_tag(driver: WebDriver, browser_id: int, timeout: int = 5) -> Optional[Tuple[str, str]]:
    """
    Wait for the Cookie Bot ID to be found, and return it if this occurs.
    :param driver: Selenium webdriver currently used.
    :param timeout: Time to wait in seconds.
    :return Cookie bot ID, or None if none found.
    """
    try:
        wait = WebDriverWait(driver, timeout)
        element = wait.until(_exists_script_tag_with_cbid(browser_id))
        cbid = element.get_attribute("data-cbid")
        src = element.get_attribute("src")
        
        res = cb_base_pat.search(src)
        if cbid and src and res:
            return str(cbid), res.group(1)
        return None
    except TimeoutException:
        return None


def _try_retrieve_cbid_all_variants(browser: CBConsentCrawlerBrowser) -> Optional[Tuple[str, str]]:
    """
    Attempt to retrieve the 'cbid' value with both the cbid tag approach,
    as well as by simply searching the page source using a regular expression pattern.
    :param webdriver: Selenium webdriver
    :return CBID, or None if not found.
    """
    browser_id = browser.crawl.browser_id

    # Try to find the Cookie Bot ID inside of a script tag, using the cbid attribute.
    maybe_cbid = browser.execute_in_IFrames(_find_cbid_script_tag, timeout=3)

    if maybe_cbid:
        logger.info(f"COOKIEBOT: Found Cookiebot ID using Variant 1, tld: {maybe_cbid[1]} (browser_id {browser_id})")

        return maybe_cbid
    else:
        # Variant 2 & 3: CBID may actually be integrated into the URL itself, rather
        # than being an attribute. Simply use a regex on the page source for this.
        page_source = browser.driver.page_source

        variant_2 = cbid_variant2_pat.search(page_source)
        variant_3 = cbid_variant3_pat.search(page_source)
        if variant_2:
            logger.info("COOKIEBOT: Found Cookiebot ID using Variant 2 (browser_id %s)", browser_id)

            return variant_2.group(2), variant_2.group(1)
        elif variant_3:
            logger.info("COOKIEBOT: Found Cookiebot ID using Variant 3 (browser_id %s)", browser_id)

            return variant_3.group(2), variant_3.group(1)
        else:
            logger.error("COOKIEBOT: Could not find the Cookiebot ID (browser_id: %s)", browser_id)
            return None


def _try_find_correct_referer(source: str, browser_id: Optional[int], cbid: str, tld: str, fallback: str) -> str:
    """
    The referer required to access the Cookiebot data may differ from the site the request
    is made from. In this case, the referer is listed as an argument inside the cc.js URL
    itself. This extracts said URL.
    :param source: website source code.
    :param cbid: cookiebot ID previously discovered
    :param tld: cookiebot consent URL top level domain (com or eu)
    :param fallback: referer string to use if the referer URL cannot be found. Typically set to be the current URL.
    :return: Referer string, or defined fallback if referer cannot be found.
    """
    ref_pattern = re.compile(f"https://consent\\.cookiebot\\.{tld}/{cbid}/cc\\.js.*(\\?|&amp;)referer=(.*?)&.*")
    m = ref_pattern.search(source)
    if m:
        new_referer = m.group(2)
        logger.info("COOKIEBOT: Found referer: %s (browser_id %s)", new_referer, browser_id)
        return new_referer
    else:
        logger.info("COOKIEBOT: No referer specified, using default. (browser_id: %s)", browser_id)
        return fallback

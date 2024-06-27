from __future__ import annotations

import re
import json
from typing import Tuple, TYPE_CHECKING, Dict, Any, Optional, List, Union

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

# import js2py

from crawler.enums import CookieCategory, CrawlState, PageState
from crawler.utils import uuid_pattern, logger
from crawler.database import store_consent_data, Crawl, SiteVisit

if TYPE_CHECKING:
    from crawler.browser import CBConsentCrawlerBrowser

# Base URL patterns required for Variant A
onetrust_pattern_A = re.compile(r"(https://cdn-apac\.onetrust\.com)")
onetrust_pattern_B = re.compile(r"(https://cdn-ukwest\.onetrust\.com)")
cookielaw_base_pattern = re.compile(r"(https://cdn\.cookielaw\.org)")
cmp_cookielaw_base_pattern = re.compile(r"(https://cmp-cdn\.cookielaw\.org)")
optanon_base_pattern = re.compile(r"(https://optanon\.blob\.core\.windows\.net)")
cookiecdn_base_pattern = re.compile(r"(https://cookie-cdn\.cookiepro\.com)")
cookiepro_base_pattern = re.compile(r"(https://cookiepro\.blob\.core\.windows\.net)")

base_patterns = [onetrust_pattern_A, onetrust_pattern_B,
                 cookielaw_base_pattern, cmp_cookielaw_base_pattern, optanon_base_pattern,
                 cookiecdn_base_pattern, cookiepro_base_pattern]

# Javascript direct links, required for Variant B
v2_onetrust_pattern_A = re.compile(r"https://cdn-apac\.onetrust\.com/consent/"
                                  + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_onetrust_pattern_B = re.compile(r"https://cdn-ukwest\.onetrust\.com/consent/"
                                  + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_cookielaw_pattern = re.compile(r"https://cdn\.cookielaw\.org/consent/"
                                  + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_cmp_cookielaw_pattern = re.compile(r"https://cmp-cdn\.cookielaw\.org/consent/"
                                      + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_optanon_pattern = re.compile(r"https://optanon\.blob\.core\.windows\.net/consent/"
                                + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_cookiepro_cdn_pattern = re.compile(r"https://cookie-cdn\.cookiepro\.com/consent/"
                                      + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")
v2_cookiepro_blob_pattern = re.compile(r"https://cookiepro\.blob\.core\.windows\.net/consent/"
                                       + uuid_pattern.pattern + r"[a-zA-Z0-9_-]*\.js")

variantB_patterns = [v2_onetrust_pattern_A, v2_onetrust_pattern_B,
                     v2_cookielaw_pattern, v2_cmp_cookielaw_pattern, v2_optanon_pattern,
                     v2_cookiepro_cdn_pattern, v2_cookiepro_cdn_pattern]

# OneTrust does not have uniform category names.
# To that end, we use regex keyword patterns to map a category name to the internally defined categories.
en_necessary_pattern = re.compile(r"(mandatory|necessary|essential|required)", re.IGNORECASE)
en_analytical_pattern = re.compile(r"(measurement|analytic|anonym|research|performance|statistic)", re.IGNORECASE)
en_functional_pattern = re.compile(r"(functional|preference|security|secure|video)", re.IGNORECASE)
en_targeting_pattern = re.compile(r"(^ads.*|.*\s+ads.*|Ad Selection|advertising|advertise|targeting"
                               r"|personali[sz]ed|personali[sz]ation|sale of personal data|marketing"
                                  r"|tracking|tracker|fingerprint|geolocation|personal info)", re.IGNORECASE)
en_uncat_pattern = re.compile(r"(uncategori[zs]e|unclassified|unknown)", re.IGNORECASE)


# german patterns
de_necessary_pattern = re.compile(r"(notwendig|nötig|erforderlich)", re.IGNORECASE)
de_analytical_pattern = re.compile(r"(analyse|analytisch|leistung|statistik|performance)", re.IGNORECASE)
de_functional_pattern = re.compile(r"(funktional|funktionel|sicherheit|video)", re.IGNORECASE)
de_targeting_pattern = re.compile(r"(werbung|werbe|marketing|anzeigen|reklame|personalisiert|tracking)", re.IGNORECASE)
de_uncat_pattern = re.compile(r"(unkategorisiert|unklassifiziert|unbekannt)", re.IGNORECASE)

# social media pattern
social_media_pattern = re.compile(r"(social.media|social.network|soziales.netzwerk|soziale.medien"
                                  r"|facebook|youtube|twitter|instagram|linkedin|whatsapp|pinterest"
                                  r"|\s+xing|\s+reddit|tumblr)", re.IGNORECASE)

def check_onetrust_presence(webdriver: WebDriver) -> bool:
    """ Check whether a OneTrust pattern is referenced on the website """
    psource = webdriver.page_source
    found = False
    ot_iters = iter(base_patterns)
    try:
        while not found:
            pattern = next(ot_iters)
            if pattern.search(psource, re.IGNORECASE) is not None:
                return True
    except StopIteration:
        found = False

    return False

def internal_onetrust_scrape(url: str, visit: SiteVisit, webdriver: CBConsentCrawlerBrowser) -> Tuple[CrawlState, str]:
    """
    Extract cookie category data from the variants of the OneTrust Cookie Consent Platform.
    The category data is found in json, either separate or as an inline document inside javascript.
    The crawling process attempts to obtain this data and read the data from it.
    @param url: The website we are trying to crawl. (performs a GET request)
    @param visit: Visit 
    @param webdriver: The Selenium webdriver we use to request the page.
    @return: A tuple consisting of 2 values:
        1. Resulting crawl state.
        2. Error report, or number of extracted cookies if successful.
    """

    # Variant A, Part 1: Try to retrieve data domain id
    browser = visit.browser
    logger.info("ONETRUST: Attempting Variant A (browser_id: %s)", browser.browser_id)

    result = webdriver.execute_in_IFrames(_variantA_try_retrieve_ddid, timeout=5)

    if result:
        domain_url = result[0]
        dd_id = result[1]
        logger.info("ONETRUST: VARIANT A: OneTrust data domain url = %s, %s (browser_id: %s)", domain_url, dd_id, browser.browser_id)

        # Variant A, Part 2: Using the data domain ID, retrieve ruleset ID list
        rs_ids, state, report = _variantA_try_retrieve_ruleset_id(domain_url, dd_id, webdriver)
        if state != CrawlState.SUCCESS:
            logger.error("FAILED to retrieve ruleset_id: %s (browser_id: %s)", state, browser.browser_id)
            return state, report

        # c_logmsg(f"ONETRUST: VARIANT A: Found {len(rs_ids)} ruleset ids", browser_id, logging.INFO)
        # c_logmsg(f"ONETRUST: VARIANT A: Retrieved ruleset ids {rs_ids}", browser_id, logging.DEBUG)

        # Variant A, Part 3: For each ruleset id, retrieve cookie json
        cookie_count, state, report = _variantA_get_and_parse_json(domain_url, dd_id, rs_ids, webdriver, visit)
        if state != CrawlState.SUCCESS:
            # c_logmsg(report, browser_id, logging.ERROR)
            return state, report

        logger.info("ONETRUST: VARIANT A: Retrieved %s cookies (browser_id: %s)", cookie_count, browser.browser_id)
    else:
        # Variant B, Part 1: Obtain the javascript URL

        logger.info("ONETRUST: Attempting Variant B (browser_id: %s)", browser.browser_id)

        script_url = webdriver.execute_in_IFrames(_variantB_try_retrieve_jsurl, timeout=5)
        if not script_url:
            report = "ONETRUST: Could not find a valid OneTrust CMP Variant on this URL."
            logger.error("%s (browser_id: %s): %s", report, browser.browser_id, result)

            return CrawlState.CMP_NOT_FOUND, report
        # c_logmsg(f"ONETRUST: VARIANT B: Onetrust Javascript URL = {script_url}", browser_id, logging.INFO)
        logger.info("ONETRUST: VARIANT B: Onetrust Javascript URL = %s (browser_id %s)", script_url, browser.browser_id)

        # Variant B, Part 2: Access the script and retrieve raw data from it
        data_dict, state, report = _variantB_parse_script_for_object(script_url, webdriver)
        if state != CrawlState.SUCCESS or data_dict is None:
            logger.error("Failed with state %s: %s and data_dict %s", state, report, data_dict)
            return state, report
        logger.info("ONETRUST: VARIANT B: Successfully retrieved OneTrust Consent javascript object data. (browser_id %s)", browser.browser_id)

        # Variant B, Part 3: Extract the cookie values from raw data
        cookie_count, state, report = _variantB_extract_cookies_from_dict(data_dict, browser, visit)
        if state != CrawlState.SUCCESS:
            logger.error("Failed in part3 with state %s: %s", state, report)
            return state, report

        logger.info("ONETRUST: VARIANT B: Retrieved %s cookies (browser_id: %s)", cookie_count, browser.browser_id)

    return CrawlState.SUCCESS, f"Extracted {cookie_count} cookie entries."


def category_lookup_en(browser: Crawl, cat_name: str) -> CookieCategory:
    """
    Map english category name defined in the CMP to the internal representation.
    """
    if en_targeting_pattern.search(cat_name): return CookieCategory.ADVERTISING
    elif en_necessary_pattern.search(cat_name): return CookieCategory.ESSENTIAL
    elif en_analytical_pattern.search(cat_name): return CookieCategory.ANALYTICAL
    elif en_functional_pattern.search(cat_name): return CookieCategory.FUNCTIONAL
    elif en_uncat_pattern.search(cat_name): return CookieCategory.UNCLASSIFIED
    elif social_media_pattern.search(cat_name): return CookieCategory.SOCIAL_MEDIA
    else:
        logger.warning("ONETRUST: %s not recognized by English patterns (browser_id: %s)", cat_name, browser.browser_id)
        return CookieCategory.UNRECOGNIZED


def category_lookup_de(browser: Crawl, cat_name: str) -> CookieCategory:
    """
    Map english category name defined in the CMP to the internal representation.
    """
    if de_targeting_pattern.search(cat_name): return CookieCategory.ADVERTISING
    elif de_necessary_pattern.search(cat_name): return CookieCategory.ESSENTIAL
    elif de_analytical_pattern.search(cat_name): return CookieCategory.ANALYTICAL
    elif de_functional_pattern.search(cat_name): return CookieCategory.FUNCTIONAL
    elif de_uncat_pattern.search(cat_name): return CookieCategory.UNCLASSIFIED
    elif social_media_pattern.search(cat_name): return CookieCategory.SOCIAL_MEDIA
    else:
        logger.warning("ONETRUST: '%s' not recognized by German patterns (browser_id: %s)", cat_name, browser.browser_id)
        return CookieCategory.UNRECOGNIZED


class _exists_script_tag_with_ddid():
    """
    Extract "data-domain-script" attribute value from first script tag
    that contains it. This will allow us to access the OneTrust ruleset json.
    @return: data domain id string, or False if not found
    """

    def __init__(self, browser):
        self.browser= browser

    def __call__(self, driver) -> Union[bool, Tuple[str, str]]:
        elems = driver.find_elements(By.TAG_NAME, "script")
        for e in elems:
            try:
                # Find a script tag with the data-domain-script attribute
                dd_id = str(e.get_attribute("data-domain-script"))
                if (dd_id is not None) and (uuid_pattern.match(str(dd_id)) or str(dd_id) == "center-center-default-stack-global-ot"):
                    source_stub = e.get_attribute("src")
                    if source_stub is None:
                        logger.warning("ONETRUST: VARIANT A: Found a script tag with the data-domain attribute, but no URL? Script ID: %s (browser_id %s)", dd_id, self.browser.browser_id)
                        continue
                    else:
                        for pat in base_patterns:
                            m = pat.match(source_stub)
                            if m:
                                return m.group(1), dd_id
                        else:
                            # c_logmsg(f"ONETRUST: VARIANT A: Found a data-domain-script tag with unknown source "
                                     # f"URL: {source_stub}. Script ID: {dd_id}", self.browser_id, logging.WARN)
                            logger.warning("TODO")

            except StaleElementReferenceException:
                continue
        return False


class _exists_script_tag_with_jsurl():
    """
    Directly retrieve the link to the javascript containing the OneTrust consent categories.
    Looks for domains of the form:  "https://<domain>/consent/<UUID>.js"
    @return: (base url, data domain id) or False if not found
    """

    def __init__(self, browser):
        self.browser = browser

    def __call__(self, driver) -> Union[bool, str]:
        elems = driver.find_elements(By.TAG_NAME, "script")
        for e in elems:
            try:
                source = e.get_attribute("src")
                if source:
                    # any of them match --> extract URL. otherwise, continue to next script tag
                    for p in variantB_patterns:
                        matchobj = p.match(source)
                        if matchobj:
                            logger.info("ONETRUST: VARIANT B: Pattern found: %s (browser_id: %s)", p.pattern, self.browser.browser_id)
                            return matchobj.group(0)
            except StaleElementReferenceException:
                continue

        return False


def _variantA_try_retrieve_ddid(driver: WebDriver, browser: Crawl, timeout: int = 5) -> Optional[Tuple[str, str]]:
    """
    Variant A involves the Data Domain ID we need being stored inside a script tag attribute.
    Additionally, it retrieves the OneTrust URL used for storing the cookie categories.
    This function starts the process of searching for said data domain ID, using WebDriverWait.
    @param driver: webdriver to look for the script tag with.
    @param browser_id: identifier for the browser that performs the action
    @param timeout: timeout after which the search gives up
    @return: Tuple: (base domain, data domain ID), or None if not found
        base domain: cookielaw, optanon or cookiepro
        data domain ID: unique identifier for the CDN to access the rulesets
    """
    try:
        wait = WebDriverWait(driver, timeout)
        tup = wait.until(_exists_script_tag_with_ddid(browser.browser_id))
        # base_domain, dd_id
        return tup[0], tup[1]
    except TimeoutException:
        logger.info("ONETRUST: VARIANT A: Timeout on trying to retrieve data domain id value. (browser_id: %s)", browser.browser_id)
        return None


def _variantA_try_retrieve_ruleset_id(domain_url: str, dd_id: str,
                                      browser: CBConsentCrawlerBrowser) -> Tuple[List[Tuple[str,str]], CrawlState, str]:
    """
    Using the data-domain id, parse a list of rulesets from a json file stored on the domain url, and
    extract IDs that are essential for retrieving the json files storing the actual cookie category data.
    @param domain_url: Domain on which to access the ruleset json
    @param dd_id: Data domain ID (UUID) that is used to retrieve the ruleset json
    @param browser: Browser used to crawl the website
    @return: (cookie json ids, crawl state, report). List of ids may be empty if none found.
    """
    target_url = f"{domain_url}/consent/{dd_id}/{dd_id}.json"

    state, ruleset_json = browser.get_content(target_url)

    if state != PageState.OK:
        return [], state, f"PageState of {target_url} is {state}"

    ids = []
    rs_dict = json.loads(ruleset_json)

    try:
        rulesets = rs_dict["RuleSet"]
        if rulesets is None:
            logger.error(f"ONETRUST: VARIANT A: No valid 'RuleSet' element found on {target_url}")
            return [], CrawlState.PARSE_ERROR, f"ONETRUST: VARIANT A: No valid 'RuleSet' element found on {target_url}"
        else:
            for r in rulesets:
                languageset = r["LanguageSwitcherPlaceholder"]
                if languageset is None:
                    continue
                if "en" in languageset.values():
                    ids.append(("en", r["Id"]))
                elif "en-GB" in languageset.values():
                    ids.append(("en-gb", r["Id"]))
                elif "en-US" in languageset.values():
                    ids.append(("en-us", r["Id"]))
                elif "de" in languageset.values():
                    ids.append(("de", r["Id"]))
                else:
                    logger.warning("ONETRUST: VARIANT A: Ruleset did not have a recognized language, defaulting to english. (browser_id: %s)", browser.crawl.browser_id)
                    ids.append(("en", r["Id"]))

        if len(ids) == 0:
            logger.error(f"ONETRUST: VARIANT A: No valid language ruleset found on {target_url}")
            return [], CrawlState.PARSE_ERROR, f"ONETRUST: VARIANT A: No valid language ruleset found on {target_url}"

        return ids, CrawlState.SUCCESS, f"ONETRUST: Found {len(ids)} ruleset ids"
    except (AttributeError, KeyError) as kex:
        logger.error(f"ONETRUST: VARIANT A: Key Error on {target_url} -- Details: {kex}")
        return [], CrawlState.PARSE_ERROR, f"ONETRUST: VARIANT A: Key Error on {target_url} -- Details: {kex}"


def _variantA_get_and_parse_json(domain_url: str, dd_id: str, ruleset_ids: List[Tuple[str, str]], webdriver: CBConsentCrawlerBrowser, visit: SiteVisit) -> Tuple[int, CrawlState, str]:
    """
    Retrieve and parse the json files from the domain URL storing the cookie categories.
    The raw cookie data will be stored internally and can later be persisted to disk.
    @param domain_url: Domain on which to access the consent data json
    @param dd_id: Data domain ID, previously extracted before retrieving the ruleset ids.
    @param ruleset_ids: List of ids extracted from the ruleset json.
    @return: number of cookies extracted, crawl state, report
    """
    browser = webdriver.crawl
    assert browser

    browser_id = browser.browser_id

    cookie_count = 0
    
    logger.info("RULESET IDS: %s", ruleset_ids)
    for lang, i in ruleset_ids:
        curr_ruleset_url = f"{domain_url}/consent/{dd_id}/{i}/{lang}.json"
        # cc_json, state, report = simple_get_request(curr_ruleset_url)
        state, cc_json = webdriver.get_content(curr_ruleset_url)

        if state != PageState.OK:
            logger.error("ONETRUST: VARIANT A: Failed to retrieve ruleset at: %s, (browser_id: %s)", curr_ruleset_url, browser.browser_id)
            # c_logmsg(f"ONETRUST: VARIANT A: Details: {state} -- {report}", browser.browser_id, logging.DEBUG)
            logger.error("ONETRUST: VARIANT A: Details: %s -- %s (browser_id: %s)", state, report, browser.browser_id)
            continue

        try:
            json_data = json.loads(cc_json)

            if "DomainData" not in json_data:
                logger.warning("ONETRUST: VARIANT A: Could not find \"DomainData\" attribute inside decoded JSON. (browser_id: %s)", browser.browser_id)
                continue
            json_body = json_data["DomainData"]

            ## Language Detection
            if "Language" not in json_body:
                logger.warning("ONETRUST: VARIANT A: Could not find \"Language\" attribute inside decoded JSON. (browser_id: %s)", browser.browser_id)
                continue
            elif "Culture" not in json_body["Language"]:
                logger.warning("ONETRUST: VARIANT A: Could not find \"Culture\" attribute inside decoded JSON. (browser_id: %s)", browser.browser_id)
                continue
            elif any(lstring in json_body["Language"]["Culture"] for lstring in ["en", "en-GB", "en-US"]):
                cat_lookup = category_lookup_en
            elif "de" in json_body["Language"]["Culture"]:
                cat_lookup = category_lookup_de
            else:
                # c_logmsg(f"ONETRUST: VARIANT A: Unrecognized language in ruleset: {json_body['Language']['Culture']}", browser.browser_id, logging.WARN)
                # c_logmsg(f"ONETRUST: VARIANT A: Trying english anyways...", browser_id, logging.WARN)
                cat_lookup = category_lookup_en

            ## Cookie Data extraction
            if "Groups" not in json_data["DomainData"]:
                logger.warning("ONETRUST: VARIANT A: Could not find \"Groups\" attribute inside decoded JSON. (browser_id: %s)", browser.browser_id)
                continue

            group_list = json_data["DomainData"]["Groups"]
            for g_contents in group_list:
                if "GroupName" not in g_contents:
                    logger.warning("ONETRUST: VARIANT A: Could not find Category Name for group inside decoded JSON. (browser_id: %s)", browser.browser_id)
                    continue
                cat_name = g_contents["GroupName"]
                cat_id = cat_lookup(browser, cat_name)

                if "FirstPartyCookies" in g_contents:
                    firstp_cookies = g_contents["FirstPartyCookies"]
                    for c in firstp_cookies:
                        purpose = c["description"] if "description" in c else None
                        expiry = c["Length"] if "Length" in c else None
                        if "IsSession" in c:
                            expiry = "session" if c["IsSession"] else expiry

                        # Store to the database
                        store_consent_data(name=c["Name"], domain=c["Host"], cat_id=cat_id, cat_name=cat_name, browser=browser, visit=visit, purpose=purpose, expiry=expiry, type_name=None, type_id=None)

                        cookie_count += 1
                else:
                    logger.warning("ONETRUST: VARIANT A: No First Party Cookies inside group for decoded JSON.")

                if "Hosts" in g_contents:
                    thirdp_cookies = g_contents["Hosts"]
                    for host_dat in thirdp_cookies:
                        if "Cookies" not in host_dat:
                            continue
                        for c in host_dat["Cookies"]:
                            purpose = c["description"] if "description" in c else None
                            expiry = c["Length"] if "Length" in c else None
                            if "IsSession" in c:
                                expiry = "session" if c["IsSession"] else expiry

                            # Store to the database
                            store_consent_data(name=c["Name"], domain=c["Host"], cat_id=cat_id, cat_name=cat_name, browser=browser, visit=visit, purpose=purpose, expiry=expiry, type_name=None, type_id=None)
                            cookie_count += 1
                else:
                    pass
                    logger.warning("ONETRUST: VARIANT A: No Third Party Cookies inside group for decoded JSON.")
        except (AttributeError, KeyError) as ex:
            logger.error("ONETRUST: VARIANT A: Could not retrieve an expected attribute from json. (browser_id %s)", browser_id)
            pass
            # c_logmsg(f"ONETRUST: VARIANT A: Could not retrieve an expected attribute from json.", browser_id, logging.ERROR)
            # c_logmsg(f"ONETRUST: VARIANT A: Details: {type(ex)} -- {ex}", browser_id, logging.ERROR)
        except json.JSONDecodeError as ex:
            logger.error("ONETRUST: VARIANT A: Failed to decode json file for ruleset : %s (browser_id %s)", curr_ruleset_url, browser_id)
            # c_logmsg(f"ONETRUST: VARIANT A: Details: {type(ex)} -- {ex}", browser_id, logging.ERROR)
            continue

        # stop after first successful ruleset
        if cookie_count > 0:
            break

    if cookie_count == 0:
        return 0, CrawlState.NO_COOKIES, f"ONETRUST: VARIANT A: Could not extract any cookies for ddid: {dd_id}."
    else:
        return cookie_count, CrawlState.SUCCESS, f"ONETRUST: VARIANT A: Cookies Extracted: {cookie_count}"


def _variantB_try_retrieve_jsurl(driver: WebDriver, browser_id: int, timeout: int = 5) -> Optional[str]:
    """
    Find OneTrust javascript URL inside the HTML of the current webdriver page.
    @param driver: Selenium webdriver currently active
    @param browser_id: browser that performs the action
    @param timeout: Time to wait in seconds.
    @return URL pattern, or None if none found.
    """
    try:
        wait = WebDriverWait(driver, timeout)
        js_url = wait.until(_exists_script_tag_with_jsurl(browser_id))
        return js_url
    except TimeoutException:
        logger.info("ONETRUST: VARIANT B: Timeout on trying to retrieve javascript link. (browser_id: %s)", browser_id)
        return None


def _variantB_parse_script_for_object(script_url: str, webdriver: CBConsentCrawlerBrowser) -> Tuple[Optional[Dict[str, Any]], CrawlState, str]:
    """
    Use the requests library to retrieve the OneTrust Javascript document containing
    the cookie consent categories, and transform it into a dictionary.
    @param script_url: URL to retrieve the javascript file from
    @param browser: process that performs the action
    @return: Tuple:
        data_dict: Dictionary of JSON data from which the cookie categories can be retrieved.
        state: Result status
        msg: Potential Error Report
    """
    state, content = webdriver.get_content(script_url)

    if state != PageState.OK:
        return None, CrawlState.LIBRARY_ERROR, f"Unable to fetch {script_url} due to PageState of {state}"

    onetrust_script: str = content.strip()

    # purge newlines
    onetrust_script = re.sub('\n', ' ', onetrust_script)

    # Find the start of the group array
    matchobj = re.search(",\\s*Groups:\\s*\\[", onetrust_script)
    try:
        if matchobj:
            startpoint = matchobj.start(0)

            # Get the end of the group array
            i = matchobj.end(0)
            open_brackets = 1
            in_quotes = False
            while i < len(onetrust_script) and open_brackets > 0:
                if onetrust_script[i] == '"':
                    in_quotes = not in_quotes
                if not in_quotes:
                    if onetrust_script[i] == "[":
                        open_brackets += 1
                    elif onetrust_script[i] == "]":
                        open_brackets -= 1
                i += 1
            group_string = onetrust_script[startpoint + 1:i]

            # put the object into a javascript function, and evaluate it
            # This returns a dict of the cookie consent data we need.
            js_object_string = "function $() {return {" + group_string + "}};"
            # TODO: parse js_object_string as something with variant B is found
            logger.error(f"Please report this to the developer. Group string: {group_string}")
            # data_dict = js2py.eval_js(js_object_string)()

            # return data_dict, CrawlState.SUCCESS, "ONETRUST: VARIANT B: Successfully extracted objects from javascript"
            return None, CrawlState.LIBRARY_ERROR, "ONETRUST: VARIANT B is not supported right now. Please report to the developer"
        else:
            return None, CrawlState.PARSE_ERROR, "ONETRUST: VARIANT B: Failed to find desired javascript object in Onetrust consent script."
    except Exception as ex:
        return None, CrawlState.UNKNOWN, f"ONETRUST: VARIANT B: Unexpected error while parsing OneTrust javascript: : {type(ex)} {ex}"


def _variantB_extract_cookies_from_dict(consent_data: Dict[str, Any], browser: Crawl, visit: SiteVisit) -> Tuple[int, CrawlState, str]:
    """
    Using the dictionary from the previous step, extract the useful data contained within.
    @param consent_data: Cookie data dictionary retrieved from previous step.
    @param browser_id: process that performs the action
    @return: number of cookies extracted, crawl state, report
    """
    cookie_count = 0
    try:
        # If we arrive here, "Groups" must be in the dictionary
        g_data = consent_data["Groups"]
        for g_contents in g_data:

            # Try to retrieve the category name, and transform it to the internally defined categories
            try:
                if "Parent" not in g_contents or g_contents["Parent"] is None:
                    langproplist = g_contents["GroupLanguagePropertiesSets"]
                else:
                    langproplist = g_contents["Parent"]["GroupLanguagePropertiesSets"]

                if len(langproplist) > 0:
                    cat_name = langproplist[0]["GroupName"]["Text"]
                    cat_id = category_lookup_en(browser, cat_name)
                    if cat_id == CookieCategory.UNRECOGNIZED:
                        cat_id = category_lookup_de(browser, cat_name)

                else:
                    raise AttributeError("Empty Group")
            except (AttributeError, KeyError):
                cat_name = "undefined"
                cat_id = CookieCategory.UNRECOGNIZED
                logger.warning("ONETRUST: Unable to find category name. Attempting cookie retrieval anyways... (browser_id: %s)", browser.browser_id)

            for cookie_dat in g_contents["Cookies"]:
                cname = cookie_dat["Name"]  # not null
                chost = cookie_dat["Host"]  # not null
                cdesc = cookie_dat["description"] if "description" in cookie_dat else None
                cexpiry = cookie_dat["Length"] if "Length" in cookie_dat else None
                if "IsSession" in cookie_dat:
                    cexpiry = "session" if cookie_dat["IsSession"] else cexpiry

                store_consent_data(name=cname, domain=chost, cat_id=cat_id, cat_name=cat_name, browser=browser, visit=visit, purpose=cdesc, expiry=cexpiry, type_name=None, type_id=None)
                cookie_count += 1

    except (AttributeError, KeyError) as ex:
        logger.error(f"ONETRUST: VARIANT B: Could not retrieve an expected attribute from consent data dict. -- {type(ex)} - {ex}")
        return 0, CrawlState.PARSE_ERROR, f"ONETRUST: VARIANT B: Could not retrieve an expected attribute from consent data dict. -- {type(ex)} - {ex}"
    if cookie_count == 0:
        logger.warning("ONETRUST: VARIANT B: Consent Platform Script contained zero cookies!")
        return 0, CrawlState.NO_COOKIES, "ONETRUST: VARIANT B: Consent Platform Script contained zero cookies!"
    else:
        logger.info(f"ONETRUST: VARIANT B: Successfully retrieved {cookie_count} cookies.")
        return cookie_count, CrawlState.SUCCESS, f"ONETRUST: VARIANT B: Successfully retrieved {cookie_count} cookies."

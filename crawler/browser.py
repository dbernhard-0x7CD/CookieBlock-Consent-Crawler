from __future__ import annotations

import json
from abc import ABC, abstractmethod
import time

import logging
import shutil
import tempfile
from pathlib import Path
from types import TracebackType
from typing import (
    Optional,
    NamedTuple,
    Any,
    List,
    TypeVar,
    Callable,
    cast,
    Generator,
    Dict,
)
from urllib.parse import urldefrag

from bs4 import BeautifulSoup
from hyperlink import URL, URLParseError
from numpy import random
import undetected_chromedriver as uc

from selenium.common.exceptions import (
    NoAlertPresentException,
    TimeoutException,
    WebDriverException,
    JavascriptException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.expected_conditions import staleness_of
from selenium.webdriver.support.wait import WebDriverWait

from selenium_stealth import stealth

from seleniumwire import webdriver
from seleniumwire.request import Request, Response
from seleniumwire.utils import decode

from crawler.database import store_result, Crawl, SiteVisit
from crawler.enums import PageState, CookieTuple, CrawlerType, CrawlState
from crawler.utils import logger

from crawler.cmps.cookiebot import check_cookiebot_presence, internal_cookiebot_scrape
from crawler.cmps.termly import check_termly_presence
from crawler.cmps.onetrust import check_onetrust_presence

FuncT = TypeVar("FuncT", bound=Callable[..., Any])

# Presence check before full crawl process
presence_check_methods = {
    CrawlerType.COOKIEBOT: check_cookiebot_presence,
    CrawlerType.ONETRUST: check_onetrust_presence,
    CrawlerType.TERMLY: check_termly_presence,
}

# All supported crawl methods
crawl_methods: Dict = {
    CrawlerType.COOKIEBOT: internal_cookiebot_scrape,
    # CrawlerType.ONETRUST: internal_onetrust_scrape,
    # CrawlerType.TERMLY: internal_termly_scrape,
}


def post_load_routine(func: FuncT, browser_init: Optional["Browser"] = None) -> FuncT:
    """
    Collects cookies and dismisses alert windows after the decorated function is run
    """

    def func_wrapper(*args: Any, **kwargs: Any) -> None:
        ret = func(*args, **kwargs)

        # check if self (Browser) is the first argument
        browser = args[0]
        if not isinstance(browser, Browser):
            if browser_init:
                browser = browser_init
            else:
                logger.error("Browser not provided to the post_routine decorator")
                return ret

        logger.debug("executing post function routine")
        browser.dismiss_dialogs()
        return ret

    return cast(FuncT, func_wrapper)


class Browser(ABC):
    driver: uc.Chrome | webdriver.Firefox

    """
    Abstracts driver for Selenium
    """

    def __init__(
        self,
        timeout: float,
        seconds_before_processing_page: float,
        proxy: Optional[str] = None,
    ) -> None:
        """This implements an abstract basic browser which provides some common settings (screenshots, proxy, timeout and the seconds_before_processing_page).
        And also dumping logic, link collecion and other shared logic.

        Args:
            timeout (float): Timeout for a page to load.
            seconds_before_processing_page (float): Seconds to wait before determining the status of a page.
            proxy (Optional[str], optional): Proxy url. Defaults to None.
        """

        # Contains a dictionary from all URLs of loaded resources to their state
        self._load_status: dict[str, PageState] = {}

        self.last_loaded_url: Optional[URL] = None

        self.proxy = proxy
        self.timeout = timeout
        self.seconds_before_processing_page = seconds_before_processing_page

        # Needs to also have enfbots_ prefix as this is also deleted
        # via 'run-crawler.sh' in case the crawler crashes.
        self.temp_download_directory = tempfile.TemporaryDirectory(
            prefix="enfbots_download_dir_"
        )

    @property
    @abstractmethod
    def requests(self) -> list[Any]: ...

    @abstractmethod
    def __enter__(self) -> Browser: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        logger.info("Exiting selenium driver")
        # noinspection PyBroadException
        try:
            self._press_key(Keys.ESCAPE)
            logger.info(
                "During the crawl %s HTTP requests were performed.", len(self.requests)
            )
            self.driver.quit()
        except Exception as e:
            # some errors (like KeyboardInterrupt) might crash Selenium and we cannot quit it like this
            logger.warning("Driver failed to quit gracefully, due to %s", e)

    def dismiss_dialogs(self) -> None:
        # try to dismiss alert windows
        try:
            while True:
                self.driver.switch_to.alert.dismiss()
                logger.debug("Dismissed alert")
                time.sleep(0.5)
        except (NoAlertPresentException, TimeoutException):
            pass

    @property
    def user_agent(self) -> str:
        agent = self.execute_script("return navigator.userAgent;")
        if agent:
            return agent
        logging.warning("Browser returned no user agent!")
        return "Mozilla/5.0 (X11; Linux x86_64; rv:94.0) Gecko/20100101 Firefox/94.0"

    @property
    def current_url(self) -> URL:
        try:
            # Even though the type from driver.current_url says it can never be None
            # it sometimes is and we have to handle this case here
            return URL.from_text(self.driver.current_url)
        except TypeError:
            return URL.from_text("")

    @post_load_routine
    def load_page(self, url: URL, timeout: Optional[float] = None) -> PageState:
        timeout = self.seconds_before_processing_page if timeout is None else timeout

        # this function is NOT thread safe
        # selenium wire will add a trailing slash, so we add it before to be able to match requests later
        # adds a trailing slash if it does not exist

        if not url.path:
            url = url.child("")

        logger.debug("Loading page %s", url)
        self.last_loaded_url = url
        error = None
        try:
            str_url = str(url)
            logger.info("Calling driver.get %s", str_url)
            self.driver.get(str_url)
        except TimeoutException:
            logging.warning("Timeout on: %s", url)
            return PageState.TIMEOUT
        except WebDriverException as e:
            if "net::ERR_NAME_NOT_RESOLVED" in str(e.msg):
                return PageState.DNS_ERROR
            # other exceptions in Chrome.
            # actual error is in load status
            error = e

        # this may add up to 40 s to the whole crawl execution
        if timeout == 0:
            timeout = 0.8 + random.random() * 1.2
        logger.info("Waiting for %0.2f seconds", timeout)
        time.sleep(timeout)

        if str_url in self._load_status:
            logger.info("load status is: %s", self._load_status[str_url])
            return self._load_status[str_url]
        else:
            logger.info("NO load status found for %s", str_url)

        try:
            logger.info("Pressing escape")
            self._press_key(Keys.ESCAPE)
        except (
            UnexpectedAlertPresentException,
            WebDriverException,
            TimeoutException,
        ) as e:
            logger.exception(e)
            # Also continue

        if str(url) in self._load_status:
            tries = 3
            while tries > 0:
                try:
                    status = self._load_status[str(url)]
                    if status == PageState.REDIRECT:
                        return self._load_status[str(self.last_loaded_url)]
                    return status
                except KeyError:
                    pass
                tries -= 1
            # End of tries
            logger.error("No status for URL: %s", url)
            return PageState.UNKNOWN_ERROR
        if error:
            # an error on get without extra
            logger.error("Unknown error occurred on page load.", exc_info=error)
            return PageState.UNKNOWN_ERROR

        # this indicates the list was probably an anchor and no reload happened (e.g. SPA)
        return PageState.OK

    def full_dump(self, output_dir: Path, name: str) -> None:
        path = output_dir / "dump_urls.txt"
        with path.open("a") as file:
            print(f"{name}: {self.current_url.to_text()}", file=file)

        self.dump_html(output_dir, name)

    def dump_html(self, output_dir: Path, name: str) -> Path:
        """
        Save page as html. Returns the filename of the stored file.
        :param output_dir: storage path
        :param name: file name
        """
        current_url = self.current_url
        logger.info("Dumping HTML: %s on page: %s", name, current_url)

        if str(current_url).strip().lower().endswith(".pdf"):
            logger.error("Dumping html on pdf page %s", current_url)

        content = self.get_html()
        file_name = output_dir / f"{name}.html"

        # Write out
        output_dir.mkdir(parents=True, exist_ok=True)
        with file_name.open("w") as out_file:
            out_file.write(content)
        return file_name

    def get_html(self) -> str:
        return self.driver.page_source

    def get_soup(self) -> BeautifulSoup:
        """
        Parse the content of the web page
        :return: BeautifulSoup of the page content
        """
        soup = BeautifulSoup(
            self.driver.page_source, "html.parser", multi_valued_attributes=None
        )
        for nos in soup.find_all("noscript"):
            nos.decompose()
        return soup

    def get_links(self) -> list[LinkTuple]:
        """
        For currently loaded page, locate all links and their accompanying text
        """

        # Find all href tags
        # JavaScript efficient implementation
        get_links_js = (Path(__file__).parent / "js/get_links.js").read_text()

        links = self.execute_script(get_links_js)

        if links is None:
            return []

        current_url = self.current_url

        results = []
        for item in links:
            if item is None:
                continue

            if "href" in item:
                if item["href"]:
                    href = item["href"].strip()
                    try:
                        url = URL.from_text(urldefrag(href).url)
                    except URLParseError:
                        logger.warning("Badly formatted url encountered %s", href)
                        continue
                    except ValueError:
                        logger.warning(
                            "Badly formatted url encountered, other ValueError %s", href
                        )
                        continue
                    # check for valid scheme (not mailto...)
                    if url.scheme not in ("http", "https", ""):
                        continue
                    if not url.absolute:
                        try:
                            url = current_url.click(url)
                        except NotImplementedError as e:
                            logger.warning(
                                "NotImplementedError when clicking: %s on link %s",
                                e,
                                url,
                            )
                            continue

                    path_only = url.replace(scheme=None, host=None, port=None).to_text()
                    test_str = [item["text"], path_only, item["alt_text"]]
                    results.append(LinkTuple(url, test_str))
            else:
                logger.error("Strange link found: %s", item)
        return results

    @post_load_routine
    def activate(self, el: WebElement) -> None:
        """
        Using this function, one is also able to click on partially hidden html elements, which would otherwise cause
        an exception when trying to simulate a click on it :param el: element to make active
        """
        self.driver.execute_script("arguments[0].click();", el)

    @post_load_routine
    def execute_script(
        self, script: str, *args: Any, raise_exception: bool = False
    ) -> Any:
        """
        Wrapper of browser execute_script. Raises JavascriptException
        """
        logger.debug("Executing JS code in selenium: %s", script)
        try:
            res = self.driver.execute_script(script, *args)
            # logger.info("> Result was: %s", res)
            return res
        except JavascriptException as e:
            if raise_exception:
                raise e
            else:
                logger.exception("JavaScript exception encountered")

    @post_load_routine
    def _press_key(self, key: Any) -> None:
        """
        Operate the browser via keyboard.
        :param key: keyboard keys to press
        """
        actions = ActionChains(self.driver)
        actions.send_keys(key)
        actions.perform()


class CBConsentCrawlerBrowser(Browser):
    def __init__(
        self,
        seconds_before_processing_page: float,
        crawl: Optional[Crawl],
        proxy: Optional[str] = None,
    ) -> None:
        super().__init__(
            timeout=7,
            seconds_before_processing_page=seconds_before_processing_page,
            proxy=proxy,
        )

        self.cookie_tracker: set[CookieTuple] = set()
        self.crawl = crawl

    def load_page(self, url: URL, timeout: Optional[float] = None) -> PageState:
        ps = super().load_page(url, timeout)

        return ps

    def check_cmps(self, visit: SiteVisit) -> None:
        logger.info("checking for CMPs")

        results: Dict[CrawlerType, Any] = dict()

        for t, y in presence_check_methods.items():
            x = y(self.driver)
            logger.info("%s: %s", t.name, x)
            results[t] = x

        for t, found in results.items():
            if found:
                logger.info("Crawling for %s", t.name)
                crawl_state, message = crawl_methods[t](
                    str(self.current_url), browser_id=visit.browser_id, visit_id=visit.visit_id, webdriver=self.driver
                )

                logger.info("\tResult %s, %s", crawl_state, message)

                store_result(
                    browser=self.crawl, cmp_type=t, report=message, visit=visit, crawlState=crawl_state
                )
                break  # original crawler only crawls first one
        store_result(browser=self.crawl, visit=visit, report="No known Consent Management Platform found on the given URL.", cmp_type=CrawlerType.FAILED, crawlState=CrawlState.CMP_NOT_FOUND)



    def collect_cookies(self) -> None:
        cookies = self.driver.get_cookies()

        if cookies is None:
            logger.info("NO cookies")
            return

        for cookie in cookies:
            my_cookie = CookieTuple(
                cookie.get("name"),
                cookie.get("value"),
                cookie.get("path"),
                cookie.get("domain"),
                cookie.get("secure"),
                cookie.get("httpOnly"),
                cookie.get("expiry"),
                cookie.get("sameSite"),
            )
            logger.info("Cookie: %s", cookie)
            # the tuple is  hashable nicely so the set should work
            if my_cookie not in self.cookie_tracker:
                self.cookie_tracker.add(my_cookie)


class Chrome(CBConsentCrawlerBrowser):
    def __init__(
        self,
        seconds_before_processing_page: float,
        chrome_path: Path,
        chromedriver_path: Path,
        chrome_profile_path: Path,
        use_temp: bool = True,
        intercept_network: bool = True,
        headless: bool = True,
        crawl: Optional[Crawl] = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Creates a CBConsentCrawlerBrwoser using Chrome via the webdriver.

        Args:
            use_temp (bool, optional): If a temporary directory should be used for the profile data which will be altered. Defaults to True.
        """
        super().__init__(
            seconds_before_processing_page=seconds_before_processing_page, crawl=crawl
        )
        self._requests: list[Any] = []
        self.use_temp = use_temp

        # By default we use a temporary directory to always have a fresh chrome profile
        if self.use_temp:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="enfbots_")
            self.profile_path = Path(self._temp_dir.name) / "chrome_profile"

            # copy profile to the temporary directory
            shutil.copytree(
                chrome_profile_path,
                self.profile_path,
                ignore_dangling_symlinks=True,
            )
        else:
            self.profile_path = chrome_profile_path
        self.headless = headless

        self.chrome_path = chrome_path
        self.driver_path = chromedriver_path

    @property
    def requests(self) -> list[Any]:
        return self._requests

    def __enter__(self) -> Chrome:
        options = uc.ChromeOptions()

        # required in docker (no X11 context)
        # More info on flags: https://peter.sh/experiments/chromium-command-line-switches/
        options.add_argument("--no-sandbox")

        # if len(self.languages) > 0:
        #     options.add_argument("--accept-lang=" + ",".join(self.languages))
        #     options.add_argument("--lang=" + ",".join(self.languages))
        #     logging.info("Preferring languages: %s", ",".join(self.languages))

        # just some options passing in to skip annoying popups
        options.add_argument("--no-first-run")
        options.add_argument("--no-service-autorun")
        options.add_argument("--password-store=basic")
        if self.proxy:
            options.add_argument(f"--proxy-server={self.proxy}")

        # if Config().CHROME_DISABLE_SHM:
        #     options.add_argument("--disable-dev-shm-usage")

        prefs = {
            "download_restrictions": 0,  # Allows the browser to download all content; https://chromeenterprise.google/policies/?policy=DownloadRestrictions
            "download.default_directory": self.temp_download_directory.name,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "profile.default_content_settings.popups": False,
            "plugins.always_open_pdf_externally": True,
        }
        options.add_experimental_option("prefs", prefs)

        # For debugging
        # options.add_argument("--enable-logging")
        # options.add_argument("--v=1")

        options.headless = self.headless

        options.page_load_strategy = "normal"

        logger.info(
            "Instantiating chrome %s using %s with profile_path %s",
            self.chrome_path,
            self.driver_path,
            self.profile_path,
        )

        self.driver = uc.Chrome(
            options=options,
            driver_executable_path=str(self.driver_path),
            browser_executable_path=str(self.chrome_path / "chrome"),
            version_main=122,
            headless=self.headless,
            user_data_dir=str(self.profile_path),
            enable_cdp_events=True,
        )

        stealth(
            self.driver,
            languages=["en-US", "en"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )

        # Time to wait when calling driver.get
        # self.driver.set_page_load_timeout(Config().PAGE_TIMEOUT_SEC)

        self.driver.add_cdp_listener(
            "Network.responseReceived", self._handle_cdp_response_received
        )

        return self

    def _handle_cdp_response_received(self, data: Any) -> None:
        """
        This function is analogous to _process_intercepted_request but is directly called
        from undetected-chromedriver instead of using node to communicate with the CDP endpoint.
        The javascript is still used for captcha solving.

        This handles the response and adds usefull information to self._load_status.
        """
        if data["method"] == "Network.responseReceived":
            url = data["params"]["response"]["url"]
            http_status = data["params"]["response"]["status"]

            if http_status == 404:
                self._load_status[url] = (
                    PageState.HTTP_ERROR
                )  # TODO : add pagestate HTTP_404?
            if 200 <= http_status < 300:
                self._load_status[url] = PageState.OK

    def _process_intercepted_request(self, data: Any) -> None:
        self.requests.append(data)
        url = URL.from_text(data["request"]["url"])
        response = data["response"]
        if url == self.last_loaded_url:
            error = response.get("error_reason")
            if error:
                logger.warning(
                    "Load of page %s was interrupted with status %s.", url, error
                )
                # Failed, Aborted, TimedOut, AccessDenied, ConnectionClosed, ConnectionReset,
                # ConnectionRefused, ConnectionAborted, ConnectionFailed, NameNotResolved,
                # InternetDisconnected, AddressUnreachable, BlockedByClient, BlockedByResponse
                if error == "NameNotResolved":
                    self._load_status[str(url)] = PageState.DNS_ERROR
                elif error == "TimedOut":
                    self._load_status[str(url)] = PageState.TIMEOUT
                else:
                    self._load_status[str(url)] = PageState.TCP_ERROR
            else:
                headers = response["headers"]
                status_code = response["http_status"]
                location = headers.get("Location")
                content_type = headers.get("Content-Type")
                if 300 <= status_code < 400 and location:
                    loc = URL.from_text(location)
                    if not loc.absolute:
                        loc = url.click(loc)
                    self.last_loaded_url = loc
                    self._load_status[str(url)] = PageState.REDIRECT
                elif status_code >= 400:
                    logger.error(
                        "On %s: HTTP status_code %s and response: %s",
                        url,
                        status_code,
                        response,
                    )
                    logger.error("Data: %s", data)
                    self._load_status[str(url)] = PageState.HTTP_ERROR
                elif not is_content_type_accepted(content_type):
                    # actually block done by download_restriction parameter
                    self._load_status[str(url)] = PageState.BAD_CONTENT_TYPE
                else:
                    self._load_status[str(url)] = PageState.OK
                    logger.debug(
                        "Fetched the page %s with status %s and content type %s",
                        url,
                        status_code,
                        content_type,
                    )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:

        super().__exit__(exc_type, exc_val, exc_tb)

        # noinspection PyBroadException
        try:
            # Chrome might still be writing into it after quit(). Give it some time
            time.sleep(0.2)

            if self.use_temp:
                self._temp_dir.cleanup()
        except Exception:
            logger.warning("Unable to remove the temporary directory", stack_info=False)

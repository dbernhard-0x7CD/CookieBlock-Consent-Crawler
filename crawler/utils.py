import logging
import re
import requests
import requests.exceptions
from typing import Tuple, Optional, Dict, Any

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException

from crawler.enums import CrawlState

logger = logging.getLogger("cookieblock-consent-crawler")

# unique identifier pattern
uuid_pattern = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# user agent string for requests call
chrome_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.77 Safari/537.36"


def set_log_formatter(fmt: str, date_format: str) -> None:
    """
    Sets the given format for the root logger and all its handlers.
    The handlers may be to a file or to console. It also ensures that
    at least one console handler exists.
    """
    logger = logging.getLogger()
    log_formatter = logging.Formatter(
        fmt=fmt,
        datefmt=date_format,
    )
    # Set the log_formatter from above for all and ensure
    # that at lest one handler is present
    for handler in logger.handlers:
        handler.setFormatter(log_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.INFO)
    logger.addHandler(console_handler)


set_log_formatter(
    "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d:%H:%M:%S"
)
logger.setLevel(logging.INFO)


def simple_get_request(
    url: str,
    browser_id: int,
    timeout: Tuple[int, int] = (6, 30),
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[requests.Response], CrawlState, str]:
    """
    Performs a simple GET request using the requests library.
    This is exclusively used for URLs that are known to point to a single file,
    such as a json or javascript document. As such, this is a much faster way
    to browse to the URL than using Selenium.
    @param url: URL to send a GET request for
    @param browser_id: process id that executes the request
    @param timeout: tuple of timeouts, connection timeout and load timeout respectively
    @param headers: Header arguments for the get request, as a dictionary
    @return: Tuple:
        1. Response from requests library
        2. crawlstate result
        3. potential erro message
    """
    try:
        # add chrome header in case bot detection is present
        if headers is None:
            extended_headers = {"User-Agent": chrome_user_agent}
        else:
            extended_headers = headers.copy()
            extended_headers["User-Agent"] = chrome_user_agent

        # perform fast get request for simple applications
        r = requests.get(url, timeout=timeout, verify=True, headers=extended_headers)

        if r.status_code >= 400:
            return None, CrawlState.HTTP_ERROR, f"Error Code: {r.status_code}"
        else:
            return r, CrawlState.SUCCESS, str(r.status_code)
    except requests.exceptions.HTTPError as ex:
        logger.error(
            f'BROWSER {browser_id}: HTTP Error Exception for URL "{url}". Details: {ex}'
        )
        return None, CrawlState.HTTP_ERROR, f"Selenium HTTP Error encountered: {ex}"
    except requests.exceptions.SSLError as ex:
        logger.error(
            f"BROWSER {browser_id}: SSL Certificate issue encountered when connecting to {url}. -- Details: {ex}"
        )
        return None, CrawlState.SSL_ERROR, f"Encountered an SSL error: {ex}"
    except (requests.exceptions.URLRequired, requests.exceptions.MissingSchema) as ex:
        logger.error(
            f'BROWSER {browser_id}: Possibly malformed URL: "{url}" -- Details: "{ex}"'
        )
        return None, CrawlState.MALFORMED_URL, f"Malformed URL for get request: {url}"
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.ProxyError,
        requests.exceptions.TooManyRedirects,
        requests.exceptions.Timeout,
    ) as ex:
        logger.error(
            f'BROWSER {browser_id}: Connection to "{url}" failed. -- Details: {ex}'
        )
        return None, CrawlState.CONN_FAILED, f"Connection to host failed. Details: {ex}"
    except Exception as ex:
        logger.error(f"BROWSER {browser_id}: Unexpected Error on {url}: {ex}")
        logger.debug(traceback.format_exc())
        return (
            None,
            CrawlState.UNKNOWN,
            f"An unexpected error occurred when accessing {url}: {ex}",
        )


def execute_in_IFrames(
    command, driver: WebDriver, browser_id: int, timeout: int
) -> Optional[Any]:
    """
    Execute the provided command in each iFrame.
    @param command: command to execute, as an executable class
    @param driver: webdriver that performs the browsing
    @param browser_id: identifier for the browser
    @param timeout: how long to wait for the result until timeout
    @return: None if not found, Any if found
    """
    result = command(driver, browser_id, timeout)
    if result:
        return result
    else:
        driver.switch_to.default_content()
        iframes = driver.find_elements_by_tag_name("iframe")

        for iframe in iframes:
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(iframe)
                result = command(driver, browser_id, timeout=0)
                if result:
                    driver.switch_to.default_content()
                    return result
            except StaleElementReferenceException:
                c_logmsg(
                    "Iframe turned stale, trying next one", browser_id, logging.WARN
                )
                continue

        # If we get here, search also fails in iframes
        driver.switch_to.default_content()
        return None

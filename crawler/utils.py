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

import re

from selenium.webdriver.remote.webdriver import WebDriver

from crawler.enums import CookieCategory

# url for the cookiebot consent CDN
cb_base_url = "https://consent\\.cookiebot\\.(com|eu)/"

# regex patterns for cookiebot urls
cb_base_pat = re.compile(cb_base_url)
cbid_variant2_pat = re.compile(cb_base_url + "([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/cc\\.js")
cbid_variant3_pat = re.compile("[&?]cbid=([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def check_cookiebot_presence(webdriver: WebDriver) -> bool:
    """ Check whether Cookiebot is referenced on the website """
    psource = webdriver.page_source
    matchobj = cb_base_pat.search(psource, re.IGNORECASE)
    return matchobj is not None


import re

from selenium.webdriver.remote.webdriver import WebDriver

# Base URL patterns required for Variant A
onetrust_pattern_A = re.compile("(https://cdn-apac\\.onetrust\\.com)")
onetrust_pattern_B = re.compile("(https://cdn-ukwest\\.onetrust\\.com)")
cookielaw_base_pattern = re.compile("(https://cdn\\.cookielaw\\.org)")
cmp_cookielaw_base_pattern = re.compile("(https://cmp-cdn\\.cookielaw\\.org)")
optanon_base_pattern = re.compile("(https://optanon\\.blob\\.core\\.windows\\.net)")
cookiecdn_base_pattern = re.compile("(https://cookie-cdn\\.cookiepro\\.com)")
cookiepro_base_pattern = re.compile("(https://cookiepro\\.blob\\.core\\.windows\\.net)")

base_patterns = [onetrust_pattern_A, onetrust_pattern_B,
                 cookielaw_base_pattern, cmp_cookielaw_base_pattern, optanon_base_pattern,
                 cookiecdn_base_pattern, cookiepro_base_pattern]


def check_onetrust_presence(webdriver: WebDriver) -> bool:
    """ Check whether a OneTrust pattern is referenced on the website """
    psource = webdriver.page_source
    found = False
    ot_iters = iter(base_patterns)
    try:
        while not found:
            pattern = next(ot_iters)
            found = pattern.search(psource, re.IGNORECASE) is not None
    except StopIteration:
        found = False

    return found

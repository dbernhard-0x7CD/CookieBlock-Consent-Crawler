
import re

from selenium.webdriver.remote.webdriver import WebDriver

# url for the termly consent
termly_base = "https://app.termly.io/api/v1/snippets/websites/"
termly_url_pattern = re.compile("https://app\\.termly\\.io/")

def check_termly_presence(webdriver: WebDriver) -> bool:
    """ Check whether a Termly pattern is referenced on the website """
    psource = webdriver.page_source
    matchobj = termly_url_pattern.search(psource, re.IGNORECASE)
    return matchobj is not None

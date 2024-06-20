from enum import Enum
from typing import NamedTuple


class PageState(Enum):
    """
    Enum to represent state of index page
    """

    # When the domain name cannot be resolved
    DNS_ERROR = 15

    # Everything OK
    OK = 0
    REDIRECT = 2
    TIMEOUT = 3
    BAD_CONTENT_TYPE = 11
    HTTP_ERROR = 12
    TCP_ERROR = 13
    UNKNOWN_ERROR = 14


class CookieTuple(NamedTuple):
    name: str
    value: str
    path: str
    domain: str
    secure: bool
    http_only: bool
    expiry: int
    same_site: str


class CookieCategory(IntEnum):
    """ ICC categories """
    UNRECOGNIZED = -1  # A class that is not unclassified but which the crawler cannot identify.
    ESSENTIAL = 0      # Cookie necessary for the site to function.
    FUNCTIONAL = 1     # Functional and Preferences. Change website options etc.
    ANALYTICAL = 2     # Includes performance and statistics.
    ADVERTISING = 3    # Cookies for Advertising/Tracking/Social Media/Marketing/Personal Data Sale etc.
    UNCLASSIFIED = 4   # Cookies that have been explicitly labelled as unclassified
    SOCIAL_MEDIA = 5   # Not used for training, but still interesting to know

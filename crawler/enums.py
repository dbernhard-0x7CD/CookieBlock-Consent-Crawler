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

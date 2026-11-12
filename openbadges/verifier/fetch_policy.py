"""
Restrict which hosts the verifier may fetch resources from.

Set the ALLOWED_FETCH_HOSTS environment variable to a comma-separated list of
host names (e.g. "lms.example.org,lms2.example.org"). When it is set, every HTTP
request made by the verifier, including each hop of a redirect, must:

- use the https scheme,
- target a host that exactly matches one of the listed names (case-insensitive),
- use the default port (443).

Requests that do not match raise FetchNotAllowedError before anything is sent.
When the variable is unset or empty, all hosts are allowed (the original behavior).

This guards against server-side request forgery: badge data supplied by users
contains URLs that the verifier dereferences.
"""
import os

from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError

try:
    from urlparse import urlparse
except ImportError:
    from urllib.parse import urlparse


ALLOWED_FETCH_HOSTS_ENV = 'ALLOWED_FETCH_HOSTS'


class FetchNotAllowedError(ConnectionError):
    """The URL is not permitted by ALLOWED_FETCH_HOSTS. Nothing was sent."""


def get_allowed_hosts():
    """Return a frozenset of allowed host names, or None if all hosts are allowed."""
    value = os.environ.get(ALLOWED_FETCH_HOSTS_ENV, '')
    hosts = frozenset(h.strip().lower() for h in value.split(',') if h.strip())
    return hosts or None


def check_url_allowed(url):
    allowed = get_allowed_hosts()
    if allowed is None:
        return

    try:
        pieces = urlparse(url)
        port = pieces.port
    except ValueError:
        raise FetchNotAllowedError("Fetching URL is not allowed (unparseable URL): {}".format(url))

    if pieces.scheme != 'https':
        raise FetchNotAllowedError("Fetching URL is not allowed (scheme must be https): {}".format(url))
    if pieces.hostname not in allowed:
        raise FetchNotAllowedError("Fetching URL is not allowed (host not in {}): {}".format(
            ALLOWED_FETCH_HOSTS_ENV, url))
    if port not in (None, 443):
        raise FetchNotAllowedError("Fetching URL is not allowed (port must be 443): {}".format(url))


class FetchPolicyAdapter(HTTPAdapter):
    """Transport adapter that checks every outgoing request, including redirect hops."""

    def send(self, request, *args, **kwargs):
        check_url_allowed(request.url)
        return super(FetchPolicyAdapter, self).send(request, *args, **kwargs)


def apply_fetch_policy(session):
    """Mount the policy adapter on a requests session and return the session."""
    adapter = FetchPolicyAdapter()
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

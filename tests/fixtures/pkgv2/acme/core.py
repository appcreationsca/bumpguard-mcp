"""acme v2 implementation.

Changes vs v1:
- make_client gained a required keyword-only param `proxy` (potentially breaking)
- deprecated_helper was removed (breaking)
- Client.fetch dropped its `verify` parameter (breaking for callers passing it)
- Client.old_method was removed (breaking)
- renamed_thing was added (info)
"""


def make_client(url, timeout=10, *, proxy):
    return Client(url)


def renamed_thing(x):
    return x * 2


class Client:
    def __init__(self, url, retries=3):
        self.url = url
        self.retries = retries

    def fetch(self, path):
        return path

    def _private(self):
        return "ignored"

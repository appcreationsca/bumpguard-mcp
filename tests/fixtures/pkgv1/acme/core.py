"""acme v1 implementation."""


def make_client(url, timeout=10):
    return Client(url)


def deprecated_helper(x):
    return x * 2


class Client:
    def __init__(self, url, retries=3):
        self.url = url
        self.retries = retries

    def fetch(self, path, verify=True):
        return (path, verify)

    def old_method(self):
        return "gone in v2"

    def _private(self):
        return "ignored"

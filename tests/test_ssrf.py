import pytest

from src import tools


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/health",
    "http://localhost:5678/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/admin",
    "file:///etc/passwd",
    "ftp://example.com/x",
])
def test_internal_and_non_http_urls_are_refused(url):
    with pytest.raises(tools.UnsafeURL):
        tools.assert_public_url(url)


def test_redirect_to_internal_address_is_refused(monkeypatch):
    class Resp:
        is_redirect = True
        headers = {"Location": "http://127.0.0.1:8000/"}

    def only_public(u):
        if "127.0.0.1" in u:
            raise tools.UnsafeURL(u)

    monkeypatch.setattr(tools, "assert_public_url", only_public)
    monkeypatch.setattr(tools.requests, "get", lambda *a, **k: Resp())
    with pytest.raises(tools.UnsafeURL):
        tools._http_get("http://public.example/")

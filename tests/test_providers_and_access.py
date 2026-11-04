import socket

import httpx
import pytest

from conference_agent.cache import Cache
from conference_agent.config import Settings
from conference_agent.network import FetchError, HTTPTransport
from conference_agent.search_provider import BraveSearch, SearchManager, TavilySearch
from conference_agent.source_fetcher import SourceFetcher, parse_html
from conference_agent.urls import normalize_url, require_public_url


def test_url_normalization_preserves_meaning():
    assert (
        normalize_url("HTTPS://Example.EDU/call/?year=2027&utm_source=mail#dates")
        == "https://example.edu/call/?year=2027"
    )
    assert normalize_url("https://example.edu/CFP/?track=crime") != normalize_url(
        "https://example.edu/cfp/?track=networks"
    )


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "https://user:pass@example.edu"]
)
def test_unsupported_urls_rejected(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_private_ip_and_mixed_dns_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError):
        require_public_url("https://example.edu/cfp")


def test_brave_request_and_result_shape(tmp_path):
    def handle(request):
        assert request.headers["X-Subscription-Token"] == "test-secret"
        assert request.url.params["q"] == "crime workshop"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [{"title": "Workshop", "url": "https://uni.edu/cfp", "description": "CFP"}]
                }
            },
        )

    transport = HTTPTransport(
        Settings(request_interval=0), httpx.Client(transport=httpx.MockTransport(handle))
    )
    provider = BraveSearch("test-secret", transport)
    manager = SearchManager([provider], Cache(tmp_path), 3600)
    hits = manager.search("crime workshop")
    assert hits[0].provider == "brave"
    assert hits[0].snippet == "CFP"
    assert manager.search("crime workshop")[0].retrieved_at == hits[0].retrieved_at
    manager.cache.close()
    transport.close()


def test_tavily_request_and_result_shape():
    def handle(request):
        import json

        assert request.headers["Authorization"] == "Bearer test-secret"
        assert json.loads(request.content)["include_answer"] is False
        return httpx.Response(
            200, json={"results": [{"title": "Workshop", "url": "https://uni.edu/cfp", "content": "CFP"}]}
        )

    transport = HTTPTransport(
        Settings(request_interval=0), httpx.Client(transport=httpx.MockTransport(handle))
    )
    hits = TavilySearch("test-secret", transport).search("networks workshop")
    assert hits[0].provider == "tavily"
    transport.close()


def test_api_failure_does_not_expose_secrets(tmp_path, caplog):
    transport = HTTPTransport(
        Settings(request_interval=0),
        httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "test-secret"}))
        ),
    )
    cache = Cache(tmp_path)
    manager = SearchManager([BraveSearch("test-secret", transport)], cache, 0)
    assert manager.search("CFP") == []
    assert manager.issues
    assert "test-secret" not in str(manager.issues) + caplog.text
    transport.close()
    cache.close()


def fetcher_for(tmp_path, monkeypatch, handler):
    monkeypatch.setattr("conference_agent.source_fetcher.require_public_url", lambda url: url)
    settings = Settings(state_dir=tmp_path, request_interval=0)
    transport = HTTPTransport(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    cache = Cache(tmp_path)
    return SourceFetcher(settings, transport, cache)


def test_robots_disallow_prevents_page_fetch(tmp_path, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /private")

    fetcher = fetcher_for(tmp_path, monkeypatch, handle)
    with pytest.raises(FetchError, match="robots"):
        fetcher.fetch("https://example.edu/private/cfp")
    assert requests == ["/robots.txt"]
    fetcher.cache.close()
    fetcher.transport.close()


def test_robots_failure_is_not_silently_allowed(tmp_path, monkeypatch):
    fetcher = fetcher_for(tmp_path, monkeypatch, lambda _: httpx.Response(403))
    with pytest.raises(FetchError, match="robots"):
        fetcher.fetch("https://example.edu/cfp")
    fetcher.cache.close()
    fetcher.transport.close()


def test_restricted_source_not_bypassed(tmp_path, monkeypatch):
    requests = []

    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/robots.txt" else 403)

    fetcher = fetcher_for(tmp_path, monkeypatch, handle)
    with pytest.raises(FetchError, match="restricted"):
        fetcher.fetch("https://example.edu/cfp")
    assert requests == ["/robots.txt", "/cfp"]
    fetcher.cache.close()
    fetcher.transport.close()


def test_public_source_cache_retains_original_retrieval(tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<h1>Workshop 2027</h1><p>Call for papers. We invite submissions.</p>",
        )

    fetcher = fetcher_for(tmp_path, monkeypatch, handle)
    first = fetcher.fetch("https://example.edu/cfp")
    second = fetcher.fetch("https://example.edu/cfp")
    assert second.from_cache
    assert first.retrieved_at == second.retrieved_at
    assert second.extracted_at >= first.extracted_at
    assert calls == ["/robots.txt", "/cfp"]
    assert first.raw_path and first.sha256
    fetcher.cache.close()
    fetcher.transport.close()


def test_redirect_rechecks_destination_robots(tmp_path, monkeypatch):
    calls = []

    def handle(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path == "/robots.txt":
            if request.url.host == "restricted.edu":
                return httpx.Response(200, text="User-agent: *\nDisallow: /")
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://restricted.edu/cfp"})

    fetcher = fetcher_for(tmp_path, monkeypatch, handle)
    with pytest.raises(FetchError, match="robots"):
        fetcher.fetch("https://example.edu/cfp")
    assert ("restricted.edu", "/robots.txt") in calls
    assert ("restricted.edu", "/cfp") not in calls
    fetcher.cache.close()
    fetcher.transport.close()


def test_retry_after_is_honoured_without_long_block(monkeypatch):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "300"})

    transport = HTTPTransport(
        Settings(request_interval=0), httpx.Client(transport=httpx.MockTransport(handle))
    )
    with pytest.raises(FetchError, match="deferred"):
        transport.request("GET", "https://example.edu")
    with pytest.raises(FetchError, match="deferred"):
        transport.request("GET", "https://example.edu")
    assert len(calls) == 1
    transport.close()


def test_bounded_transient_retries(monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr("conference_agent.network.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "conference_agent.network.time.sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )

    def handle(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) < 3 else 200, text="ok")

    transport = HTTPTransport(
        Settings(request_interval=0), httpx.Client(transport=httpx.MockTransport(handle))
    )
    assert transport.request("GET", "https://example.edu").text == "ok"
    assert len(calls) == 3
    transport.close()


def test_html_publication_date_links_and_noise_removed():
    source = parse_html(
        b'<title>University</title><meta property="article:published_time" content="2026-10-01"><nav>Old events</nav><main><h1>Crime Workshop 2027</h1><p>Call for papers</p><a href="/submit?utm_source=mail">Submit your paper</a></main><script>ignore this</script>',
        "https://example.edu/cfp",
    )
    assert source.title == "Crime Workshop 2027"
    assert source.published_at == "2026-10-01"
    assert "Old events" not in source.text
    assert "ignore this" not in source.text
    assert source.links[0].url == "https://example.edu/submit"
    assert source.links[0].raw_url == "https://example.edu/submit?utm_source=mail"

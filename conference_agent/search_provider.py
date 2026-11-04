from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .cache import Cache
from .config import ConfigurationError, Settings
from .models import SearchHit
from .network import FetchError, HTTPTransport
from .urls import normalize_url

logger = logging.getLogger(__name__)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, count: int = 5) -> list[SearchHit]: ...


class BraveSearch:
    name = "brave"

    def __init__(self, key: str, transport: HTTPTransport):
        self.key, self.transport = key, transport

    def search(self, query: str, count: int = 5) -> list[SearchHit]:
        response = self.transport.request(
            "GET",
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query[:590], "count": min(count, 20), "search_lang": "en"},
            headers={"X-Subscription-Token": self.key, "Accept": "application/json"},
        )
        if response.status_code != 200:
            raise FetchError(f"Brave returned HTTP {response.status_code}; check credentials and plan.")
        data = response.json()
        return [
            SearchHit(
                title=item.get("title", ""),
                url=item["url"],
                snippet=item.get("description", ""),
                provider=self.name,
                query=query,
                published_at=item.get("page_age"),
            )
            for item in data.get("web", {}).get("results", [])
            if item.get("url")
        ]


class TavilySearch:
    name = "tavily"

    def __init__(self, key: str, transport: HTTPTransport):
        self.key, self.transport = key, transport

    def search(self, query: str, count: int = 5) -> list[SearchHit]:
        response = self.transport.request(
            "POST",
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.key}"},
            json={
                "query": query,
                "max_results": min(count, 20),
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        if response.status_code != 200:
            raise FetchError(f"Tavily returned HTTP {response.status_code}; check credentials and plan.")
        return [
            SearchHit(
                title=item.get("title", ""),
                url=item["url"],
                snippet=item.get("content", ""),
                provider=self.name,
                query=query,
                published_at=item.get("published_date"),
            )
            for item in response.json().get("results", [])
            if item.get("url")
        ]


def configured_providers(settings: Settings, transport: HTTPTransport) -> list[SearchProvider]:
    providers: list[SearchProvider] = []
    if settings.brave_key and settings.search_provider in {"auto", "brave"}:
        providers.append(BraveSearch(settings.brave_key, transport))
    if settings.tavily_key and settings.search_provider in {"auto", "tavily"}:
        providers.append(TavilySearch(settings.tavily_key, transport))
    if settings.search_provider != "auto" and not providers:
        raise ConfigurationError(f"SEARCH_PROVIDER={settings.search_provider} has no configured API key.")
    return providers


class SearchManager:
    def __init__(self, providers: list[SearchProvider], cache: Cache, ttl: float):
        self.providers, self.cache, self.ttl = providers, cache, ttl
        self.issues: list[str] = []
        self.requests = 0
        self.successes = 0

    def search(self, query: str, count: int = 5) -> list[SearchHit]:
        found: dict[str, SearchHit] = {}
        for provider in self.providers:
            self.requests += 1
            key = Cache.key("search-v1", [provider.name, query, count])
            try:
                cached = self.cache.get(key, self.ttl)
                hits = (
                    [SearchHit.model_validate(h) for h in cached]
                    if cached is not None
                    else provider.search(query, count)
                )
                if cached is None:
                    self.cache.put(key, [h.model_dump(mode="json") for h in hits])
                self.successes += 1
                for hit in hits:
                    try:
                        hit.raw_url = hit.raw_url or hit.url
                        hit.url = normalize_url(hit.url)
                        found.setdefault(hit.url, hit)
                    except ValueError:
                        continue
                logger.info("search_complete", extra={"provider": provider.name, "count": len(hits)})
            except (FetchError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                # API bodies and credentials are deliberately excluded from logs/reports.
                issue = f"{provider.name} search failed ({type(exc).__name__})."
                if issue not in self.issues:
                    self.issues.append(issue)
                logger.warning(issue)
        return list(found.values())

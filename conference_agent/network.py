from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


class HTTPTransport:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.interval = settings.request_interval
        self.user_agent = "ResearchConferenceDiscovery/0.1"
        if settings.contact:
            self.user_agent += " (" + settings.contact.replace("\n", " ").replace("\r", " ") + ")"
        self.client = client or httpx.Client(
            timeout=settings.timeout,
            follow_redirects=False,
            headers={"User-Agent": self.user_agent},
            trust_env=False,
        )
        self.next_allowed: dict[str, float] = {}

    def request(
        self, method: str, url: str, *, min_interval: float = 0, max_bytes: int = 6_000_000, **kwargs
    ) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(3):
            delay = self.next_allowed.get(host, 0) - time.monotonic()
            if delay > 60:
                raise FetchError("Host deferred by rate limit; retry on a later run.")
            if delay > 0:
                time.sleep(delay)
            self.next_allowed[host] = time.monotonic() + max(self.interval, min_interval)
            try:
                with self.client.stream(method, url, **kwargs) as response:
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > max_bytes:
                        raise FetchError("Source exceeds download limit.")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError("Source exceeds download limit.")
                        chunks.append(chunk)
                    # iter_bytes decodes compression; avoid decoding the reconstructed body twice.
                    headers = {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in {"content-encoding", "content-length"}
                    }
                    buffered = httpx.Response(
                        response.status_code,
                        headers=headers,
                        content=b"".join(chunks),
                        request=response.request,
                    )
                if buffered.status_code not in {429, 500, 502, 503, 504}:
                    return buffered
                retry_after = buffered.headers.get("retry-after")
                backoff = 2**attempt + random.uniform(0, 0.2)
                if retry_after:
                    try:
                        backoff = max(backoff, float(retry_after))
                    except ValueError:
                        try:
                            retry_date = parsedate_to_datetime(retry_after)
                            backoff = max(backoff, (retry_date - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError, OverflowError):
                            pass
                self.next_allowed[host] = time.monotonic() + backoff
                if backoff > 60:
                    raise FetchError("Retry-After exceeds run wait budget; host deferred.")
                if attempt == 2:
                    raise FetchError(f"HTTP {buffered.status_code} after retries.")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 2:
                    raise FetchError(f"Network request failed ({type(exc).__name__}).") from exc
                self.next_allowed[host] = time.monotonic() + 2**attempt
            logger.info("request_retry", extra={"host": host, "attempt": attempt + 1})
        raise FetchError("Request did not complete.")

    def close(self) -> None:
        self.client.close()

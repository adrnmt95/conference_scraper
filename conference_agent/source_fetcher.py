from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .cache import Cache
from .config import Settings
from .models import Source, SourceLink, utcnow
from .network import FetchError, HTTPTransport
from .urls import domain_matches, hostname, normalize_url, require_public_url


def parse_html(raw: bytes, url: str) -> Source:
    soup = BeautifulSoup(raw, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    heading = soup.find("h1")
    if heading and len(heading.get_text(" ", strip=True)) > 8:
        title = heading.get_text(" ", strip=True)
    published, kind = None, None
    for attrs in [{"property": "article:published_time"}, {"name": "date"}, {"itemprop": "datePublished"}]:
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            published, kind = str(meta["content"]), "page publication metadata (not independently dated)"
            break
    links = []
    for anchor in soup.find_all("a", href=True):
        try:
            raw_link = urljoin(url, str(anchor["href"]))
            link = normalize_url(raw_link)
        except ValueError:
            continue
        links.append(SourceLink(url=link, raw_url=raw_link, text=anchor.get_text(" ", strip=True)[:300]))
    for item in soup(["script", "style", "nav", "footer", "noscript", "form"]):
        item.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    lines = [re.sub(r"\s+", " ", line).strip() for line in main.get_text("\n", strip=True).splitlines()]
    text = "\n".join(line for line in lines if line)
    return Source(
        url=url,
        final_url=url,
        title=title,
        text=text[:160000],
        links=links[:500],
        published_at=published,
        publication_date_kind=kind,
    )


class SourceFetcher:
    def __init__(self, settings: Settings, transport: HTTPTransport, cache: Cache):
        self.settings, self.transport, self.cache = settings, transport, cache
        self.robots: dict[str, tuple[RobotFileParser | None, bool]] = {}
        self.raw_dir = settings.state_dir / "sources"
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def _permitted(self, url: str) -> tuple[bool, float]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.robots:
            key = Cache.key("robots-v1", origin)
            saved = self.cache.get(key, 3600)
            if saved is None:
                try:
                    robots_url = origin + "/robots.txt"
                    for _ in range(4):
                        require_public_url(robots_url)
                        response = self.transport.request("GET", robots_url, max_bytes=500000)
                        if response.is_redirect:
                            target = normalize_url(urljoin(robots_url, response.headers.get("location", "")))
                            if hostname(target) != hostname(url):
                                raise FetchError("Cross-origin robots redirect is uncertain.")
                            robots_url = target
                            continue
                        break
                    else:
                        raise FetchError("Too many robots redirects.")
                    saved = {"status": response.status_code, "text": response.text}
                    self.cache.put(key, saved)
                except (FetchError, ValueError):
                    saved = {"status": 503, "text": ""}
            status = saved["status"]
            parser = None
            allowed = status in {404, 410}
            if status == 200:
                parser = RobotFileParser(origin + "/robots.txt")
                parser.parse(saved["text"].splitlines())
                allowed = True
            self.robots[origin] = parser, allowed
        parser, allowed = self.robots[origin]
        if not allowed:
            return False, 0
        if parser:
            return parser.can_fetch(self.transport.user_agent, url), float(
                parser.crawl_delay(self.transport.user_agent) or parser.crawl_delay("*") or 0
            )
        return True, 0

    def fetch(self, url: str) -> Source:
        raw_url = url
        original = normalize_url(url)
        url = original
        for _ in range(6):
            url = require_public_url(url)
            if any(domain_matches(hostname(url), blocked) for blocked in self.settings.blocked_domains):
                raise FetchError("Domain excluded by configured website terms/access policy.")
            if re.search(r"/(?:login|sign-?in|captcha|auth)(?:/|\?|$)", url, re.I):
                raise FetchError("Login or anti-bot page is not fetched.")
            permitted, delay = self._permitted(url)
            if not permitted:
                raise FetchError("robots.txt disallows access or could not be checked reliably.")
            key = Cache.key("source-v1", url)
            saved = self.cache.get(key, min(self.settings.source_cache_seconds, 6 * 3600))
            if saved:
                source = Source.model_validate(saved)
                source.from_cache = True
                source.extracted_at = utcnow()
                source.url = original
                source.raw_url = raw_url
                return source
            response = self.transport.request("GET", url, min_interval=delay)
            if response.is_redirect:
                url = normalize_url(urljoin(url, response.headers.get("location", "")))
                continue  # Every redirect target gets its own public-URL and robots checks.
            if response.status_code in {401, 403, 429}:
                raise FetchError(f"Access restricted (HTTP {response.status_code}); no bypass attempted.")
            if response.status_code != 200:
                raise FetchError(f"Source returned HTTP {response.status_code}.")
            raw = response.content
            ctype = response.headers.get("content-type", "").split(";")[0].lower()
            if ctype == "application/pdf" or raw.startswith(b"%PDF-"):
                from pypdf import PdfReader

                try:
                    reader = PdfReader(io.BytesIO(raw))
                    if reader.is_encrypted:
                        raise FetchError("Encrypted CFP is not accessible.")
                    text = "\n".join(page.extract_text() or "" for page in reader.pages)
                    source = Source(
                        url=original,
                        final_url=url,
                        title=Path(urlsplit(url).path).stem,
                        text=text[:160000],
                        content_type="application/pdf",
                    )
                except FetchError:
                    raise
                except Exception as exc:
                    raise FetchError("Could not extract the public CFP PDF.") from exc
                suffix = ".pdf"
            elif ctype in {"text/html", "application/xhtml+xml", ""}:
                source = parse_html(raw, url)
                suffix = ".html"
            elif ctype == "text/plain":
                source = Source(url=original, final_url=url, title="", text=response.text[:160000])
                suffix = ".txt"
            else:
                raise FetchError("Unsupported source content type.")
            if re.search(
                r"verify you are human|just a moment.*cloudflare|access denied|enable javascript and cookies",
                source.text[:3000],
                re.I,
            ):
                raise FetchError("Anti-bot page encountered; source left unverified.")
            if re.search(
                r"(?:automated (?:access|scraping)|web scraping) (?:is |are )?(?:strictly )?prohibited",
                source.text,
                re.I,
            ):
                raise FetchError("Source states that automated access is prohibited.")
            if len(source.text.strip()) < 30:
                raise FetchError("Source has too little public text (possibly JavaScript or a scanned PDF).")
            source.url, source.final_url = original, url
            source.raw_url = raw_url
            source.content_type = ctype or source.content_type
            source.sha256 = hashlib.sha256(raw).hexdigest()
            raw_path = self.raw_dir / (source.sha256 + suffix)
            raw_path.write_bytes(raw)
            source.raw_path = str(raw_path.resolve())
            self.cache.put(key, source.model_dump(mode="json"))
            return source
        raise FetchError("Too many source redirects.")

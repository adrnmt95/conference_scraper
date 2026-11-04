from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError("Only public HTTP(S) URLs are supported.")
    if parts.username or parts.password:
        raise ValueError("Authenticated URLs are not supported.")
    host = parts.hostname.lower().encode("idna").decode()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    netloc = host if port in {None, 80 if parts.scheme == "http" else 443} else f"{host}:{port}"
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    ]
    path = parts.path or "/"
    # Keep path case, trailing slashes, and semantic query parameters intact.
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(sorted(query)), ""))


def hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def domain_matches(host: str, domain: str) -> bool:
    domain = domain.lower().strip().removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def require_public_url(url: str) -> str:
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    host = parts.hostname or ""
    if parts.port not in {None, 80, 443}:
        raise ValueError("Nonstandard network ports are not fetched.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Private network URLs are not fetched.")
    try:
        addresses = socket.getaddrinfo(host, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Source hostname could not be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
        raise ValueError("Private or reserved network addresses are not fetched.")
    return normalized


def series_key(name: str, organiser: str | None = None) -> str:
    value = name.casefold()
    value = re.sub(r"\b(?:19|20)\d{2}\b|\b\d+(?:st|nd|rd|th)\b", " ", value)
    value = re.sub(r"\b(?:call for papers|cfp)\b", " ", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))

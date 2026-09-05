"""Hardened HTTP fetch: browser-TLS-impersonated requests via Scrapling.

Many anti-bot walls fingerprint the TLS handshake, not just headers - a
perfect User-Agent string from python-requests still looks like Python on
the wire. Scrapling's Fetcher (curl_cffi underneath) impersonates Chrome's
actual TLS fingerprint.

Measured on our own blocklist (2026-09-05):
  - careers.cognizant.com : requests 403 -> scrapling 200
  - tcs.com/careers       : blocked     -> scrapling 200

NOTE: scrapling's browser engines (StealthyFetcher/DynamicFetcher) crash or
time out on this macOS setup - only the plain HTTP Fetcher is used here.
Re-evaluate the browser engines on the Linux deployment box.
"""
from typing import Optional


class HardenedResponse:
    """Small requests-like shim so call sites can stay simple."""
    def __init__(self, status: int, text: str, url: str):
        self.status_code = status
        self.text = text
        self.url = url


def hardened_get(url: str, timeout: int = 20, headers: Optional[dict] = None):
    """GET with Chrome TLS impersonation; falls back to plain requests.

    Returns HardenedResponse or raises on total failure.
    """
    try:
        from scrapling.fetchers import Fetcher
        p = Fetcher.get(url, impersonate="chrome", timeout=timeout,
                        stealthy_headers=True, headers=headers or {})
        body = p.body if isinstance(p.body, str) else p.body.decode("utf-8", errors="ignore")
        return HardenedResponse(p.status, body, getattr(p, "url", url))
    except Exception:
        # scrapling missing or failed - plain requests is better than nothing
        import requests
        r = requests.get(url, timeout=timeout, headers=headers or {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"})
        return HardenedResponse(r.status_code, r.text, r.url)

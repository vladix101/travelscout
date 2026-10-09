"""Guarded async HTTP client used by every server-side provider."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.security import BlockedURLError, assert_url_allowed

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)


class ProviderHTTPError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


async def _guard(request: httpx.Request) -> None:
    # Runs for the initial request and for every redirect hop.
    await asyncio.to_thread(assert_url_allowed, str(request.url))


def make_client(timeout: float = 30.0, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        max_redirects=3,
        headers=headers or {},
        event_hooks={"request": [_guard]},
    )


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    attempts: int = 2,
    backoff: float = 1.5,
    **kwargs: Any,
) -> httpx.Response:
    """Limited retries for timeouts / 5xx / 429. Never retries blocked URLs or other 4xx."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.request(method, url, **kwargs)
        except BlockedURLError:
            raise
        except httpx.TimeoutException as exc:
            last_exc = exc
            log.warning("timeout %s %s (attempt %d)", method, url.split("?")[0], attempt)
        except httpx.HTTPError as exc:
            last_exc = exc
            log.warning("http error %s %s: %s", method, url.split("?")[0], type(exc).__name__)
        else:
            if resp.status_code < 400:
                return resp
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise ProviderHTTPError(f"HTTP {resp.status_code}", resp.status_code)
            last_exc = ProviderHTTPError(f"HTTP {resp.status_code}", resp.status_code)
        if attempt < attempts:
            await asyncio.sleep(backoff * attempt)
    if isinstance(last_exc, ProviderHTTPError):
        raise last_exc
    raise ProviderHTTPError(f"request failed: {type(last_exc).__name__}")

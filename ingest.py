"""Module 1 - Data Scraping Spiders (ingestion).

Fetches a list of target URLs (RSS/Atom feeds, Reddit-style JSON listings, or
plain HTML pages) concurrently, strips HTML noise, and emits a JSON array of
records shaped as::

    {"timestamp": ISO-8601 str, "source_url": str, "headline": str, "raw_text": str}

Usage::

    python ingest.py https://hnrss.org/frontpage -o data/raw.json
    python ingest.py --sources sources.txt -o data/raw.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from calendar import timegm
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger("webbot.ingest")

USER_AGENT = "future-web-bot/0.1 (+linguistic trend research; respectful crawler)"
DEFAULT_CONCURRENCY = 5
DEFAULT_PER_HOST_INTERVAL = 2.0  # seconds between requests to the same host
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 3

_WS_RE = re.compile(r"\s+")
_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe")


class HostRateLimiter:
    """Enforces a minimum interval between requests to the same host."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last.get(host, 0.0)
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last[host] = time.monotonic()


def clean_html(html: str) -> str:
    """Strip tags, scripts, and boilerplate; collapse whitespace."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    return _WS_RE.sub(" ", soup.get_text(" ")).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _struct_to_iso(struct: Any) -> str | None:
    if not struct:
        return None
    return datetime.fromtimestamp(timegm(struct), tz=timezone.utc).isoformat(timespec="seconds")


def parse_feed(body: str, source_url: str) -> list[dict]:
    """Parse RSS/Atom into records. Returns [] if the body isn't a feed."""
    feed = feedparser.parse(body)
    if not feed.entries:
        return []
    records = []
    for entry in feed.entries:
        text = ""
        if entry.get("content"):
            text = " ".join(c.get("value", "") for c in entry.content)
        text = text or entry.get("summary", "") or entry.get("description", "")
        headline = clean_html(entry.get("title", ""))
        raw_text = clean_html(text)
        if not (headline or raw_text):
            continue
        records.append({
            "timestamp": _struct_to_iso(entry.get("published_parsed") or entry.get("updated_parsed")) or _now_iso(),
            "source_url": entry.get("link") or source_url,
            "headline": headline,
            "raw_text": raw_text,
        })
    return records


def parse_reddit_json(data: Any, source_url: str) -> list[dict]:
    """Parse a Reddit listing (e.g. https://www.reddit.com/r/worldnews/new.json)."""
    try:
        children = data["data"]["children"]
    except (KeyError, TypeError):
        return []
    records = []
    for child in children:
        post = child.get("data", {})
        created = post.get("created_utc")
        ts = datetime.fromtimestamp(created, tz=timezone.utc).isoformat(timespec="seconds") if created else _now_iso()
        permalink = post.get("permalink")
        records.append({
            "timestamp": ts,
            "source_url": f"https://www.reddit.com{permalink}" if permalink else source_url,
            "headline": post.get("title", "").strip(),
            "raw_text": clean_html(post.get("selftext_html") or "") or post.get("selftext", "").strip(),
        })
    return records


def parse_html_page(body: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = clean_html(body)
    if not text:
        return []
    return [{"timestamp": _now_iso(), "source_url": source_url, "headline": title, "raw_text": text}]


def parse_response(body: str, content_type: str, source_url: str) -> list[dict]:
    """Dispatch on content type / shape: JSON listing, feed, or HTML page."""
    ctype = content_type.lower()
    if "json" in ctype:
        try:
            return parse_reddit_json(json.loads(body), source_url)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from %s", source_url)
            return []
    if any(t in ctype for t in ("xml", "rss", "atom")) or body.lstrip().startswith("<?xml"):
        records = parse_feed(body, source_url)
        if records:
            return records
    return parse_html_page(body, source_url)


async def fetch_one(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    limiter: HostRateLimiter,
) -> list[dict]:
    """Fetch and parse a single URL with retries on 429/5xx/network errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        async with semaphore:
            await limiter.wait(url)
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                log.warning("Network error on %s (attempt %d/%d): %s", url, attempt, MAX_RETRIES, exc)
                resp = None
        if resp is not None:
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = resp.headers.get("retry-after", "")
                delay = float(retry_after) if retry_after.isdigit() else 2.0 ** attempt
                log.warning("HTTP %d from %s; backing off %.1fs", resp.status_code, url, delay)
                await asyncio.sleep(min(delay, 60.0))
                continue
            if resp.status_code >= 400:
                log.error("HTTP %d from %s; skipping", resp.status_code, url)
                return []
            records = parse_response(resp.text, resp.headers.get("content-type", ""), url)
            log.info("Fetched %d records from %s", len(records), url)
            return records
        await asyncio.sleep(2.0 ** attempt)
    log.error("Giving up on %s after %d attempts", url, MAX_RETRIES)
    return []


async def ingest(
    urls: Iterable[str],
    concurrency: int = DEFAULT_CONCURRENCY,
    per_host_interval: float = DEFAULT_PER_HOST_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict]:
    """Fetch every URL concurrently and return a flat, de-duplicated record list."""
    urls = list(dict.fromkeys(u.strip() for u in urls if u.strip()))
    semaphore = asyncio.Semaphore(concurrency)
    limiter = HostRateLimiter(per_host_interval)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
        transport=transport,
    ) as client:
        results = await asyncio.gather(
            *(fetch_one(client, u, semaphore, limiter) for u in urls),
            return_exceptions=True,
        )
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for url, result in zip(urls, results):
        if isinstance(result, BaseException):
            log.error("Unhandled error for %s: %r", url, result)
            continue
        for rec in result:
            key = (rec["source_url"], rec["headline"])
            if key not in seen:
                seen.add(key)
                records.append(rec)
    return records


def load_sources(path: str | Path) -> list[str]:
    lines = Path(path).read_text().splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and clean target sources into JSON records.")
    parser.add_argument("urls", nargs="*", help="Target URLs (RSS/Atom, JSON listings, HTML pages)")
    parser.add_argument("--sources", help="File with one URL per line (# comments allowed)")
    parser.add_argument("-o", "--output", help="Output JSON file (default: stdout)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--per-host-interval", type=float, default=DEFAULT_PER_HOST_INTERVAL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    urls = list(args.urls) + (load_sources(args.sources) if args.sources else [])
    if not urls:
        parser.error("provide at least one URL or --sources")

    records = asyncio.run(ingest(urls, args.concurrency, args.per_host_interval))
    payload = json.dumps(records, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload)
        log.info("Wrote %d records to %s", len(records), args.output)
    else:
        sys.stdout.write(payload + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

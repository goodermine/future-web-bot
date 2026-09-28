import asyncio
import json

import httpx

import ingest

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>t</title>
<item><title>Grid failure spreads</title><link>https://ex.com/a</link>
<description>&lt;p&gt;Power is &lt;b&gt;out&lt;/b&gt; now&lt;/p&gt;&lt;script&gt;x()&lt;/script&gt;</description>
<pubDate>Mon, 01 Sep 2026 12:00:00 GMT</pubDate></item>
</channel></rss>"""

REDDIT = {"data": {"children": [{"data": {
    "title": "Something is happening", "selftext": "people are worried",
    "created_utc": 1788000000, "permalink": "/r/x/comments/1/y/"}}]}}


def _transport():
    calls = {"flaky": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/feed":
            return httpx.Response(200, text=RSS, headers={"content-type": "application/rss+xml"})
        if path == "/r.json":
            return httpx.Response(200, text=json.dumps(REDDIT), headers={"content-type": "application/json"})
        if path == "/page":
            return httpx.Response(200, text="<html><title>Hi</title><nav>menu</nav><p>Body text</p></html>",
                                  headers={"content-type": "text/html"})
        if path == "/flaky":
            calls["flaky"] += 1
            if calls["flaky"] == 1:
                return httpx.Response(503, headers={"retry-after": "0"})
            return httpx.Response(200, text="<p>recovered</p>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


def test_clean_html_strips_noise():
    assert ingest.clean_html("<p>a <b>b</b></p><script>bad()</script><style>x{}</style>") == "a b"


def test_ingest_all_source_types():
    transport, calls = _transport()
    urls = [f"https://ex.com/{p}" for p in ("feed", "r.json", "page", "flaky", "missing")]
    records = asyncio.run(ingest.ingest(urls, per_host_interval=0, transport=transport))
    by_head = {r["headline"]: r for r in records}

    rss = by_head["Grid failure spreads"]
    assert rss["raw_text"] == "Power is out now"
    assert rss["timestamp"] == "2026-09-01T12:00:00+00:00"
    assert rss["source_url"] == "https://ex.com/a"

    assert by_head["Something is happening"]["source_url"] == "https://www.reddit.com/r/x/comments/1/y/"
    assert by_head["Hi"]["raw_text"] == "Hi Body text"
    assert any(r["raw_text"] == "recovered" for r in records)
    assert calls["flaky"] == 2
    assert set(records[0]) == {"timestamp", "source_url", "headline", "raw_text"}

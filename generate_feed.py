#!/usr/bin/env python3
# generate_feed.py  (v2 - browser headers + cache-buster to defeat stale CDN copy)
"""
Build a podcast RSS feed by scraping the Faithful Word Baptist Church
sermons page (page5.html).

v2 change: some servers hand automated/datacenter clients a stale cached
copy of the page. We now send full browser-like headers and a cache-busting
query parameter to force the current version, and we print the date range so
the Action log shows exactly what was scraped.

Output: feed.xml
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SOURCE_URL = "https://www.faithfulwordbaptist.org/page5.html"
BASE_URL = "https://www.faithfulwordbaptist.org/"
OUTPUT_FILE = "feed.xml"
SIZE_CACHE_FILE = "sizes.json"

MAX_ITEMS = None            # None = every sermon
FETCH_SIZES = True

FEED_TITLE = "Faithful Word Baptist Church - Sermons"
FEED_DESCRIPTION = (
    "Sermon audio scraped from the Faithful Word Baptist Church website. "
    "Unofficial personal feed."
)
FEED_LINK = "https://www.faithfulwordbaptist.org/page5.html"
FEED_LANGUAGE = "en-us"
FEED_AUTHOR = "Faithful Word Baptist Church"

# Full browser-like headers. Servers that serve stale copies to bots often
# key off a missing/odd User-Agent, so we present as a normal Chrome browser.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def parse_date(date_cell_text):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", date_cell_text)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        year += 2000
    text = date_cell_text.upper()
    hour = 19 if "PM" in text else (10 if "AM" in text else 12)
    try:
        return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


def load_size_cache():
    if os.path.exists(SIZE_CACHE_FILE):
        try:
            with open(SIZE_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_size_cache(cache):
    try:
        with open(SIZE_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except OSError:
        pass


def get_size(url, cache):
    if url in cache:
        return cache[url]
    if not FETCH_SIZES:
        return 0
    try:
        r = requests.head(url, headers=HEADERS, timeout=20, allow_redirects=True)
        length = int(r.headers.get("Content-Length", 0))
    except (requests.RequestException, ValueError):
        length = 0
    cache[url] = length
    return length


def scrape_episodes():
    # Cache-busting query param forces a fresh copy past any CDN cache.
    resp = requests.get(
        SOURCE_URL,
        headers=HEADERS,
        params={"_": int(time.time())},
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    episodes, seen = [], set()
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not href.lower().endswith(".mp3"):
            continue
        mp3_url = href if href.startswith("http") else BASE_URL + href.lstrip("/")
        if mp3_url in seen:
            continue
        row = link.find_parent("tr")
        if row is None:
            continue
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        date_text = cells[0].get_text(" ", strip=True)
        title = cells[1].get_text(" ", strip=True)
        speaker = cells[-1].get_text(" ", strip=True) if len(cells) >= 3 else FEED_AUTHOR
        pub_date = parse_date(date_text)
        if pub_date is None or not title:
            continue
        seen.add(mp3_url)
        episodes.append(
            {"title": title, "speaker": speaker or FEED_AUTHOR, "url": mp3_url, "date": pub_date}
        )

    episodes.sort(key=lambda e: e["date"], reverse=True)
    if MAX_ITEMS:
        episodes = episodes[:MAX_ITEMS]
    return episodes


def build_rss(episodes):
    size_cache = load_size_cache()
    now = format_datetime(datetime.now(timezone.utc))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    parts.append(
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">'
    )
    parts.append("<channel>")
    parts.append(f"<title>{escape(FEED_TITLE)}</title>")
    parts.append(f"<link>{escape(FEED_LINK)}</link>")
    parts.append(f"<description>{escape(FEED_DESCRIPTION)}</description>")
    parts.append(f"<language>{FEED_LANGUAGE}</language>")
    parts.append(f"<lastBuildDate>{now}</lastBuildDate>")
    parts.append(f"<itunes:author>{escape(FEED_AUTHOR)}</itunes:author>")
    parts.append("<itunes:explicit>false</itunes:explicit>")
    parts.append(
        '<itunes:category text="Religion &amp; Spirituality">'
        '<itunes:category text="Christianity"/></itunes:category>'
    )
    for ep in episodes:
        length = get_size(ep["url"], size_cache)
        parts.append("<item>")
        parts.append(f"<title>{escape(ep['title'])}</title>")
        parts.append(f"<itunes:author>{escape(ep['speaker'])}</itunes:author>")
        parts.append(f"<description>{escape(ep['speaker'])}</description>")
        parts.append(f'<enclosure url="{escape(ep["url"])}" length="{length}" type="audio/mpeg"/>')
        parts.append(f'<guid isPermaLink="true">{escape(ep["url"])}</guid>')
        parts.append(f"<pubDate>{format_datetime(ep['date'])}</pubDate>")
        parts.append("</item>")
    parts.append("</channel>")
    parts.append("</rss>")
    save_size_cache(size_cache)
    return "\n".join(parts)


def main():
    episodes = scrape_episodes()
    if not episodes:
        print("ERROR: no episodes found - the page layout may have changed.", file=sys.stderr)
        sys.exit(1)
    rss = build_rss(episodes)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(rss)
    # Diagnostics visible in the Action log:
    print(f"Wrote {OUTPUT_FILE} with {len(episodes)} episodes.")
    print(f"NEWEST: {episodes[0]['date'].date()}  {episodes[0]['title']}")
    print(f"OLDEST: {episodes[-1]['date'].date()}  {episodes[-1]['title']}")


if __name__ == "__main__":
    main()

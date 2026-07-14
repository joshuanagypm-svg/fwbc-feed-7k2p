#!/usr/bin/env python3
"""
Build a podcast RSS feed by scraping the Faithful Word Baptist Church
sermons page (page5.html), which lists each sermon with a direct MP3 link.

The site's own .rss file stopped updating in 2016, so this regenerates a
fresh feed from the live HTML table every time it runs.

Output: feed.xml  (a valid RSS 2.0 + iTunes podcast feed)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
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
SIZE_CACHE_FILE = "sizes.json"   # caches MP3 byte sizes so we don't re-HEAD every run

# Set to a number to cap how many episodes appear in the feed (newest first),
# or None to include everything. Most podcast apps are happy with 200-ish.
MAX_ITEMS = None

# Whether to fetch each NEW mp3's byte size via a HEAD request (for accurate
# <enclosure length>). Cached after the first time. Safe to leave True.
FETCH_SIZES = True

FEED_TITLE = "Faithful Word Baptist Church - Sermons"
FEED_DESCRIPTION = (
    "Sermon audio scraped from the Faithful Word Baptist Church website. "
    "Unofficial personal feed."
)
FEED_LINK = "https://www.faithfulwordbaptist.org/page5.html"
FEED_LANGUAGE = "en-us"
FEED_AUTHOR = "Faithful Word Baptist Church"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; personal-podcast-feed/1.0)"
}


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------
def parse_date(date_cell_text):
    """
    Turn a cell like '07/08/26, Wed PM' into a timezone-aware datetime.

    We use the AM/PM marker to offset the time of day so that a morning and
    an evening sermon on the same date keep the correct order in the feed.
    Returns None if no MM/DD/YY date can be found.
    """
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", date_cell_text)
    if not m:
        return None

    month, day, year = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if year < 100:                      # '26' -> 2026
        year += 2000

    # Pick an hour from the AM/PM/evening hint so ordering is stable.
    text = date_cell_text.upper()
    if "PM" in text:
        hour = 19                       # evening service
    elif "AM" in text:
        hour = 10                       # morning service
    else:
        hour = 12

    try:
        return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Size lookup (cached)
# ---------------------------------------------------------------------------
def load_size_cache():
    if os.path.exists(SIZE_CACHE_FILE):
        try:
            with open(SIZE_CACHE_FILE, "r") as f:
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
    """Return the Content-Length for an mp3, using the cache when possible."""
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


# ---------------------------------------------------------------------------
# Scrape
# ---------------------------------------------------------------------------
def scrape_episodes():
    resp = requests.get(SOURCE_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    episodes = []
    seen = set()

    # Find every link that points at an .mp3 file.
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not href.lower().endswith(".mp3"):
            continue

        mp3_url = href if href.startswith("http") else BASE_URL + href.lstrip("/")
        if mp3_url in seen:
            continue

        # Walk up to the table row this link lives in.
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
            {
                "title": title,
                "speaker": speaker or FEED_AUTHOR,
                "url": mp3_url,
                "date": pub_date,
            }
        )

    # Newest first.
    episodes.sort(key=lambda e: e["date"], reverse=True)
    if MAX_ITEMS:
        episodes = episodes[:MAX_ITEMS]
    return episodes


# ---------------------------------------------------------------------------
# Build RSS
# ---------------------------------------------------------------------------
def build_rss(episodes):
    size_cache = load_size_cache()
    now = format_datetime(datetime.now(timezone.utc))

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
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
    parts.append('<itunes:explicit>false</itunes:explicit>')
    parts.append(
        '<itunes:category text="Religion &amp; Spirituality"><itunes:category text="Christianity"/></itunes:category>'
    )

    for ep in episodes:
        length = get_size(ep["url"], size_cache)
        pub = format_datetime(ep["date"])
        title = escape(ep["title"])
        speaker = escape(ep["speaker"])
        url = escape(ep["url"])

        parts.append("<item>")
        parts.append(f"<title>{title}</title>")
        parts.append(f"<itunes:author>{speaker}</itunes:author>")
        parts.append(f"<description>{speaker}</description>")
        parts.append(f'<enclosure url="{url}" length="{length}" type="audio/mpeg"/>')
        parts.append(f"<guid isPermaLink=\"true\">{url}</guid>")
        parts.append(f"<pubDate>{pub}</pubDate>")
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

    print(f"Wrote {OUTPUT_FILE} with {len(episodes)} episodes.")
    print(f"Newest: {episodes[0]['title']} ({episodes[0]['date'].date()})")


if __name__ == "__main__":
    main()

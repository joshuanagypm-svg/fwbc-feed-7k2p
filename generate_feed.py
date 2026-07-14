#!/usr/bin/env python3
# generate_feed.py  (v3 - fetch via relay so GitHub isn't served the stale 2014 copy)
"""
Build a podcast RSS feed from the Faithful Word Baptist Church sermons page.

Why v3: the church's site serves GitHub's servers a frozen ~Dec-2014 copy of
the page, no matter what headers we send. So instead of asking the site
directly, we route the request through public fetch relays (which pull the page
from their own servers and get the current version). We try several in order
and pick the first that returns fresh content. As a backup, each sermon's date
can also be read from its MP3 filename (e.g. 070826p.mp3 -> 2026-07-08 PM).

Output: feed.xml
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape

import requests
from bs4 import BeautifulSoup

SOURCE_URL = "https://www.faithfulwordbaptist.org/page5.html"
BASE_URL = "https://www.faithfulwordbaptist.org/"
OUTPUT_FILE = "feed.xml"
SIZE_CACHE_FILE = "sizes.json"

MAX_ITEMS = None
FETCH_SIZES = True
FRESH_YEAR = 2020          # if newest episode is >= this year, we consider the source current

FEED_TITLE = "Faithful Word Baptist Church - Sermons"
FEED_DESCRIPTION = (
    "Sermon audio scraped from the Faithful Word Baptist Church website. "
    "Unofficial personal feed."
)
FEED_LINK = SOURCE_URL
FEED_LANGUAGE = "en-us"
FEED_AUTHOR = "Faithful Word Baptist Church"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def parse_date(text):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yy < 100:
        yy += 2000
    t = text.upper()
    hour = 19 if "PM" in t else (10 if "AM" in t else 12)
    try:
        return datetime(yy, mm, dd, hour, tzinfo=timezone.utc)
    except ValueError:
        return None


def date_from_url(mp3_url):
    """Backup: read the date from an MP3 filename like 070826p.mp3 (MMDDYY + a/p)."""
    fn = mp3_url.rsplit("/", 1)[-1].lower()
    m = re.search(r"(\d{2})(\d{2})(\d{2})([ap])?\.mp3$", fn)
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = 19 if m.group(4) == "p" else (10 if m.group(4) == "a" else 12)
    try:
        return datetime(2000 + yy, mm, dd, hour, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_html(html):
    soup = BeautifulSoup(html, "html.parser")
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
        pub = parse_date(date_text) or date_from_url(mp3_url)
        if pub is None:
            continue
        if not title:
            title = f"Sermon {pub.date()}"
        seen.add(mp3_url)
        episodes.append(
            {"title": title, "speaker": speaker or FEED_AUTHOR, "url": mp3_url, "date": pub}
        )
    episodes.sort(key=lambda e: e["date"], reverse=True)
    return episodes


def get_episodes():
    """Try each relay in turn; use the first that returns current content."""
    enc = quote(SOURCE_URL, safe="")
    cb = int(time.time())
    strategies = [
        ("allorigins", f"https://api.allorigins.win/raw?url={enc}", {}),
        ("corsproxy",  f"https://corsproxy.io/?url={enc}", {}),
        ("jina",       f"https://r.jina.ai/{SOURCE_URL}", {"X-Return-Format": "html"}),
        ("direct",     f"{SOURCE_URL}?_={cb}", {}),
    ]
    best = []
    for name, url, extra in strategies:
        try:
            r = requests.get(url, headers={**HEADERS, **extra}, timeout=60)
            r.raise_for_status()
            eps = parse_html(r.text)
        except Exception as e:  # noqa: BLE001 - we want to try the next relay
            print(f"[{name}] failed: {e}")
            continue
        if not eps:
            print(f"[{name}] returned no parseable episodes")
            continue
        newest = max(e["date"] for e in eps)
        print(f"[{name}] {len(eps)} episodes, newest {newest.date()}")
        if newest.year >= FRESH_YEAR:
            print(f"[{name}] -> current content, using this source")
            return eps
        if len(eps) > len(best):
            best = eps
    print("WARNING: no relay returned current content; using best available.")
    return best


# --- size cache ---
def load_sizes():
    if os.path.exists(SIZE_CACHE_FILE):
        try:
            with open(SIZE_CACHE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_sizes(c):
    try:
        with open(SIZE_CACHE_FILE, "w") as f:
            json.dump(c, f)
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


def build_rss(episodes):
    if MAX_ITEMS:
        episodes = episodes[:MAX_ITEMS]
    cache = load_sizes()
    now = format_datetime(datetime.now(timezone.utc))
    p = ['<?xml version="1.0" encoding="UTF-8"?>']
    p.append('<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
             'xmlns:content="http://purl.org/rss/1.0/modules/content/">')
    p.append("<channel>")
    p.append(f"<title>{escape(FEED_TITLE)}</title>")
    p.append(f"<link>{escape(FEED_LINK)}</link>")
    p.append(f"<description>{escape(FEED_DESCRIPTION)}</description>")
    p.append(f"<language>{FEED_LANGUAGE}</language>")
    p.append(f"<lastBuildDate>{now}</lastBuildDate>")
    p.append(f"<itunes:author>{escape(FEED_AUTHOR)}</itunes:author>")
    p.append("<itunes:explicit>false</itunes:explicit>")
    p.append('<itunes:category text="Religion &amp; Spirituality">'
             '<itunes:category text="Christianity"/></itunes:category>')
    for ep in episodes:
        length = get_size(ep["url"], cache)
        p.append("<item>")
        p.append(f"<title>{escape(ep['title'])}</title>")
        p.append(f"<itunes:author>{escape(ep['speaker'])}</itunes:author>")
        p.append(f"<description>{escape(ep['speaker'])}</description>")
        p.append(f'<enclosure url="{escape(ep["url"])}" length="{length}" type="audio/mpeg"/>')
        p.append(f'<guid isPermaLink="true">{escape(ep["url"])}</guid>')
        p.append(f"<pubDate>{format_datetime(ep['date'])}</pubDate>")
        p.append("</item>")
    p.append("</channel></rss>")
    save_sizes(cache)
    return "\n".join(p)


def main():
    episodes = get_episodes()
    if not episodes:
        print("ERROR: no episodes from any source.", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_rss(episodes))
    print(f"Wrote {OUTPUT_FILE} with {len(episodes)} episodes.")
    print(f"NEWEST: {episodes[0]['date'].date()}  {episodes[0]['title']}")
    print(f"OLDEST: {episodes[-1]['date'].date()}  {episodes[-1]['title']}")


if __name__ == "__main__":
    main()

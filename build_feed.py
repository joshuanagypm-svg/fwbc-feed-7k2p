#!/usr/bin/env python3
# build_feed.py  (v4 - builds the feed from a page you saved in your browser)
"""
Build the Faithful Word Baptist Church podcast feed from a *saved copy* of the
sermons page.

Why this exists: the church's website serves automated tools (like GitHub) a
frozen 2014 copy of the page, and the page's HTML also contains broken comment
markers that make strict parsers drop everything after 2014. Your browser sees
the real, current page - so you save it, and this script reads the sermons out
of that saved file. It handles both a normal "Save Page As" file and a
"View Source" saved file, and it reads sermons by pattern so the broken HTML
comments don't matter.

Usage: put your saved page in the same folder as this script (any .html name)
and run it. It writes feed.xml.
"""

import glob
import html as htmllib
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup

OUTPUT_FILE = "feed.xml"
BASE_URL = "https://www.faithfulwordbaptist.org/"

FEED_TITLE = "Faithful Word Baptist Church - Sermons"
FEED_DESCRIPTION = "Sermon audio from Faithful Word Baptist Church. Unofficial personal feed."
FEED_LINK = "https://www.faithfulwordbaptist.org/page5.html"
FEED_AUTHOR = "Faithful Word Baptist Church"


def find_saved_page():
    """Find the saved HTML page in the current folder (largest .html with sermons)."""
    candidates = []
    for path in glob.glob("*.html") + glob.glob("*.htm"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(500000)
        except OSError:
            continue
        if "faithfulwordbaptist" in head.lower() and ".mp3" in head.lower():
            candidates.append((os.path.getsize(path), path))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def load_source(path):
    """Return the page's HTML source, un-wrapping a View-Source save if needed."""
    saved = open(path, encoding="utf-8", errors="replace").read()
    if "line-content" in saved and "line-number" in saved:
        soup = BeautifulSoup(saved, "html.parser")
        cells = soup.select("td.line-content")
        if cells:
            return "\n".join(c.get_text() for c in cells)
    return saved


def date_from_filename(url):
    fn = url.rsplit("/", 1)[-1].lower()
    m = re.search(r"(\d{2})(\d{2})(\d{2})([ap])?\.mp3$", fn)
    if not m:
        return None
    mm, dd, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour = 19 if m.group(4) == "p" else (10 if m.group(4) == "a" else 12)
    try:
        return datetime(2000 + yy, mm, dd, hour, tzinfo=timezone.utc)
    except ValueError:
        return None


def extract(src):
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)  # drop commented-out template rows
    """Pull (date, title, speaker, url) for every sermon, ignoring broken HTML comments."""
    # Tight pattern: title cell immediately followed by the links cell's first mp3,
    # then (optionally) the speaker cell. Anchoring on title->mp3 keeps them paired.
    rec = re.compile(
        r'<td\s+class="prch-title[^"]*">\s*(?P<title>.*?)\s*</td>\s*'
        r'<td>\s*<a\s+href="(?P<url>[^"]*?\.mp3)"'
        r'(?P<mid>.*?)</td>\s*'
        r'<td>\s*(?P<speaker>[^<]*?)\s*</td>',
        re.S | re.I,
    )
    episodes, seen = [], set()
    for m in rec.finditer(src):
        title = re.sub(r"<[^>]+>", "", htmllib.unescape(m.group("title"))).strip()
        url = m.group("url")
        if not url.startswith("http"):
            url = BASE_URL + url.lstrip("/")
        if url in seen:
            continue
        speaker = htmllib.unescape(m.group("speaker")).strip() or FEED_AUTHOR
        date = date_from_filename(url)
        if date is None:
            continue  # a handful of oddly-named files; skip rather than mis-date
        if not title:
            title = f"Sermon {date.date()}"
        seen.add(url)
        episodes.append({"title": title, "speaker": speaker, "url": url, "date": date})
    episodes.sort(key=lambda e: e["date"], reverse=True)
    return episodes


def build_rss(episodes):
    now = format_datetime(datetime.now(timezone.utc))
    p = ['<?xml version="1.0" encoding="UTF-8"?>']
    p.append('<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">')
    p.append("<channel>")
    p.append(f"<title>{escape(FEED_TITLE)}</title>")
    p.append(f"<link>{escape(FEED_LINK)}</link>")
    p.append(f"<description>{escape(FEED_DESCRIPTION)}</description>")
    p.append("<language>en-us</language>")
    p.append(f"<lastBuildDate>{now}</lastBuildDate>")
    p.append(f"<itunes:author>{escape(FEED_AUTHOR)}</itunes:author>")
    p.append("<itunes:explicit>false</itunes:explicit>")
    p.append('<itunes:category text="Religion &amp; Spirituality">'
             '<itunes:category text="Christianity"/></itunes:category>')
    for e in episodes:
        p.append("<item>")
        p.append(f"<title>{escape(e['title'])}</title>")
        p.append(f"<itunes:author>{escape(e['speaker'])}</itunes:author>")
        p.append(f"<description>{escape(e['speaker'])}</description>")
        p.append(f'<enclosure url="{escape(e["url"])}" length="0" type="audio/mpeg"/>')
        p.append(f'<guid isPermaLink="true">{escape(e["url"])}</guid>')
        p.append(f"<pubDate>{format_datetime(e['date'])}</pubDate>")
        p.append("</item>")
    p.append("</channel></rss>")
    return "\n".join(p)


def main():
    path = find_saved_page()
    if not path:
        print("ERROR: no saved sermons page (.html) found in this folder.", file=sys.stderr)
        sys.exit(1)
    print(f"Reading saved page: {path}")
    episodes = extract(load_source(path))
    if not episodes:
        print("ERROR: no sermons found in the saved page.", file=sys.stderr)
        sys.exit(1)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(build_rss(episodes))
    print(f"Wrote {OUTPUT_FILE} with {len(episodes)} episodes.")
    print(f"NEWEST: {episodes[0]['date'].date()}  {episodes[0]['title']}")
    print(f"OLDEST: {episodes[-1]['date'].date()}  {episodes[-1]['title']}")


if __name__ == "__main__":
    main()

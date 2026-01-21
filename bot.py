import os
import re
import time
import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

BASE_URL = "https://www.royal.uk/media-centre/future-engagements"

# More browser-like headers (often helps with 403s)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.royal.uk/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


@dataclass(frozen=True)
class Engagement:
    date: dt.date
    who: str
    text: str


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def fetch(url: str, retries: int = 4) -> str:
    """
    Fetch a page with a few retries + backoff.
    """
    last_err = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
            return r.text
        except requests.exceptions.RequestException as e:
            last_err = e
            # Backoff: 1s, 2s, 4s, 8s...
            time.sleep(2 ** attempt)
    raise last_err  # type: ignore


def parse_page(html: str) -> List[Engagement]:
    """
    Parse the Future engagements page.

    The page generally follows:
      DATE (e.g., "22 January 2026")
      HEADING (royal family member)
      PARAGRAPH (engagement description)
    repeated.
    """
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup

    elements = main.find_all(["h2", "h3", "p", "div", "span"], recursive=True)
    date_re = re.compile(r"^\d{1,2}\s+[A-Za-z]+\s+\d{4}$")

    current_date: Optional[dt.date] = None
    current_who: Optional[str] = None
    out: List[Engagement] = []

    for el in elements:
        t = clean(el.get_text(" ", strip=True))
        if not t:
            continue

        # Detect a date line
        if date_re.match(t):
            try:
                current_date = dateparser.parse(t, dayfirst=True).date()
            except Exception:
                current_date = None
            continue

        # Detect "who" in headings (only if we have a current date)
        if el.name in ("h2", "h3") and current_date:
            current_who = t
            continue

        # Detect description block after date + who
        if current_date and current_who and el.name in ("p", "div", "span"):
            # Filter out obvious noise
            if "Pagination" in t or "page" == t.lower():
                continue

            # Require some length so we don’t capture nav/footer crumbs
            if len(t) >= 20:
                out.append(Engagement(
                    date=current_date,
                    who=current_who,
                    text=t.rstrip(".") + "."
                ))
                current_who = None  # reset after capturing one description

    return out


def scrape_all(max_pages: int = 10, polite_sleep_s: float = 1.0) -> List[Engagement]:
    """
    Scrape page 0..N until no items appear or we hit max_pages.
    """
    items: List[Engagement] = []

    for page in range(max_pages):
        url = BASE_URL if page == 0 else f"{BASE_URL}?page={page}"
        html = fetch(url)
        page_items = parse_page(html)

        if not page_items:
            break

        items.extend(page_items)
        time.sleep(polite_sleep_s)

    # Deduplicate (date, who, text)
    uniq = {(i.date, i.who, i.text): i for i in items}
    return sorted(uniq.values(), key=lambda x: (x.date, x.who, x.text))


def group_for_week(items: List[Engagement], start: dt.date, end: dt.date) -> Dict[str, List[str]]:
    """
    Return grouped text lines keyed by formatted date.
    """
    week = [i for i in items if start <= i.date <= end]
    grouped: Dict[str, List[str]] = {}
    for i in week:
        k = i.date.strftime("%A, %d %B %Y")
        grouped.setdefault(k, []).append(f"**{i.who}:** {i.text}")
    return grouped


def chunk_text(text: str, max_len: int = 3800) -> List[str]:
    """
    Discord embed description limit is 4096 chars.
    Keep some headroom for safety.
    """
    chunks: List[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > max_len:
            if buf.strip():
                chunks.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        chunks.append(buf.rstrip())
    return chunks


def post_discord_embed(webhook_url: str, title: str, description: str) -> None:
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "url": BASE_URL
        }]
    }
    r = requests.post(webhook_url, json=payload, timeout=30)
    r.raise_for_status()


def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        raise SystemExit("Missing DISCORD_WEBHOOK_URL environment variable / secret.")

    today = dt.date.today()
    end = today + dt.timedelta(days=7)

    items = scrape_all(max_pages=10, polite_sleep_s=1.0)
    grouped = group_for_week(items, today, end)

    title = f"Royal Diary — {today:%d %b} to {end:%d %b %Y}"

    if not grouped:
        post_discord_embed(
            webhook,
            title,
            f"No listed engagements found for {today:%d %b %Y}–{end:%d %b %Y}.\n\nSource: {BASE_URL}"
        )
        return

    lines: List[str] = []
    for day in sorted(grouped.keys(), key=lambda d: dateparser.parse(d, dayfirst=True)):
        lines.append(f"__**{day}**__")
        for item in grouped[day]:
            lines.append(f"• {item}")
        lines.append("")

    full = "\n".join(lines).strip() + f"\n\nSource: {BASE_URL}"

    for part in chunk_text(full):
        post_discord_embed(webhook, title, part)


if __name__ == "__main__":
    main()

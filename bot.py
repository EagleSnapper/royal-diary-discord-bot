import os
import re
import time
import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

BASE_URL = "https://www.royal.uk/media-centre/future-engagements"
HEADERS = {"User-Agent": "RoyalDiaryWeeklyBot/1.0"}

@dataclass(frozen=True)
class Engagement:
    date: dt.date
    who: str
    text: str

def fetch(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def parse_page(html: str) -> List[Engagement]:
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

        if date_re.match(t):
            try:
                current_date = dateparser.parse(t, dayfirst=True).date()
            except Exception:
                current_date = None
            continue

        if el.name in ("h2", "h3") and current_date:
            current_who = t
            continue

        if current_date and current_who and el.name in ("p", "div", "span"):
            if len(t) >= 20:
                out.append(Engagement(current_date, current_who, t.rstrip(".") + "."))
                current_who = None

    return out

def scrape_all() -> List[Engagement]:
    items: List[Engagement] = []
    for page in range(10):
        url = BASE_URL if page == 0 else f"{BASE_URL}?page={page}"
        page_items = parse_page(fetch(url))
        if not page_items:
            break
        items.extend(page_items)
        time.sleep(1)

    uniq = {(i.date, i.who, i.text): i for i in items}
    return list(uniq.values())

def post_to_discord(webhook: str, title: str, description: str):
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "url": BASE_URL
        }]
    }
    requests.post(webhook, json=payload, timeout=30)

def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        return

    today = dt.date.today()
    end = today + dt.timedelta(days=7)

    items = scrape_all()
    week = [i for i in items if today <= i.date <= end]

    if not week:
        post_to_discord(
            webhook,
            "Royal Diary – Weekly Update",
            "No engagements listed for the coming week."
        )
        return

    lines = []
    current = None
    for i in sorted(week, key=lambda x: x.date):
        if i.date != current:
            current = i.date
            lines.append(f"__**{current:%A, %d %B %Y}**__")
        lines.append(f"• **{i.who}:** {i.text}")

    post_to_discord(
        webhook,
        f"Royal Diary – {today:%d %b} to {end:%d %b %Y}",
        "\n".join(lines)
    )

if __name__ == "__main__":
    main()

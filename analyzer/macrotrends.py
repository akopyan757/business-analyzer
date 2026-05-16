"""Scrape macrotrends 10+ year history. Best-effort — site is brittle, falls back gracefully.

Since macrotrends layout requires the company slug (e.g. /AAPL/apple/...), we resolve it
via the front-page search and then fetch the revenue/margin tables.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .config import CACHE_DIR

UA = {"User-Agent": "Mozilla/5.0 (compatible; BusinessAnalyzer/1.0)"}
BASE = "https://www.macrotrends.net"


def _cache(*parts: str) -> Path:
    p = CACHE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def resolve_slug(ticker: str) -> Optional[str]:
    """Find macrotrends slug for ticker via the public stock-screener-list."""
    cache = _cache("macrotrends_slugs.json")
    slugs: dict[str, str] = {}
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 30 * 86400:
        slugs = json.loads(cache.read_text())
    if ticker.upper() in slugs:
        return slugs[ticker.upper()]

    # Try the simple known URL pattern by probing the redirect from /stocks/charts/{T}/
    url = f"{BASE}/stocks/charts/{ticker.upper()}/x/revenue"
    try:
        r = requests.get(url, headers=UA, allow_redirects=True, timeout=20)
        if r.status_code == 200:
            m = re.search(r"/stocks/charts/([A-Z\.\-]+)/([a-z0-9\-]+)/", r.url)
            if m:
                slug = m.group(2)
                slugs[ticker.upper()] = slug
                cache.write_text(json.dumps(slugs))
                return slug
    except Exception:
        return None
    return None


def _fetch_page(ticker: str, slug: str, metric: str) -> Optional[str]:
    cache = _cache("macrotrends", f"{ticker.upper()}_{metric}.html")
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 7 * 86400:
        return cache.read_text(encoding="utf-8", errors="ignore")
    url = f"{BASE}/stocks/charts/{ticker.upper()}/{slug}/{metric}"
    try:
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200:
            return None
        cache.write_text(r.text, encoding="utf-8")
        time.sleep(0.5)
        return r.text
    except Exception:
        return None


def _parse_history_table(html: str) -> pd.DataFrame:
    """macrotrends pages embed history JSON inside a <script> var originalData."""
    m = re.search(r"var originalData = (\[.*?\]);", html, re.S)
    if not m:
        return pd.DataFrame()
    try:
        data = json.loads(m.group(1))
    except Exception:
        return pd.DataFrame()
    rows = []
    for d in data:
        date = d.get("date") or d.get("field_name")
        # Each row may have many fiscal-year keys. We pull date+v1.
        for k, v in d.items():
            if k in ("date", "field_name"):
                continue
            if isinstance(v, str) and v.startswith("<"):
                # often value embedded inside td html, strip
                v = re.sub(r"<[^>]+>", "", v)
            rows.append({"date": date, "key": k, "value": v})
    return pd.DataFrame(rows)


def fetch_history(ticker: str) -> dict[str, pd.Series]:
    """Return {metric: Series indexed by date}."""
    slug = resolve_slug(ticker)
    if not slug:
        return {}
    out: dict[str, pd.Series] = {}
    metrics = {
        "revenue": "revenue",
        "gross_profit": "gross-profit",
        "operating_income": "operating-income",
        "net_income": "net-income",
        "fcf": "free-cash-flow",
    }
    for col, slug_metric in metrics.items():
        html = _fetch_page(ticker, slug, slug_metric)
        if not html:
            continue
        # Easier path: parse the visible <table> by header
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table.historical_data_table")
        if not table:
            continue
        rows = []
        for tr in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", cells[0]):
                val = cells[1].replace("$", "").replace(",", "")
                try:
                    rows.append((cells[0], float(val)))
                except ValueError:
                    pass
        if rows:
            s = pd.Series({pd.Timestamp(d): v for d, v in rows}).sort_index()
            out[col] = s
    return out


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    h = fetch_history(t)
    for k, s in h.items():
        print(k, len(s), "points,", "last:", s.tail(3).to_dict())

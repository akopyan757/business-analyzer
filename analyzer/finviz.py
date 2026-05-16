"""Scrape finviz quote snapshot: multiples, target price, recommendation, segment hints."""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .config import CACHE_DIR

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36"}
URL = "https://finviz.com/quote.ashx?t={ticker}"


def fetch(ticker: str, max_age_hours: int = 12) -> dict:
    cache = CACHE_DIR / "finviz" / f"{ticker.upper()}.html"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists() and (time.time() - cache.stat().st_mtime) < max_age_hours * 3600:
        html = cache.read_text(encoding="utf-8", errors="ignore")
    else:
        r = requests.get(URL.format(ticker=ticker.upper()), headers=UA, timeout=30)
        if r.status_code != 200:
            return {}
        html = r.text
        cache.write_text(html, encoding="utf-8")
        time.sleep(0.5)

    soup = BeautifulSoup(html, "lxml")
    out: dict[str, str] = {}
    table = soup.select_one("table.snapshot-table2")
    if table:
        cells = table.find_all("td")
        for i in range(0, len(cells) - 1, 2):
            key = cells[i].get_text(strip=True)
            val = cells[i + 1].get_text(strip=True)
            out[key] = val

    # Sector / Industry / Country come from header tab-links
    for a in soup.select("a.tab-link"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if "sec_" in href and "Sector" not in out:
            out["Sector"] = text
        elif "ind_" in href and "Industry" not in out:
            out["Industry"] = text
        elif "geo_" in href and "Country" not in out:
            out["Country"] = text
    return out


def _parse_num(v: Optional[str]) -> Optional[float]:
    if not v or v in ("-", "N/A"):
        return None
    v = v.strip()
    mult = 1.0
    if v.endswith("%"):
        try:
            return float(v[:-1]) / 100
        except ValueError:
            return None
    if v.endswith("B"):
        mult, v = 1e9, v[:-1]
    elif v.endswith("M"):
        mult, v = 1e6, v[:-1]
    elif v.endswith("K"):
        mult, v = 1e3, v[:-1]
    elif v.endswith("T"):
        mult, v = 1e12, v[:-1]
    try:
        return float(v.replace(",", "")) * mult
    except ValueError:
        return None


def parse_snapshot(d: dict) -> dict:
    """Normalize finviz snapshot into typed dict."""
    return {
        "price": _parse_num(d.get("Price")),
        "market_cap": _parse_num(d.get("Market Cap")),
        "sector": d.get("Sector"),
        "industry": d.get("Industry"),
        "country": d.get("Country"),
        "pe": _parse_num(d.get("P/E")),
        "forward_pe": _parse_num(d.get("Forward P/E")),
        "ps": _parse_num(d.get("P/S")),
        "pb": _parse_num(d.get("P/B")),
        "pfcf": _parse_num(d.get("P/FCF")),
        "ev_ebitda": _parse_num(d.get("EV/EBITDA")),
        "roe": _parse_num(d.get("ROE")),
        "roi": _parse_num(d.get("ROI")),
        "debt_eq": _parse_num(d.get("Debt/Eq")),
        "dividend_yield": _parse_num(d.get("Dividend Yield") or d.get("Dividend %")),
        "target_price": _parse_num(d.get("Target Price")),
        "recom": _parse_num(d.get("Recom")),  # 1=Buy ... 5=Sell
        "beta": _parse_num(d.get("Beta")),
        "perf_ytd": _parse_num(d.get("Perf YTD")),
        "perf_year": _parse_num(d.get("Perf Year")),
        "raw": d,
    }


def recom_label(score: Optional[float]) -> str:
    if score is None:
        return "—"
    if score <= 1.5: return "Strong Buy"
    if score <= 2.5: return "Buy"
    if score <= 3.5: return "Hold"
    if score <= 4.5: return "Sell"
    return "Strong Sell"


if __name__ == "__main__":
    import json, sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    raw = fetch(t)
    parsed = parse_snapshot(raw)
    print(json.dumps({k: v for k, v in parsed.items() if k != "raw"}, indent=2, default=str))

"""SEC EDGAR client: ticker→CIK, latest 10-K / 10-Q filings, raw HTML download."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from .config import CACHE_DIR, SEC_HEADERS

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}"


@dataclass
class Filing:
    form: str            # "10-K" or "10-Q"
    accession: str       # "0000320193-24-000123"
    filing_date: str     # "2024-11-01"
    period_of_report: str
    primary_doc: str     # filename of the main HTML
    cik: str             # zero-padded 10 chars

    @property
    def primary_url(self) -> str:
        nodash = self.accession.replace("-", "")
        return f"{ARCHIVE_URL.format(cik_int=int(self.cik), accession_nodash=nodash)}/{self.primary_doc}"

    @property
    def filing_index_url(self) -> str:
        nodash = self.accession.replace("-", "")
        return f"{ARCHIVE_URL.format(cik_int=int(self.cik), accession_nodash=nodash)}/"


def _sleep():
    time.sleep(0.12)


def _cache_path(*parts: str) -> Path:
    p = CACHE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ticker_to_cik(ticker: str) -> Optional[str]:
    """Return zero-padded 10-char CIK for ticker, or None."""
    cache = _cache_path("ticker_map.json")
    if not cache.exists():
        r = requests.get(TICKER_MAP_URL, headers=SEC_HEADERS, timeout=30)
        r.raise_for_status()
        cache.write_text(r.text)
        _sleep()
    data = json.loads(cache.read_text())
    upper = ticker.upper()
    for row in data.values():
        if row["ticker"].upper() == upper:
            return str(row["cik_str"]).zfill(10)
    return None


def _submissions(cik: str) -> dict:
    cache = _cache_path(cik, "submissions.json")
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 24 * 3600:
        return json.loads(cache.read_text())
    r = requests.get(SUBMISSIONS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    cache.write_text(r.text)
    _sleep()
    return json.loads(cache.read_text())


def get_latest_filings(ticker: str) -> dict[str, Optional[Filing]]:
    """Return dict {'10-K': Filing|None, '10-Q': Filing|None} for ticker."""
    cik = ticker_to_cik(ticker)
    if not cik:
        raise ValueError(f"Unknown ticker: {ticker}")
    subs = _submissions(cik)
    recent = subs["filings"]["recent"]
    out: dict[str, Optional[Filing]] = {"10-K": None, "10-Q": None}
    for i, form in enumerate(recent["form"]):
        if form not in out or out[form] is not None:
            continue
        out[form] = Filing(
            form=form,
            accession=recent["accessionNumber"][i],
            filing_date=recent["filingDate"][i],
            period_of_report=recent["reportDate"][i],
            primary_doc=recent["primaryDocument"][i],
            cik=cik,
        )
        if all(out.values()):
            break
    return out


def download_filing_html(filing: Filing) -> str:
    """Return the main 10-K/10-Q HTML, cached on disk."""
    cache = _cache_path(filing.cik, filing.accession.replace("-", ""), filing.primary_doc)
    if cache.exists():
        return cache.read_text(encoding="utf-8", errors="ignore")
    r = requests.get(filing.primary_url, headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    cache.write_text(r.text, encoding="utf-8")
    _sleep()
    return r.text


def company_name(cik: str) -> str:
    return _submissions(cik).get("name", "")


def sic_info(cik: str) -> tuple[str, str]:
    s = _submissions(cik)
    return s.get("sic", ""), s.get("sicDescription", "")


if __name__ == "__main__":
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    cik = ticker_to_cik(t)
    print(f"{t} → CIK {cik} ({company_name(cik)})")
    f = get_latest_filings(t)
    for form, fil in f.items():
        if fil:
            print(f"  {form}: {fil.accession} filed {fil.filing_date} ({fil.period_of_report})")
            print(f"    {fil.primary_url}")

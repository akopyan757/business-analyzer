"""Parse SEC EDGAR XBRL companyfacts into annual & quarterly financials.

Output is a normalized pandas DataFrame indexed by period_end date with columns:
    revenue, cogs, gross_profit, opex, operating_income, net_income,
    rd_expense, sga_expense, fcf, total_assets, cash, total_debt, equity
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from .config import CACHE_DIR, SEC_HEADERS

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# (column, [candidate us-gaap tags in priority order])
TAG_MAP: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "cogs": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ],
    "gross_profit": ["GrossProfit"],
    "opex": ["OperatingExpenses"],
    "rd_expense": ["ResearchAndDevelopmentExpense"],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
        # Fallbacks для компаний которые не отчитываются стандартным тегом
        # (фарма, услуги, иногда финансовые)
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "total_assets": ["Assets"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "total_debt_long": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "total_debt_short": [
        "ShortTermBorrowings",
        "DebtCurrent",
        "LongTermDebtCurrent",
        "ShortTermBankLoansAndNotesPayable",
    ],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
    ],
}


def _cache_path(*parts: str) -> Path:
    p = CACHE_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_company_facts(cik: str) -> dict:
    cache = _cache_path(cik, "companyfacts.json")
    if cache.exists() and (time.time() - cache.stat().st_mtime) < 24 * 3600:
        return json.loads(cache.read_text())
    r = requests.get(FACTS_URL.format(cik=cik), headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    cache.write_text(r.text)
    time.sleep(0.12)
    return json.loads(cache.read_text())


def _pick_facts_all(facts: dict, tags: list[str]) -> list[tuple[int, dict]]:
    """Collect rows from ALL candidate tags, tagged with priority (lower = higher priority).
    Caller can later prefer higher-priority tag rows but fall back to lower-priority
    when the preferred tag has no data for that period.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    out: list[tuple[int, dict]] = []
    for priority, tag in enumerate(tags):
        if tag not in us_gaap:
            continue
        units = us_gaap[tag].get("units", {})
        pool = units.get("USD") or units.get("USD/shares") or units.get("shares") or next(iter(units.values()), [])
        for r in pool:
            out.append((priority, r))
    return out


def _series_from_facts(facts: dict, tags: list[str], periodicity: str) -> pd.Series:
    """periodicity: 'FY' (annual) or 'Q' (quarterly).

    For instant facts (balance sheet), we ignore periodicity and use latest per fiscal year/quarter.
    """
    raws = _pick_facts_all(facts, tags)
    rows = []
    for priority, r in raws:
        end = r.get("end")
        fp = r.get("fp")           # FY, Q1, Q2, Q3
        form = r.get("form", "")
        val = r.get("val")
        start = r.get("start")
        if end is None or val is None:
            continue
        rows.append({"end": end, "start": start, "fp": fp, "form": form, "val": val, "_p": priority})
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows)
    df["end"] = pd.to_datetime(df["end"])
    if "start" in df.columns:
        df["start"] = pd.to_datetime(df["start"], errors="coerce")
        df["duration_days"] = (df["end"] - df["start"]).dt.days
    else:
        df["duration_days"] = None

    if periodicity == "FY":
        mask = (df["fp"] == "FY") & df["form"].str.startswith("10-K")
        sub = df[mask]
        # Filter duration facts to ~year (340-380 days). Instant facts have NaN duration → keep all.
        if sub["duration_days"].notna().any():
            year_mask = sub["duration_days"].isna() | sub["duration_days"].between(340, 380)
            year_sub = sub[year_mask]
            if not year_sub.empty:
                sub = year_sub
        if sub.empty:
            sub = df[df["form"].str.startswith("10-K")]
    else:
        # quarterly: include 10-Q (Q1/Q2/Q3) AND 10-K Q4 derivation if possible.
        # For flow facts prefer rows with duration ~90 days. For instant facts just take all.
        if df["duration_days"].notna().any():
            sub = df[(df["duration_days"] >= 80) & (df["duration_days"] <= 100)]
        else:
            sub = df

    # На каждую дату оставляем строку с наивысшим приоритетом тега (минимальный _p),
    # а среди них — самую свежую (последний `filed`-update).
    sub = sub.sort_values(["_p", "end"]).drop_duplicates("end", keep="first")
    sub = sub.sort_values("end")
    return pd.Series(sub["val"].values, index=sub["end"].values)


def build_financials(cik: str, periodicity: str = "FY") -> pd.DataFrame:
    """Build a wide DataFrame indexed by period_end."""
    facts = get_company_facts(cik)
    cols = {}
    for col, tags in TAG_MAP.items():
        s = _series_from_facts(facts, tags, periodicity)
        if not s.empty:
            cols[col] = s
    df = pd.DataFrame(cols).sort_index()

    # Derive gross_profit — заполняем NaN если тег устарел (типа NFLX где GP только до 2020)
    if {"revenue", "cogs"} <= set(df.columns):
        derived_gp = df["revenue"] - df["cogs"]
        if "gross_profit" in df.columns:
            df["gross_profit"] = df["gross_profit"].where(df["gross_profit"].notna(), derived_gp)
        else:
            df["gross_profit"] = derived_gp
    # Derive cogs if missing (rev - gp)
    if "cogs" not in df.columns and {"revenue", "gross_profit"} <= set(df.columns):
        df["cogs"] = df["revenue"] - df["gross_profit"]
    # Derive opex if missing or zero — many companies (KO, PG) don't report OperatingExpenses,
    # only SG&A + R&D separately. opex = GP - OI is always correct identity.
    if {"gross_profit", "operating_income"} <= set(df.columns):
        derived_opex = df["gross_profit"] - df["operating_income"]
        if "opex" not in df.columns:
            df["opex"] = derived_opex
        else:
            df["opex"] = df["opex"].where(df["opex"].notna() & (df["opex"] > 0), derived_opex)

    # Total debt
    if "total_debt_long" in df.columns or "total_debt_short" in df.columns:
        long_d = df["total_debt_long"].fillna(0) if "total_debt_long" in df.columns else 0
        short_d = df["total_debt_short"].fillna(0) if "total_debt_short" in df.columns else 0
        df["total_debt"] = long_d + short_d

    # FCF = CFO - CapEx
    if {"cfo", "capex"} <= set(df.columns):
        df["fcf"] = df["cfo"] - df["capex"]

    return df


@dataclass
class WaterfallRow:
    revenue: float
    cogs: float
    gross_profit: float
    opex: float
    operating_income: float
    tax_and_interest: float  # revenue - cogs - opex - net_income (residual)
    net_income: float
    period_end: str


def latest_waterfall(df: pd.DataFrame) -> Optional[WaterfallRow]:
    if df.empty or "revenue" not in df.columns or "net_income" not in df.columns:
        return None
    row = df.dropna(subset=["revenue", "net_income"]).iloc[-1]
    rev = float(row["revenue"])
    cogs = float(row.get("cogs", 0) or 0)
    gp = float(row.get("gross_profit", rev - cogs) or (rev - cogs))
    opex = float(row.get("opex", 0) or 0)
    oi = float(row.get("operating_income", gp - opex) or (gp - opex))
    ni = float(row["net_income"])
    tax_int = oi - ni
    return WaterfallRow(
        revenue=rev, cogs=cogs, gross_profit=gp, opex=opex,
        operating_income=oi, tax_and_interest=tax_int, net_income=ni,
        period_end=str(row.name)[:10],
    )


def compute_metrics(annual: pd.DataFrame) -> dict[str, float]:
    """Compute the 5 moat metrics from the annual DataFrame."""
    out: dict[str, float] = {}
    if annual.empty:
        return out
    last = annual.iloc[-1]
    rev = last.get("revenue")
    if rev and rev > 0:
        if pd.notna(last.get("gross_profit")):
            out["gm"] = float(last["gross_profit"]) / float(rev)
        if pd.notna(last.get("operating_income")):
            out["om"] = float(last["operating_income"]) / float(rev)
        if pd.notna(last.get("net_income")):
            out["nm"] = float(last["net_income"]) / float(rev)
        if pd.notna(last.get("fcf")):
            out["fcf_margin"] = float(last["fcf"]) / float(rev)

    # ROIC ≈ NOPAT / (Equity + Debt − Cash)
    if all(c in annual.columns for c in ("operating_income", "equity")):
        nopat = float(last["operating_income"]) * (1 - 0.21)
        invested = float(last.get("equity", 0) or 0) + float(last.get("total_debt", 0) or 0) - float(last.get("cash", 0) or 0)
        if invested > 0:
            out["roic"] = nopat / invested

    # Revenue CAGR 5Y
    if "revenue" in annual.columns and len(annual["revenue"].dropna()) >= 6:
        rev_series = annual["revenue"].dropna()
        first = float(rev_series.iloc[-6])
        last_v = float(rev_series.iloc[-1])
        if first > 0 and last_v > 0:
            out["rev_cagr_5y"] = (last_v / first) ** (1 / 5) - 1

    # GM trend (5Y slope)
    if "gross_profit" in annual.columns and "revenue" in annual.columns:
        gm = (annual["gross_profit"] / annual["revenue"]).dropna().tail(5)
        if len(gm) >= 3:
            import numpy as np
            x = np.arange(len(gm))
            slope = np.polyfit(x, gm.values, 1)[0]
            out["gm_trend"] = float(slope)

    return out


if __name__ == "__main__":
    import sys
    from .edgar import ticker_to_cik
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    cik = ticker_to_cik(t)
    df = build_financials(cik, "FY")
    print(df.tail(6).to_string())
    print("\nMetrics:", compute_metrics(df))
    print("\nWaterfall:", latest_waterfall(df))

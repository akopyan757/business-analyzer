"""Extract revenue-by-segment from XBRL companyfacts (using us-gaap dimension axes)."""
from __future__ import annotations

import pandas as pd

from .xbrl import get_company_facts


REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]


def latest_segments(cik: str) -> dict[str, float]:
    """Return {segment_name: revenue} for the most recent FY where segments are tagged.

    XBRL stores segment breakdowns via 'dimensions' / 'segment' members, but the
    companyfacts API gives us facts without explicit dimensions. We approximate
    by detecting facts that share the latest 'end' date and sum below total
    revenue — these are the per-segment components disclosed in the same period.
    """
    facts = get_company_facts(cik).get("facts", {}).get("us-gaap", {})
    total_rev = None
    for tag in REVENUE_TAGS:
        if tag in facts and "USD" in facts[tag].get("units", {}):
            rows = facts[tag]["units"]["USD"]
            fy_rows = [r for r in rows if r.get("fp") == "FY" and r["form"].startswith("10-K")]
            if not fy_rows:
                continue
            # Most recent FY end date
            latest_end = max(r["end"] for r in fy_rows)
            same_period = [r for r in fy_rows if r["end"] == latest_end]
            # The total revenue for that period: pick the row with max value
            same_period.sort(key=lambda r: r["val"], reverse=True)
            if not same_period:
                continue
            total_rev = same_period[0]["val"]
            # Other rows at same end date with smaller value are likely segment components
            sub_components = same_period[1:]
            # Heuristic: only keep components whose sum is within ±15% of total
            if sub_components:
                summed = sum(r["val"] for r in sub_components)
                if total_rev > 0 and abs(summed - total_rev) / total_rev < 0.25:
                    # We don't have segment names from companyfacts. Return as "Segment N".
                    return {f"Segment {i+1}": r["val"] for i, r in enumerate(sub_components)}
            return {}
    return {}

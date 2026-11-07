"""SEC EDGAR XBRL companyfacts extraction.

Flow metrics (revenue, income, cash flow) are taken from 10-K / FY rows with
an annual duration (~300-400 days); balance metrics from instant rows.
Duplicates across filings (restatements) are resolved by keeping, per fiscal
year-end, the row from the most recently filed document.
"""
from datetime import date

from .util import fetch_json

BALANCE_METRICS = {"cash", "investments_current", "investments_noncurrent",
                   "total_debt", "equity"}
UNIT_PREFERENCE = {
    "diluted_shares": ["shares"],
    "eps_diluted": ["USD/shares"],
}


def _parse_day(s):
    y, m, d = (int(p) for p in s.split("-"))
    return date(y, m, d)


def annual_series(facts, candidates, metric):
    """Try every candidate concept and keep the FRESHEST series (max latest
    fiscal year-end). Companies change tags over time — e.g. NVDA's total
    revenue moved from RevenueFromContract... to Revenues — so first-found
    can be years stale."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    best = []
    for concept in candidates:
        node = gaap.get(concept)
        if not node:
            continue
        units = node.get("units", {})
        pref = UNIT_PREFERENCE.get(metric, ["USD"])
        unit_key = next((u for u in pref if u in units), None) or (list(units) or [None])[0]
        if unit_key is None:
            continue
        by_end = {}
        for row in units[unit_key]:
            if row.get("form") != "10-K" or row.get("fp") != "FY":
                continue
            if metric in BALANCE_METRICS:
                if row.get("start"):
                    continue
            else:
                if not row.get("start"):
                    continue
                dur = (_parse_day(row["end"]) - _parse_day(row["start"])).days
                if not (300 <= dur <= 400):
                    continue
            end = row["end"]
            if end not in by_end or row.get("filed", "") > by_end[end].get("filed", ""):
                by_end[end] = row
        if by_end:
            series = [{"end": e, "value": by_end[e]["val"], "filed": by_end[e].get("filed")}
                      for e in sorted(by_end)]
            if not best or series[-1]["end"] > best[-1]["end"]:
                best = series
    return best


def get_company_metrics(sources_cfg, cik):
    src = sources_cfg["sources"]
    url = src["edgar_companyfacts_url"].format(cik=cik)
    facts = fetch_json(url, src["user_agent"], src["timeout_seconds"],
                       src["request_delay_seconds"])
    series = {}
    for metric, candidates in sources_cfg["concepts"].items():
        series[metric] = annual_series(facts, candidates, metric)
    return facts.get("entityName"), series

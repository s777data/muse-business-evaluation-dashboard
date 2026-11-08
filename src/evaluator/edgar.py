"""SEC EDGAR XBRL companyfacts extraction.

Annual series (used for through-cycle medians, growth history and the DCF
base) are taken from 10-K / FY rows with an annual duration (~300-400
days); duplicates across filings (restatements) are resolved by keeping,
per fiscal year-end, the row from the most recently filed document.

For TRAILING-TWELVE-MONTH multiples the annual figure alone is stale
whenever interim 10-Qs have been filed since the fiscal year-end, so this
module also returns, per flow metric, every filed duration row of the
chosen concept ("_periods") and, per balance metric, the latest instant
row across 10-K and 10-Q ("_balance_latest"). metrics.py builds true TTM
values from those rows: latest annual + current-FY year-to-date - same
period a year earlier. (Summing the last four quarters is NOT robust: a
fiscal Q4 is often never filed as a standalone quarter.)
"""
from datetime import date

from .util import fetch_json

BALANCE_METRICS = {"cash", "investments_current", "investments_noncurrent",
                   "total_debt", "equity"}
UNIT_PREFERENCE = {
    "diluted_shares": ["shares"],
    "eps_diluted": ["USD/shares"],
}
PERIOD_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def _parse_day(s):
    y, m, d = (int(p) for p in s.split("-"))
    return date(y, m, d)


def _unit_rows(facts, concept, metric):
    """Raw XBRL rows for one concept in the preferred unit, or []."""
    node = facts.get("facts", {}).get("us-gaap", {}).get(concept)
    if not node:
        return []
    units = node.get("units", {})
    pref = UNIT_PREFERENCE.get(metric, ["USD"])
    unit_key = next((u for u in pref if u in units), None) or (list(units) or [None])[0]
    if unit_key is None:
        return []
    return units[unit_key]


def _annual_from_rows(rows, metric):
    """Annual series from raw rows: 10-K / FY only, annual duration for
    flow metrics, instant rows for balance metrics."""
    by_end = {}
    for row in rows:
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
    return [{"end": e, "value": by_end[e]["val"], "filed": by_end[e].get("filed")}
            for e in sorted(by_end)]


def _period_rows(rows):
    """Every duration row (10-K/10-Q, incl. amendments), deduplicated by
    (start, end) keeping the most recently filed value."""
    by_key = {}
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or not row.get("start"):
            continue
        key = (row["start"], row["end"])
        if key not in by_key or row.get("filed", "") > by_key[key].get("filed", ""):
            by_key[key] = row
    return [{"start": s, "end": e, "value": by_key[(s, e)]["val"],
             "filed": by_key[(s, e)].get("filed")} for (s, e) in sorted(by_key)]


def _latest_instant(rows):
    """Latest balance-sheet instant row across 10-K and 10-Q: max period
    end, then most recently filed."""
    best = None
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or row.get("start"):
            continue
        if best is None or (row["end"], row.get("filed", "")) > (best["end"], best.get("filed", "")):
            best = row
    return {"end": best["end"], "value": best["val"], "filed": best.get("filed")} if best else None


def _instants_by_end(rows):
    """Every instant row across 10-K and 10-Q as {end: value},
    deduplicated per period end keeping the most recently filed value."""
    by_end = {}
    for row in rows:
        if row.get("form") not in PERIOD_FORMS or row.get("start"):
            continue
        end = row["end"]
        if end not in by_end or row.get("filed", "") > by_end[end].get("filed", ""):
            by_end[end] = row
    return {e: by_end[e]["val"] for e in sorted(by_end)}


# Debt is resolved in metrics.resolve_total_debt from instant rows of ALL
# debt concepts, grouped by role (see config/sources.yaml for the priority
# rules). These config keys are component lists, not standalone metrics,
# so the main per-metric loop below skips them.
# v4: debt lives in FAMILIES that coexist on one balance sheet — the
# bank/debt family (total_debt_current / total_debt_noncurrent) and the
# notes-payable family (total_notes_current / total_notes_noncurrent) —
# plus carrying-amount GRAND TOTALS (total_debt_grand_total) that replace
# all components when present.
DEBT_GROUPS = (("totals", "total_debt"),
               ("grand_totals", "total_debt_grand_total"))
DEBT_FAMILIES = (("bank_debt", "total_debt_current", "total_debt_noncurrent"),
                 ("notes_payable", "total_notes_current", "total_notes_noncurrent"))
DEBT_COMPONENT_KEYS = {"total_debt_current", "total_debt_noncurrent",
                       "total_notes_current", "total_notes_noncurrent",
                       "total_debt_grand_total"}


def annual_series(facts, candidates, metric):
    """Try every candidate concept and keep the FRESHEST series (max latest
    fiscal year-end). Companies change tags over time — e.g. NVDA's total
    revenue moved from RevenueFromContract... to Revenues — so first-found
    can be years stale."""
    best = []
    for concept in candidates:
        series = _annual_from_rows(_unit_rows(facts, concept, metric), metric)
        if series and (not best or series[-1]["end"] > best[-1]["end"]):
            best = series
    return best


def get_company_metrics(sources_cfg, cik):
    src = sources_cfg["sources"]
    url = src["edgar_companyfacts_url"].format(cik=cik)
    facts = fetch_json(url, src["user_agent"], src["timeout_seconds"],
                       src["request_delay_seconds"])
    series, periods, balance_latest = {}, {}, {}
    for metric, candidates in sources_cfg["concepts"].items():
        if metric in DEBT_COMPONENT_KEYS:
            continue
        # Choose the concept with the freshest annual series (or, for a
        # metric with no annual rows at all, the first with any rows).
        chosen_rows, best = [], []
        for concept in candidates:
            rows = _unit_rows(facts, concept, metric)
            annual = _annual_from_rows(rows, metric)
            if annual and (not best or annual[-1]["end"] > best[-1]["end"]):
                chosen_rows, best = rows, annual
            elif not best and not chosen_rows and rows:
                chosen_rows = rows
        series[metric] = best
        if metric in BALANCE_METRICS:
            latest = _latest_instant(chosen_rows)
            if latest:
                balance_latest[metric] = latest
        else:
            periods[metric] = _period_rows(chosen_rows)
    # Debt instants for every debt concept (not just the one concept the
    # annual-series selection picked): resolution at the latest balance
    # date happens in metrics.compute_metrics.
    debt_instants = {}
    for group, key in DEBT_GROUPS:
        per_concept = {}
        for concept in sources_cfg["concepts"].get(key, []):
            inst = _instants_by_end(_unit_rows(facts, concept, "total_debt"))
            if inst:
                per_concept[concept] = inst
        debt_instants[group] = per_concept
    families = {}
    for fam_name, cur_key, non_key in DEBT_FAMILIES:
        fam = {}
        for side, key in (("current", cur_key), ("noncurrent", non_key)):
            per_concept = {}
            for concept in sources_cfg["concepts"].get(key, []):
                inst = _instants_by_end(_unit_rows(facts, concept, "total_debt"))
                if inst:
                    per_concept[concept] = inst
            fam[side] = per_concept
        families[fam_name] = fam
    debt_instants["families"] = families
    series["_debt_instants"] = debt_instants
    series["_periods"] = periods
    series["_balance_latest"] = balance_latest
    return facts.get("entityName"), series

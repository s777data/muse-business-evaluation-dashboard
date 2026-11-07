"""Derived KPIs from EDGAR series. Pure arithmetic, no inputs from
anywhere else.

Two families of numbers, kept deliberately separate:
- ANNUAL history (latest FY, 5-year medians, CAGR, DCF base) — through-
  cycle by design; the scoring config chooses medians for margin levels.
- TRAILING TWELVE MONTHS (TTM) for trailing valuation multiples (P/S,
  EV/S, trailing P/E, FCF yield). TTM is built as
      latest annual + current-FY year-to-date - same YTD a year earlier
  from filed 10-K/10-Q duration rows. Summing the last four quarters is
  not robust (a fiscal Q4 is often never filed standalone), and using the
  latest annual as if it were TTM understates a fast grower's trailing
  revenue badly (NVDA: $215.9B annual vs ~$302.9B true TTM in Nov 2026).
"""
import statistics
from datetime import date, timedelta


def _by_end(series):
    return {row["end"]: row["value"] for row in series}


def _latest(series):
    return series[-1] if series else None


def _aligned_ratio(num, den):
    d = _by_end(den)
    return [(end, v / d[end] * 100) for end, v in _by_end(num).items()
            if end in d and d[end]]


def _median_last(values, n=5):
    tail = values[-n:]
    return statistics.median(tail) if tail else None


def _day(s):
    y, m, d = (int(p) for p in s.split("-"))
    return date(y, m, d)


def ttm_from_periods(rows, annual_start, annual_end, annual_value):
    """TTM from filed duration rows for one flow metric.

    rows: [{start, end, value, filed}] covering annual + interim periods.
    Returns a dict: value (None if a post-annual YTD exists but the
    prior-year YTD cannot be found), basis, and the components used.
    """
    out = {"value": None, "basis": "unavailable", "annual_value": annual_value,
           "annual_end": annual_end, "ytd_current": None, "ytd_prior": None,
           "ttm_end": annual_end}
    if annual_value is None or not rows:
        return out
    a_end = _day(annual_end)
    post = [r for r in rows if _day(r["end"]) > a_end]
    if not post:
        out.update(value=annual_value, basis="annual_only_no_interim_filings_since_fy_end")
        return out
    ttm_end = max(_day(r["end"]) for r in post)
    out["ttm_end"] = ttm_end.isoformat()
    # Current-FY YTD row: ends at the latest interim end and starts at the
    # fiscal-year start (day after the annual period ended). Prefer the
    # row with the closest start; fall back to the longest duration.
    target_start = a_end + timedelta(days=1)
    ending = [r for r in rows if _day(r["end"]) == ttm_end]
    ending.sort(key=lambda r: (abs((_day(r["start"]) - target_start).days),
                               -(_day(r["end"]) - _day(r["start"])).days))
    ytd_cur = ending[0]
    if abs((_day(ytd_cur["start"]) - target_start).days) > 15:
        ytd_cur = max(ending, key=lambda r: (_day(r["end"]) - _day(r["start"])).days)
    out["ytd_current"] = ytd_cur["value"]
    dur = (ttm_end - _day(ytd_cur["start"])).days
    # Prior-year YTD: starts at the annual period's own start (i.e. the
    # beginning of the latest fiscal year) with about the same duration.
    a_start = _day(annual_start)
    prior_cands = []
    for r in rows:
        if abs((_day(r["start"]) - a_start).days) <= 15 and a_start < _day(r["end"]) < a_end:
            if abs(((_day(r["end"]) - _day(r["start"])).days) - dur) <= 20:
                prior_cands.append(r)
    if not prior_cands:
        out["basis"] = "ttm_unavailable_prior_year_ytd_not_filed"
        return out
    prior_cands.sort(key=lambda r: (abs(((_day(r["end"]) - _day(r["start"])).days) - dur),
                                    -(date.fromisoformat(r["filed"]).toordinal() if r.get("filed") else 0)))
    ytd_prior = prior_cands[0]
    out["ytd_prior"] = ytd_prior["value"]
    out["value"] = annual_value + ytd_cur["value"] - ytd_prior["value"]
    out["basis"] = "annual_plus_current_fy_ytd_minus_prior_year_ytd"
    return out


def _annual_start(period_rows, annual_end):
    """Fiscal-year start of the annual period: the 300-400 day row in the
    period rows that ends at the annual end."""
    for r in period_rows or []:
        if r["end"] == annual_end and 300 <= (_day(r["end"]) - _day(r["start"])).days <= 400:
            return r["start"]
    return None


def _latest_period_value(period_rows):
    """Most recent period's value: max end, then shortest duration (the
    standalone quarter, not the YTD cumulative), then latest filed."""
    if not period_rows:
        return None
    max_end = max(r["end"] for r in period_rows)
    cands = [r for r in period_rows if r["end"] == max_end]
    cands.sort(key=lambda r: ((_day(r["end"]) - _day(r["start"])).days,
                              -(date.fromisoformat(r["filed"]).toordinal() if r.get("filed") else 0)))
    return cands[0]["value"]


def compute_metrics(series, tax_rate_pct):
    rev = series.get("revenue", [])
    periods = series.get("_periods", {})
    balance_latest = series.get("_balance_latest", {})
    out = {"history_years": [r["end"] for r in rev]}
    if not rev:
        return out
    latest_rev = _latest(rev)
    out["revenue_latest"] = latest_rev["value"]
    out["fiscal_year_end"] = latest_rev["end"]
    if len(rev) >= 2:
        prev = rev[-2]["value"]
        out["revenue_growth_latest_pct"] = (latest_rev["value"] / prev - 1) * 100 if prev else None
    n = min(5, len(rev) - 1)
    if n >= 2:
        base = rev[-1 - n]["value"]
        out["revenue_cagr_5y_pct"] = ((latest_rev["value"] / base) ** (1 / n) - 1) * 100 if base else None
        out["revenue_cagr_years"] = n

    # --- TTM flow values (for trailing multiples; annual history above
    # and the median/DCF logic below are intentionally untouched) ---
    ttm_details = {}
    for metric, out_key in (("revenue", "revenue"), ("eps_diluted", "eps_diluted"),
                            ("net_income", "net_income"),
                            ("operating_cash_flow", "operating_cash_flow"),
                            ("capex", "capex")):
        annual = _latest(series.get(metric, []))
        if not annual:
            continue
        start = _annual_start(periods.get(metric), annual["end"])
        if start is None:
            continue
        t = ttm_from_periods(periods.get(metric, []), start, annual["end"], annual["value"])
        out[f"{out_key}_ttm"] = t["value"]
        ttm_details[out_key] = t
    out["ttm_details"] = ttm_details
    out["ttm_end"] = (ttm_details.get("revenue") or {}).get("ttm_end")

    ocf_t, capex_t = out.get("operating_cash_flow_ttm"), out.get("capex_ttm")
    out["fcf_ttm"] = (ocf_t - capex_t) if (ocf_t is not None and capex_t is not None) else None

    shares_period = _latest_period_value(periods.get("diluted_shares"))
    out["diluted_shares_latest_period"] = shares_period

    # Gross profit: direct concept, else derived as revenue - cost of revenue
    # (some filers, e.g. STX, tag cost but not gross profit).
    gp_series = series.get("gross_profit", [])
    if not gp_series and series.get("cost_of_revenue"):
        cost = _by_end(series["cost_of_revenue"])
        gp_series = [{"end": e, "value": v - cost[e]} for e, v in _by_end(rev).items() if e in cost]
    gm_series = _aligned_ratio(gp_series, rev)
    om_series = _aligned_ratio(series.get("operating_income", []), rev)
    ocf, capex = _by_end(series.get("operating_cash_flow", [])), _by_end(series.get("capex", []))
    rev_by_end = _by_end(rev)
    fcf_by_end = {e: ocf[e] - capex[e] for e in ocf if e in capex}
    fcfm_series = [(e, fcf_by_end[e] / rev_by_end[e] * 100) for e in sorted(fcf_by_end) if rev_by_end.get(e)]
    out["fcf_latest"] = fcf_by_end[max(fcf_by_end)] if fcf_by_end else None
    out["fcf_series_by_year"] = {e: fcf_by_end[e] for e in sorted(fcf_by_end)}

    gm_vals = [v for _, v in gm_series]
    out["gross_margin_latest_pct"] = gm_vals[-1] if gm_vals else None
    out["gross_margin_median_5y_pct"] = _median_last(gm_vals)
    out["gross_margin_stdev_5y_pts"] = statistics.pstdev(gm_vals[-5:]) if len(gm_vals) >= 3 else None
    out["operating_margin_median_5y_pct"] = _median_last([v for _, v in om_series])
    out["fcf_margin_median_5y_pct"] = _median_last([v for _, v in fcfm_series])
    out["fcf_margin_latest_pct"] = fcfm_series[-1][1] if fcfm_series else None

    def lat(name):
        row = _latest(series.get(name, []))
        return row["value"] if row else None

    def blat(name):
        # Balance-sheet "latest" = latest filed period end (10-K or 10-Q),
        # falling back to the annual series.
        row = balance_latest.get(name)
        return row["value"] if row else lat(name)

    cash = blat("cash") or 0
    inv = (blat("investments_current") or 0) + (blat("investments_noncurrent") or 0)
    debt = blat("total_debt") or 0
    out["cash_latest"] = blat("cash")
    out["investments_latest"] = inv if (blat("investments_current") is not None or blat("investments_noncurrent") is not None) else None
    out["total_debt_latest"] = blat("total_debt")
    out["balance_as_of"] = max((r["end"] for r in balance_latest.values()), default=None)
    out["net_cash_latest"] = cash + inv - debt
    # Snapshot balance-sheet strength vs trailing revenue (TTM when the
    # TTM build succeeded, else the latest annual).
    rev_snapshot = out.get("revenue_ttm") or latest_rev["value"]
    out["net_cash_to_revenue"] = out["net_cash_latest"] / rev_snapshot if rev_snapshot else None
    out["capex_latest"] = lat("capex")
    out["capex_intensity_latest_pct"] = (lat("capex") / latest_rev["value"] * 100) if lat("capex") else None
    out["operating_cash_flow_latest"] = lat("operating_cash_flow")
    out["net_income_latest"] = lat("net_income")
    out["rd_expense_latest"] = lat("rd_expense")
    out["eps_diluted_latest"] = lat("eps_diluted")
    out["diluted_shares_latest"] = lat("diluted_shares")

    # Through-cycle ROIC: operating income after config tax, over invested
    # capital (equity + debt - cash - investments), aligned per year, median.
    eq, db, ca = _by_end(series.get("equity", [])), _by_end(series.get("total_debt", [])), _by_end(series.get("cash", []))
    invc = _by_end(series.get("investments_current", []))
    invn = _by_end(series.get("investments_noncurrent", []))
    oi = _by_end(series.get("operating_income", []))
    roics = []
    for e in sorted(oi):
        if e in eq and e in db and e in ca:
            capital = eq[e] + db[e] - ca[e] - invc.get(e, 0) - invn.get(e, 0)
            if capital > 0:
                roics.append(oi[e] * (1 - tax_rate_pct / 100) / capital * 100)
    out["roic_median_5y_pct"] = _median_last(roics)
    out["roic_series_pct"] = roics
    return out


def add_price_metrics(metrics, price):
    """Trailing multiples on a TTM basis: revenue / EPS / FCF come from
    the TTM build when available (falling back to the latest annual, with
    the basis recorded), and market cap uses the most recent period's
    diluted share count."""
    shares = metrics.get("diluted_shares_latest_period") or metrics.get("diluted_shares_latest")
    rev = metrics.get("revenue_ttm")
    basis = "ttm"
    if rev is None:
        rev = metrics.get("revenue_latest")
        basis = "annual_fallback"
    if not shares or not rev:
        return metrics
    metrics["shares_used_for_market_cap"] = shares
    metrics["revenue_used_for_multiples"] = rev
    metrics["multiples_basis"] = basis
    mktcap = price["last_close"] * shares
    metrics["market_cap"] = mktcap
    metrics["price_to_sales"] = mktcap / rev
    net_cash = metrics.get("net_cash_latest") or 0
    metrics["ev_to_sales"] = (mktcap - net_cash) / rev
    eps = metrics.get("eps_diluted_ttm")
    if eps is None:
        eps = metrics.get("eps_diluted_latest")
    metrics["eps_used_for_trailing_pe"] = eps
    metrics["trailing_pe"] = price["last_close"] / eps if eps else None
    fcf = metrics.get("fcf_ttm")
    if fcf is None:
        fcf = metrics.get("fcf_latest")
    metrics["fcf_used_for_yield"] = fcf
    metrics["fcf_yield_latest_pct"] = fcf / mktcap * 100 if fcf is not None else None
    metrics["discount_from_52w_high_pct"] = (price["last_close"] / price["high_52w"] - 1) * 100 if price.get("high_52w") else None
    metrics["price_vs_52w_low_pct"] = (price["last_close"] / price["low_52w"] - 1) * 100 if price.get("low_52w") else None
    return metrics

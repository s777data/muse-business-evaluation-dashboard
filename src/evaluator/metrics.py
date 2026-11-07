"""Derived KPIs from EDGAR annual series. Pure arithmetic, no inputs
from anywhere else. Margin levels used for scoring are 5-year medians
(through-cycle); the code computes both latest and median and the scoring
config chooses which series feeds which sub-score."""
import statistics


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


def compute_metrics(series, tax_rate_pct):
    rev = series.get("revenue", [])
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

    cash = lat("cash") or 0
    inv = (lat("investments_current") or 0) + (lat("investments_noncurrent") or 0)
    debt = lat("total_debt") or 0
    out["cash_latest"] = lat("cash")
    out["investments_latest"] = inv if (lat("investments_current") is not None or lat("investments_noncurrent") is not None) else None
    out["total_debt_latest"] = lat("total_debt")
    out["net_cash_latest"] = cash + inv - debt
    out["net_cash_to_revenue"] = out["net_cash_latest"] / latest_rev["value"] if latest_rev["value"] else None
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
    shares = metrics.get("diluted_shares_latest")
    rev = metrics.get("revenue_latest")
    if not shares or not rev:
        return metrics
    mktcap = price["last_close"] * shares
    metrics["market_cap"] = mktcap
    metrics["price_to_sales"] = mktcap / rev
    net_cash = metrics.get("net_cash_latest") or 0
    metrics["ev_to_sales"] = (mktcap - net_cash) / rev
    eps = metrics.get("eps_diluted_latest")
    metrics["trailing_pe"] = price["last_close"] / eps if eps else None
    fcf = metrics.get("fcf_latest")
    metrics["fcf_yield_latest_pct"] = fcf / mktcap * 100 if fcf is not None else None
    metrics["discount_from_52w_high_pct"] = (price["last_close"] / price["high_52w"] - 1) * 100 if price.get("high_52w") else None
    metrics["price_vs_52w_low_pct"] = (price["last_close"] / price["low_52w"] - 1) * 100 if price.get("low_52w") else None
    return metrics

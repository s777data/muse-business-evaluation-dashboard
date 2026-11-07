"""CLI: python -m src.evaluator.run MU

Pipeline (all code, free sources only):
  EDGAR companyfacts -> KPI metrics -> config-anchor sub-scores
  EDGAR 10-K text    -> customer concentration evidence
  Free price feed    -> latest close / 52-week range (fail closed if none)
  Config DCF         -> fair value + distance from current price
  EDGAR peers        -> percentile ranks
Writes output/scores.json with sources, data-quality flags and the
template explanations. No LLM is called at any step.
"""
import json
import sys
from datetime import date

from . import config as cfgmod
from . import dcf as dcfmod
from . import edgar, explanations, filings, metrics as metmod, prices, scoring


def _moat_checks(metrics, concentration, peer_count, checks_cfg):
    checks = []
    capex = metrics.get("capex_latest")
    checks.append({
        "check": "new_entrant_capex_barrier",
        "status": "pass" if (capex or 0) >= checks_cfg["entrant_capex_barrier_min_capex_usd"] else "fail",
        "evidence": f"latest FY capex ${capex:,.0f} vs config barrier ${checks_cfg['entrant_capex_barrier_min_capex_usd']:,.0f}" if capex else "capex missing"})
    stdev = metrics.get("gross_margin_stdev_5y_pts")
    checks.append({
        "check": "product_fungibility_risk (high margin volatility = commodity-like pricing)",
        "status": "risk_flagged" if (stdev or 0) > checks_cfg["margin_fungibility_stdev_pts_over"] else "not_flagged",
        "evidence": f"gross-margin stdev over last 5 FYs = {stdev and round(stdev, 2)} pts (config flag threshold {checks_cfg['margin_fungibility_stdev_pts_over']} pts)"})
    checks.append({
        "check": "concentrated_industry_structure (small EDGAR peer set as oligopoly proxy)",
        "status": "pass" if peer_count <= checks_cfg["oligopoly_peer_count_max_for_concentrated"] else "fail",
        "evidence": f"{peer_count} US-listed EDGAR peers in config peer set"})
    single = (concentration or {}).get("single_customer") or {}
    checks.append({
        "check": "no_single_customer_dependency",
        "status": "pass" if single.get("no_customer_at_or_above_threshold") else "fail_or_unknown",
        "evidence": single.get("quote", "no single-customer sentence found in latest 10-K")})
    checks.append({
        "check": "switching_costs / contract lock-in",
        "status": "not_evaluated_v1",
        "evidence": "No structured free source in v1 quantifies switching costs; flagged, not scored by proxy."})
    return checks


def evaluate(symbol, cfg):
    ticker = cfg["tickers"]["tickers"][symbol]
    tax = cfg["dcf"]["dcf"]["tax_rate_pct"]

    entity, series = edgar.get_company_metrics(cfg["sources"], ticker["cik"])
    m = metmod.compute_metrics(series, tax)

    price = prices.get_price(cfg["sources"], symbol)  # raises -> fail closed
    m = metmod.add_price_metrics(m, price)

    fy_year = int(m["fiscal_year_end"][:4]) if m.get("fiscal_year_end") else None
    filing = filings.get_latest_10k_concentration(cfg["sources"], cfg["scoring"]["concentration_rules"],
                                                  ticker["cik"], fy_year)
    concentration = filing["concentration"]
    conc_score = scoring.concentration_score(concentration, cfg["scoring"]["concentration_rules"])

    # Peers (EDGAR + free price feed), percentile ranks for MU
    peer_rows, peer_failures = [], []
    for peer in cfg["peers"]["peers"].get(symbol, []):
        try:
            _, pseries = edgar.get_company_metrics(cfg["sources"], peer["cik"])
            pm = metmod.compute_metrics(pseries, tax)
            try:
                pprice = prices.get_price(cfg["sources"], peer["symbol"])
                pm = metmod.add_price_metrics(pm, pprice)
            except Exception:
                pprice = None
            peer_rows.append({"symbol": peer["symbol"], "name": peer["name"],
                              "rationale": peer["rationale"],
                              "revenue": pm.get("revenue_ttm") or pm.get("revenue_latest"),
                              "revenue_annual": pm.get("revenue_latest"),
                              "gross_margin_latest_pct": pm.get("gross_margin_latest_pct"),
                              "fcf_margin_latest_pct": pm.get("fcf_margin_latest_pct"),
                              "price_to_sales": pm.get("price_to_sales"),
                              "price": pprice})
        except Exception as exc:
            peer_failures.append({"symbol": peer["symbol"], "error": str(exc)})

    percentiles = {}
    for key, mkey in (("gross_margin_latest_pct", "gross_margin_latest_pct"),
                      ("fcf_margin_latest_pct", "fcf_margin_latest_pct"), ("price_to_sales", "price_to_sales")):
        percentiles[key] = scoring.percentile_rank(m.get(mkey), [r.get(key) for r in peer_rows])
    # Scale percentile on the same TTM basis as the multiples.
    percentiles["revenue"] = scoring.percentile_rank(
        m.get("revenue_ttm") or m.get("revenue_latest"), [r.get("revenue") for r in peer_rows])

    # DCF — beta is a config input; if it is absent the DCF (and the
    # dcf_upside value sub-score) is treated as missing and the remaining
    # value weights renormalise in scoring.score_section. No fallback beta.
    beta = ticker.get("beta")
    wacc = dcfmod.build_wacc(cfg["dcf"]["dcf"], beta, m.get("market_cap"), m.get("total_debt_latest")) if beta is not None else None
    fcf_base = (m["fcf_latest"] / m["diluted_shares_latest"]) if m.get("fcf_latest") and m.get("diluted_shares_latest") else None
    path = dcfmod.growth_path(cfg["dcf"]["dcf"], m.get("revenue_cagr_5y_pct"))
    fv = dcfmod.fair_value(fcf_base, path, wacc["wacc_pct"], cfg["dcf"]["dcf"]["terminal_growth_pct"]) if (fcf_base and wacc) else None
    upside = ((fv["fair_value_per_share"] / price["last_close"] - 1) * 100) if fv else None

    # Scoring inputs assembled from computed metrics only
    m["dcf_upside_pct"] = upside
    m["customer_concentration_score"] = conc_score
    m["revenue_percentile_vs_peers"] = percentiles.get("revenue")
    sc = cfg["scoring"]
    business = scoring.score_section(sc["business"], m)
    value = scoring.score_section(sc["value"], m)
    moat = scoring.score_section(sc["moat"], m)

    data_quality = {
        "trailing_multiples_basis": m.get("multiples_basis"),
        "ttm_end": m.get("ttm_end"),
        "ttm_details": m.get("ttm_details"),
        "consensus_eps": "missing_by_design_v1_no_paid_consensus - forward multiples use filed actuals and the config DCF only",
        "beta": f"config input - {ticker.get('beta_source')}" if beta is not None else "missing - no beta in config/tickers.yaml; DCF sub-score missing, value weights renormalised",
        "beta_missing": beta is None,
        "risk_free_and_erp": "config inputs in config/dcf.yaml",
        "sk_hynix_samsung": cfg["peers"].get("excluded_note"),
        "peer_failures": peer_failures,
        "missing_business_metrics": business["missing_metrics"],
        "missing_value_metrics": value["missing_metrics"],
        "missing_moat_metrics": moat["missing_metrics"],
        "peak_cycle_warning": bool(m.get("gross_margin_latest_pct") and m.get("gross_margin_median_5y_pct")
                                   and m["gross_margin_latest_pct"] - m["gross_margin_median_5y_pct"] > 20),
        "switching_costs": "not_evaluated_v1",
    }

    expl = {
        "business": explanations.explain("Business", business),
        "value": explanations.explain("Value", value,
                                      f" The DCF fair value is ${fv['fair_value_per_share']:,.2f} vs the current ${price['last_close']:,.2f} ({upside:+.1f}%)." if fv else ""),
        "moat": explanations.explain("Moat", moat),
    }

    return {
        "symbol": symbol, "entity_name": entity, "snapshot_date": date.today().isoformat(),
        "inputs_with_sources": {
            "filing_metrics": {"source": "SEC EDGAR XBRL companyfacts", "cik": ticker["cik"],
                               "fiscal_year_end": m.get("fiscal_year_end")},
            "concentration": {"source": "SEC EDGAR latest 10-K full text", **filing["filing"]},
            "price": price,
            "beta": {"value": ticker.get("beta"), "source": ticker.get("beta_source")},
            "dcf_config": "config/dcf.yaml", "scoring_config": "config/scoring.yaml",
        },
        "kpis": {k: v for k, v in m.items() if k != "fcf_series_by_year"},
        "fcf_series_by_year": m.get("fcf_series_by_year"),
        "scores": {"business": business, "value": value, "moat": moat},
        "dcf": {"fcf_per_share_base": round(fcf_base, 4) if fcf_base else None,
                "growth_path_pct": path, "wacc": wacc, **(fv or {}),
                "current_price": price["last_close"],
                "upside_pct": round(upside, 2) if upside is not None else None,
                "distance_dollars": round(fv["fair_value_per_share"] - price["last_close"], 2) if fv else None},
        "peers": {"rows": peer_rows, "percentile_ranks_for_subject": percentiles,
                  "excluded_note": cfg["peers"].get("excluded_note")},
        "moat_replicability_checks": _moat_checks(m, concentration, len(peer_rows), sc["moat_checks"]),
        "customer_concentration": concentration,
        "concentration_score": conc_score,
        "data_quality": data_quality,
        "explanations": expl,
    }


def main(argv):
    symbol = (argv[1] if len(argv) > 1 else "MU").upper()
    cfg = cfgmod.load_all()
    if symbol not in cfg["tickers"]["tickers"]:
        print(f"Unknown ticker {symbol}: add it to config/tickers.yaml", file=sys.stderr)
        return 2
    result = evaluate(symbol, cfg)
    out_path = cfgmod.OUTPUT_DIR / "scores.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    s = result["scores"]
    print(f"{symbol} snapshot {result['snapshot_date']}  price ${result['dcf']['current_price']:,.2f} "
          f"({result['inputs_with_sources']['price']['source']})")
    print(f"Business {s['business']['total']}/10 | Value {s['value']['total']}/10 | Moat {s['moat']['total']}/10")
    if result["dcf"].get("fair_value_per_share") is not None:
        print(f"DCF fair value ${result['dcf']['fair_value_per_share']:,.2f} "
              f"({result['dcf']['upside_pct']:+.1f}% vs current, WACC {result['dcf']['wacc']['wacc_pct']}%)")
    else:
        print("DCF fair value unavailable (missing beta in config or missing FCF base); DCF sub-score missing, value weights renormalised")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""DCF with a BUILT WACC (CAPM, inputs in config/dcf.yaml + beta in
config/tickers.yaml) and growth derived from filed history, clamped and
faded per config. No consensus input exists in v1."""


def build_wacc(dcf_cfg, beta, market_cap, total_debt):
    rf = dcf_cfg["risk_free_rate_pct"]
    erp = dcf_cfg["equity_risk_premium_pct"]
    cost_equity = rf + beta * erp
    cost_debt_after_tax = dcf_cfg["pre_tax_cost_of_debt_pct"] * (1 - dcf_cfg["tax_rate_pct"] / 100)
    ev = (market_cap or 0) + (total_debt or 0)
    equity_w = (market_cap / ev) if ev else 1.0
    debt_w = 1 - equity_w
    wacc = cost_equity * equity_w + cost_debt_after_tax * debt_w
    return {"cost_of_equity_pct": round(cost_equity, 3),
            "cost_of_debt_after_tax_pct": round(cost_debt_after_tax, 3),
            "equity_weight": round(equity_w, 4), "debt_weight": round(debt_w, 4),
            "wacc_pct": round(wacc, 3),
            "inputs": {"risk_free_rate_pct": rf, "equity_risk_premium_pct": erp,
                       "beta": beta, "pre_tax_cost_of_debt_pct": dcf_cfg["pre_tax_cost_of_debt_pct"],
                       "tax_rate_pct": dcf_cfg["tax_rate_pct"]}}


def growth_path(dcf_cfg, filed_cagr_pct):
    g = dcf_cfg["growth"]
    initial = max(g["floor_pct"], min(g["cap_pct"], filed_cagr_pct or 0.0))
    years = dcf_cfg["projection_years"]
    term = dcf_cfg["terminal_growth_pct"]
    if years == 1:
        return [initial]
    step = (initial - term) / years
    return [round(initial - step * i, 4) for i in range(years)]


def fair_value(fcf_per_share_base, growth_pct_path, wacc_pct, terminal_growth_pct):
    """Present value of projected FCF/share plus Gordon terminal value.
    growth_pct_path[i] is the growth applied in projection year i+1."""
    wacc = wacc_pct / 100
    term = terminal_growth_pct / 100
    fcf = fcf_per_share_base
    pv, projected = 0.0, []
    for i, g in enumerate(growth_pct_path, start=1):
        fcf = fcf * (1 + g / 100)
        projected.append(round(fcf, 4))
        pv += fcf / (1 + wacc) ** i
    tv = fcf * (1 + term) / (wacc - term) if wacc > term else 0.0
    pv_tv = tv / (1 + wacc) ** len(growth_pct_path)
    return {"fair_value_per_share": round(pv + pv_tv, 2),
            "pv_of_projected_fcf": round(pv, 2), "pv_of_terminal_value": round(pv_tv, 2),
            "projected_fcf_per_share": projected}

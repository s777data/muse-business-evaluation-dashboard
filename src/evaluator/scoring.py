"""Config-driven piecewise scoring. The ONLY math here is interpolation
between the anchor points in config/scoring.yaml and a weighted mean.
Missing metrics are excluded and the remaining weights renormalised;
every exclusion is reported by the caller in data_quality."""


def interpolate(value, anchors):
    pts = sorted((float(x), float(y)) for x, y in anchors)
    if value <= pts[0][0]:
        return pts[0][1]
    if value >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return pts[-1][1]


def score_section(section_cfg, metric_values):
    subs, missing, weight_sum = {}, [], 0.0
    for metric, spec in section_cfg.items():
        value = metric_values.get(metric)
        if value is None:
            missing.append(metric)
            continue
        score = interpolate(float(value), spec["anchors"])
        subs[metric] = {"value": round(float(value), 4), "score": round(score, 3),
                        "weight": spec["weight"]}
        weight_sum += spec["weight"]
    total = None
    if weight_sum:
        total = sum(s["score"] * s["weight"] for s in subs.values()) / weight_sum
    return {"subscores": subs, "total": round(total, 2) if total is not None else None,
            "missing_metrics": missing, "weights_renormalised_over": round(weight_sum, 4)}


def concentration_score(concentration, rules):
    """Rule table from config/scoring.yaml applied to filing facts."""
    if not concentration or not concentration.get("single_customer"):
        return None
    single = concentration["single_customer"]
    top = concentration.get("top_ten") or {}
    if single.get("no_customer_at_or_above_threshold"):
        score = rules["base_if_no_single_customer_at_threshold"]
    else:
        score = rules["base_if_single_customer_present"]
        pct = single.get("max_single_customer_pct") or 0
        if pct > 20:
            score -= (pct - 20) * rules["single_customer_extra_penalty_per_pct_over_20"]
    top_pct = top.get("top_ten_share_pct")
    if top_pct is not None and top_pct > rules["top10_free_allowance_pct"]:
        score -= (top_pct - rules["top10_free_allowance_pct"]) * rules["top10_penalty_per_pct_over_allowance"]
    return max(rules["score_floor"], min(rules["score_ceiling"], round(score, 3)))


def percentile_rank(value, peer_values):
    vals = [v for v in peer_values if v is not None]
    if value is None or not vals:
        return None
    below = sum(1 for v in vals if v < value)
    equal = sum(1 for v in vals if v == value)
    return round((below + 0.5 * equal) / len(vals) * 100, 1)

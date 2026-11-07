"""Bottom-of-dashboard explanations: TEMPLATE sentences filled with the
actual sub-scores and values the formula produced. No model, no free text
generation — if a template slot has no data, the sentence says so."""


def _top_bottom(subs, n=2):
    items = sorted(subs.items(), key=lambda kv: kv[1]["score"], reverse=True)
    return items[:n], list(reversed(items[-n:])) if len(items) > n else []


def _fmt(metric, entry):
    return f"{metric.replace('_', ' ')} scored {entry['score']}/10 (value {entry['value']})"


def leverage_note(scored):
    """Generic leverage sentence for the Business explanation: names the
    debt-to-revenue and interest-coverage sub-scores when they were
    scored, or states plainly that they were excluded."""
    subs = scored.get("subscores", {})
    parts = []
    for key, label in (("leverage_debt_to_revenue_ttm", "Debt-to-TTM-revenue leverage"),
                       ("interest_coverage_ttm", "Interest coverage")):
        entry = subs.get(key)
        if entry:
            parts.append(f"{label} scored {entry['score']}/10 (value {entry['value']})")
    if parts:
        return " " + " and ".join(parts) + "."
    return (" Leverage checks (debt-to-TTM-revenue and interest coverage) were "
            "excluded: filed debt or interest data could not be determined.")


def explain(section_name, scored, extra=""):
    subs = scored.get("subscores", {})
    total = scored.get("total")
    if total is None or not subs:
        return f"{section_name} score unavailable: required filed metrics were missing."
    top, bottom = _top_bottom(subs)
    s1 = (f"{section_name} score is {total}/10, driven mainly by "
          + " and ".join(_fmt(m, e) for m, e in top) + ".")
    s2 = ""
    if bottom:
        s2 = (" The main drags were "
              + " and ".join(_fmt(m, e) for m, e in bottom) + ".")
    return (s1 + s2 + extra).strip()

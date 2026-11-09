"""Announced / remaining share-repurchase authorization extraction (v5.3).

System of record (SPEC_V5.3 §2.1): the latest periodic filing — 10-Q
Part II Item 2 / 10-K Part II Item 5 (Reg S-K Item 703) and the narrative
around it (equity note, MD&A, risk factors). Sentences are matched by
rule and stored VERBATIM as evidence, exactly like filings.py does for
customer concentration. Numbers are parsed from those sentences only.

Fail-closed rules (SPEC_V5.3 §2.3):
- An "increase / total authorization" figure is accepted as the
  REMAINING amount only when the filing's own construction sentence is
  located nearby (the leftover it is built from, "plus ...") — filers
  use "total authorization" both for a cumulative cap and for a new
  available total (the MU trap, SPEC §3.2), so without the construction
  sentence the figure is ambiguous and is never scored.
- Share-denominated authorizations are stored as shares and never
  converted at market price in v1.
- Employee tax-withholding repurchases are never program repurchases.
"""
import re

from .filings import split_sentences

MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(billion|million|thousand)?", re.I)
DATE_RE = re.compile(r"(January|February|March|April|May|June|July|August|"
                     r"September|October|November|December)\s+\d{1,2},\s+\d{4}")
SCALE = {"billion": 1e9, "million": 1e6, "thousand": 1e3, None: 1.0}


def _money(sentence, start=0):
    """First $ amount in the sentence at/after `start`, in USD, or None."""
    m = MONEY_RE.search(sentence, start)
    if not m:
        return None
    return float(m.group(1).replace(",", "")) * SCALE[(m.group(2) or "").lower() or None]


def _date_in(sentence):
    m = DATE_RE.search(sentence)
    return m.group(0) if m else None


def _is_program_sentence(s):
    low = s.lower()
    return "repurchas" in low and ("authoriz" in low or "program" in low)


def extract_buyback(text):
    """Extract the repurchase-authorization state from one periodic
    filing's stripped full text. Pure function — unit-testable."""
    sentences = split_sentences(text)
    out = {
        "authorization_total_usd": None,
        "cumulative_used_usd": None,
        "remaining_usd": None,
        "remaining_basis": None,
        "remaining_as_of": None,
        "authorization_date": None,
        "effective_from": None,
        "expiry": None,
        "conditions_quote": None,
        "source_quote": None,
        "construction_quote": None,
        "share_denominated_remaining": None,
        "flags": [],
        "history": [],
    }
    history = out["history"]

    # --- Program origination: "In 2018, ... authorized ... up to $X" ---
    for s in sentences:
        low = s.lower()
        if "authorized" not in low or "repurchas" not in low:
            continue
        ym = re.search(r"\bIn (\d{4}),", s)
        up_to = re.search(r"up to\s+", s)
        if ym and up_to:
            amt = _money(s, up_to.start())
            if amt:
                history.append({"type": "program_authorized", "date": ym.group(1),
                                "amount_usd": amt, "quote": s})
                out["authorization_total_usd"] = amt
                break
        # Share-denominated authorization (no $ amount at all).
        if ym and re.search(r"up to\s+[\d,]+\s+shares", low) and not MONEY_RE.search(s):
            out["flags"].append("buyback_remaining_share_denominated")
            history.append({"type": "program_authorized_shares", "date": ym.group(1),
                            "quote": s})

    # --- Cumulative used: "repurchased an aggregate of $X under ..." ---
    for s in sentences:
        if "repurchased an aggregate of" in s.lower():
            amt = _money(s)
            if amt:
                out["cumulative_used_usd"] = amt
                d = _date_in(s)
                history.append({"type": "cumulative_used", "as_of": d,
                                "amount_usd": amt, "quote": s})
                break

    # --- Remaining at period end (narrative) ---
    remaining_candidates = []
    for s in sentences:
        low = s.lower()
        if "remained available" in low or "remains" in low and "authorization" in low:
            amt = _money(s)
            if amt and "repurchas" in low:
                remaining_candidates.append((s, amt))
    period_remaining, period_quote = None, None
    for s, amt in remaining_candidates:
        if "as of" in s.lower():
            period_remaining, period_quote = amt, s
            break
    if period_remaining is None and remaining_candidates:
        period_quote, period_remaining = remaining_candidates[0][0], remaining_candidates[0][1]
    if period_remaining is not None:
        asof = None
        m = re.search(r"as of\s+(" + DATE_RE.pattern + r")", period_quote)
        if m:
            asof = m.group(1)
        out["remaining_as_of"] = asof
        history.append({"type": "remaining_at_period_end", "as_of": asof,
                        "amount_usd": period_remaining, "quote": period_quote})

    # --- Item 703 table fallback for remaining (HTML table, flattened) ---
    table_remaining = None
    idx = text.rfind("may yet be purchased under publicly announced plans or programs")
    if idx >= 0:
        chunk = text[idx:idx + 4000]
        cut = re.search(r"\bITEM\s+\d", chunk[200:])
        if cut:
            chunk = chunk[:200 + cut.start()]
        scale = 1e6 if "(in millions)" in chunk else (1e3 if "(in thousands)" in chunk else 1.0)
        amounts = [float(a.replace(",", "")) for a in
                   re.findall(r"\$\s*([\d,]+(?:\.\d+)?)", chunk)]
        if amounts:
            table_remaining = amounts[-1] * scale

    # --- Post-period board increase (supersedes period-end remaining) ---
    # A filing usually states the same increase in several places
    # (Item 5, equity note, MD&A); only some carry the construction
    # sentence nearby. Scan ALL candidates: a verified one governs,
    # and unverified duplicates of it are not flags — the ambiguity
    # flag fires only when NO statement of the increase verifies.
    increase, unverified = None, []
    for i, s in enumerate(sentences):
        low = s.lower()
        if "authorized an increase" not in low or "repurchas" not in low:
            continue
        amt = _money(s)
        if not amt:
            continue
        # Construction sentence: this sentence or the next few must show
        # what the total is built from (a remained-available leftover).
        construction = None
        for j in range(i, min(i + 7, len(sentences))):
            cj = sentences[j].lower()
            if "remained available" in cj and ("plus" in cj or "based on" in cj):
                construction = sentences[j]
                break
        if construction is None:
            unverified.append((amt, s))
            continue
        eff = None
        for j in range(i, min(i + 4, len(sentences))):
            m = re.search(r"(?:to be made|will be made|be made) from\s+(" + DATE_RE.pattern + r")",
                          sentences[j])
            if m:
                eff = m.group(1)
                break
        increase = {"amount_usd": amt, "date": _date_in(s), "effective_from": eff,
                    "quote": s, "construction_quote": construction}
        break
    if increase is None:
        for amt, s in unverified:
            out["flags"].append("buyback_total_remaining_ambiguous")
            history.append({"type": "authorization_increase_unverified",
                            "amount_usd": amt, "quote": s})
            break

    if increase:
        out["remaining_usd"] = increase["amount_usd"]
        out["remaining_basis"] = "post_period_increase_total_with_construction_sentence"
        out["authorization_total_usd"] = increase["amount_usd"]
        out["authorization_date"] = increase["date"]
        out["effective_from"] = increase["effective_from"]
        out["source_quote"] = increase["quote"]
        out["construction_quote"] = increase["construction_quote"]
        history.append({"type": "authorization_increased",
                        "date": increase["date"],
                        "effective_from": increase["effective_from"],
                        "amount_usd": increase["amount_usd"],
                        "quote": increase["quote"],
                        "construction_quote": increase["construction_quote"]})
    elif period_remaining is not None:
        out["remaining_usd"] = period_remaining
        out["remaining_basis"] = "narrative_remaining_at_period_end"
        out["source_quote"] = period_quote
    elif table_remaining is not None:
        out["remaining_usd"] = table_remaining
        out["remaining_basis"] = "item_703_table_may_yet_be_purchased"
        out["source_quote"] = ("Item 703 table, final column: approximate dollar "
                                "value of shares that may yet be purchased under "
                                "publicly announced plans or programs")
        history.append({"type": "remaining_at_period_end", "as_of": None,
                        "amount_usd": table_remaining, "quote": out["source_quote"]})

    # --- Execution notes in the period (program buys only) ---
    seen_exec = set()
    for s in sentences:
        low = s.lower()
        if ("did not repurchase any common stock under" in low
                or re.search(r"repurchased [\d.]+ million shares", low)):
            key = s[:70]
            if key not in seen_exec:
                seen_exec.add(key)
                history.append({"type": "execution_note", "quote": s})
        if len(seen_exec) >= 3:
            break

    # --- Conditions / expiry ---
    for s in sentences:
        low = s.lower()
        if _is_program_sentence(s) and "no expiration date" in low:
            out["expiry"] = None
            history.append({"type": "conditions", "quote": s})
            if "subject to" in low:
                out["conditions_quote"] = s[:400]
            break
    if out["conditions_quote"] is None:
        for s in sentences:
            low = s.lower()
            if _is_program_sentence(s) and "subject to market conditions" in low:
                out["conditions_quote"] = s[:400]
                break

    if out["remaining_usd"] is None and "buyback_remaining_share_denominated" not in out["flags"]:
        out["flags"].append("buyback_remaining_not_disclosed")
    # Reconciliation cross-check (SPEC §2.1): used + remaining should
    # not exceed a stated cumulative program total (non-increase basis).
    if (increase is None and out["cumulative_used_usd"] and out["remaining_usd"]
            and out["authorization_total_usd"]
            and out["cumulative_used_usd"] + out["remaining_usd"]
            > out["authorization_total_usd"] * 1.02):
        out["flags"].append("buyback_remaining_unreconciled")
    return out

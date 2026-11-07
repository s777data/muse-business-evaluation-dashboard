"""Free price feeds, tried in config order. Fails CLOSED: if no feed returns
usable daily history, this raises and the whole run stops with a clear error.
Stooq is listed second: during development it returned an empty body from
this network, while the Yahoo chart API returned full daily history.
"""
import csv
import io
import json
from datetime import datetime, timezone

from .util import fetch_bytes


def _from_yahoo(raw, feed_name):
    data = json.loads(raw.decode("utf-8", "replace"))
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        return None
    quotes = result["indicators"]["quote"][0]
    stamps = result.get("timestamp") or []
    rows = []
    for ts, close in zip(stamps, quotes.get("close") or []):
        if close is not None:
            rows.append((datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(), float(close)))
    if not rows:
        return None
    closes = [c for _, c in rows]
    return {"source": feed_name, "last_close": rows[-1][1], "last_date": rows[-1][0],
            "high_52w": max(closes), "low_52w": min(closes), "points": len(rows)}


def _from_stooq(raw, feed_name):
    text = raw.decode("utf-8", "replace").strip()
    if not text or "Date" not in text.splitlines()[0]:
        return None
    rows = [(r["Date"], float(r["Close"])) for r in csv.DictReader(io.StringIO(text))
            if r.get("Close") not in (None, "", "N/D")]
    if not rows:
        return None
    closes = [c for _, c in rows]
    return {"source": feed_name, "last_close": rows[-1][1], "last_date": rows[-1][0],
            "high_52w": max(closes), "low_52w": min(closes), "points": len(rows)}


PARSERS = {"yahoo_chart": _from_yahoo, "stooq_daily_csv": _from_stooq}


def get_price(sources_cfg, symbol):
    src = sources_cfg["sources"]
    errors = []
    for feed in src["price_feeds"]:
        url = feed["url"].format(symbol=symbol, symbol_lower=symbol.lower())
        try:
            raw = fetch_bytes(url, src["user_agent"], src["timeout_seconds"],
                              src["request_delay_seconds"])
            parsed = PARSERS[feed["name"]](raw, feed["name"])
            if parsed:
                parsed["url"] = url
                return parsed
            errors.append(f"{feed['name']}: empty/unusable response")
        except Exception as exc:  # keep trying the next configured feed
            errors.append(f"{feed['name']}: {exc}")
    raise RuntimeError(f"No free price feed returned data for {symbol}. Tried: {errors}")

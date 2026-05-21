"""FX conversion. Primary provider: frankfurter.app (free, no key)."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date


class FXError(RuntimeError):
    pass


def convert(amount: float, *, from_ccy: str, to_ccy: str, on: date | None = None, provider: str = "frankfurter") -> tuple[float, float]:
    """Return ``(converted_amount, rate)`` such that ``converted = amount * rate``.

    ``rate`` is units of ``to_ccy`` per 1 unit of ``from_ccy``.  ``on`` selects a
    specific date; ``None`` uses the most recent published rate.  Same-currency
    conversions short-circuit to rate=1.0 without a network call.
    """
    from_ccy = from_ccy.upper()
    to_ccy = to_ccy.upper()
    if from_ccy == to_ccy:
        return round(amount, 2), 1.0
    if provider != "frankfurter":
        raise FXError(f"Unsupported FX provider: {provider}")
    when = on.isoformat() if on else "latest"
    url = f"https://api.frankfurter.dev/v1/{when}?" + urllib.parse.urlencode(
        {"base": from_ccy, "symbols": to_ccy}
    )
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = json.loads(r.read())
    except Exception as exc:
        raise FXError(f"FX lookup failed: {exc}") from exc
    rate = body.get("rates", {}).get(to_ccy)
    if rate is None:
        raise FXError(f"FX rate {from_ccy}->{to_ccy} not returned by provider")
    return round(amount * float(rate), 2), float(rate)

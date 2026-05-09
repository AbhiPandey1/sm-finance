"""Fetch key annual metrics from SEC XBRL Company Facts API (data.sec.gov)."""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SEC_DATA = "https://data.sec.gov"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC fair access: identify your traffic
# SEC may 403 requests without a clear, contact-style User-Agent.
_DEFAULT_UA = "SMFinance/0.1 (support@smfinance.local; educational demo)"

# Used if company_tickers.json cannot be fetched (rate limit, 403, offline demo).
_FALLBACK_CIK: dict[str, str] = {
    "AAPL": "0000320193",
    "APP": "0001751008",  # AppLovin Corp.
}

_tickers_lock = threading.Lock()
_ticker_to_cik: dict[str, str] | None = None


def _user_agent() -> str:
    return (os.getenv("SEC_USER_AGENT") or _DEFAULT_UA).strip() or _DEFAULT_UA


def _headers() -> dict[str, str]:
    return {
        "User-Agent": _user_agent(),
        "Accept": "application/json",
    }


def _load_ticker_map(client: httpx.Client) -> dict[str, str]:
    global _ticker_to_cik
    with _tickers_lock:
        if _ticker_to_cik is not None:
            return _ticker_to_cik
        r = client.get(SEC_TICKERS_URL, headers=_headers(), timeout=30.0)
        r.raise_for_status()
        raw = r.json()
        m: dict[str, str] = {}
        # company_tickers.json: {"0":{"cik_str":320193,"ticker":"AAPL",...},...}
        for _k, row in raw.items():
            if not isinstance(row, dict):
                continue
            t = str(row.get("ticker", "")).strip().upper()
            cik_num = row.get("cik_str")
            if not t or cik_num is None:
                continue
            m[t] = str(int(cik_num)).zfill(10)
        _ticker_to_cik = m
        return m


def ticker_to_cik(ticker: str, client: httpx.Client | None = None) -> str | None:
    t = ticker.strip().upper()
    own = client is None
    c = client or httpx.Client()
    try:
        try:
            m = _load_ticker_map(c)
            hit = m.get(t)
            if hit:
                return hit
        except httpx.HTTPError as e:
            logger.warning("SEC ticker map fetch failed, using fallback if any: %s", e)
        return _FALLBACK_CIK.get(t)
    finally:
        if own:
            c.close()


def _cik_url_path(cik10: str) -> str:
    return f"{SEC_DATA}/api/xbrl/companyfacts/CIK{cik10}.json"


def _first_tag(us_gaap: dict[str, Any], names: tuple[str, ...]) -> tuple[str | None, dict[str, Any] | None]:
    for name in names:
        node = us_gaap.get(name)
        if isinstance(node, dict) and node.get("units"):
            return name, node
    return None, None


def _usd_points(tag_node: dict[str, Any]) -> list[dict[str, Any]]:
    units = tag_node.get("units") or {}
    if "USD" in units:
        return list(units["USD"])
    # rare: first unit bucket
    for _k, pts in units.items():
        if isinstance(pts, list):
            return list(pts)
    return []


def _period_span_days(p: dict[str, Any]) -> int | None:
    """Days between start and end when both exist (duration facts)."""
    s, e = p.get("start"), p.get("end")
    if not s or not e:
        return None
    try:
        d0 = datetime.strptime(str(s)[:10], "%Y-%m-%d")
        d1 = datetime.strptime(str(e)[:10], "%Y-%m-%d")
        return (d1 - d0).days
    except (TypeError, ValueError):
        return None


def _fy_10k_points(
    points: list[dict[str, Any]],
    *,
    min_duration_days: int | None = None,
) -> list[dict[str, Any]]:
    """FY + 10-K rows from SEC companyfacts.

    Some filers (notably Apple) publish segment/quarter amounts that still carry ``fp=FY``
    on the 10-K; those periods are ~90 days. For income-statement flow tags, pass
    ``min_duration_days`` (~350) to keep full-year duration facts only.

    Balance-sheet instant facts often omit ``start``; those are dropped when
    ``min_duration_days`` is set (use ``None`` for Assets, Debt, etc.).
    """
    out: list[dict[str, Any]] = []
    for p in points:
        if p.get("fp") != "FY":
            continue
        if p.get("form") != "10-K":
            continue
        end = p.get("end")
        if not end:
            continue
        if min_duration_days is not None:
            span = _period_span_days(p)
            if span is None or span < min_duration_days:
                continue
        out.append(p)
    return out


def _dedupe_end_latest(points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map period end -> point with max filed date."""
    by_end: dict[str, dict[str, Any]] = {}
    for p in points:
        end = str(p.get("end", ""))
        prev = by_end.get(end)
        if prev is None or str(p.get("filed", "")) >= str(prev.get("filed", "")):
            by_end[end] = p
    return by_end


def _val_map_for_tag(
    us_gaap: dict[str, Any],
    names: tuple[str, ...],
    *,
    min_duration_days: int | None = None,
) -> dict[str, float | None]:
    _tag, node = _first_tag(us_gaap, names)
    if not node:
        return {}
    pts = _fy_10k_points(_usd_points(node), min_duration_days=min_duration_days)
    by_end = _dedupe_end_latest(pts)
    return {end: _as_float(p.get("val")) for end, p in by_end.items()}


def _revenue_series(
    us_gaap: dict[str, Any],
) -> tuple[dict[str, float | None], dict[str, int | None]]:
    """Revenue values and SEC fiscal year label (fy) per period end.

    Combine multiple US-GAAP revenue tags (legacy ``Revenues`` vs ASC 606 contract revenue).
    Some issuers (e.g. Apple) stop extending ``Revenues`` but publish contract revenue for
    recent years — merging avoids stale 2018-only series.
    """
    names = (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    )
    all_pts: list[dict[str, Any]] = []
    for name in names:
        node = us_gaap.get(name)
        if not isinstance(node, dict) or not node.get("units"):
            continue
        all_pts.extend(_fy_10k_points(_usd_points(node), min_duration_days=350))
    if not all_pts:
        return {}, {}
    by_end = _dedupe_end_latest(all_pts)
    vals = {end: _as_float(p.get("val")) for end, p in by_end.items()}
    fy: dict[str, int | None] = {}
    for end, p in by_end.items():
        raw = p.get("fy")
        if isinstance(raw, int):
            fy[end] = raw
        elif isinstance(raw, str) and raw.isdigit():
            fy[end] = int(raw)
        else:
            fy[end] = None
    return vals, fy


def _as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _merge_annual(us_gaap: dict[str, Any]) -> list[dict[str, Any]]:
    revenue, rev_fy = _revenue_series(us_gaap)
    if not revenue:
        return []

    gross = _val_map_for_tag(us_gaap, ("GrossProfit",), min_duration_days=350)
    op_inc = _val_map_for_tag(us_gaap, ("OperatingIncomeLoss",), min_duration_days=350)
    net = _val_map_for_tag(
        us_gaap,
        ("NetIncomeLoss", "ProfitLoss"),
        min_duration_days=350,
    )
    assets = _val_map_for_tag(us_gaap, ("Assets",))
    liab = _val_map_for_tag(us_gaap, ("Liabilities",))
    equity = _val_map_for_tag(us_gaap, ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"))
    ocf = _val_map_for_tag(
        us_gaap,
        ("NetCashProvidedByUsedInOperatingActivities",),
        min_duration_days=350,
    )
    capex = _val_map_for_tag(
        us_gaap,
        ("PaymentsToAcquirePropertyPlantAndEquipment",),
        min_duration_days=350,
    )

    # EPS often USD/shares
    eps_dil: dict[str, float | None] = {}
    _eps_tag, eps_node = _first_tag(us_gaap, ("EarningsPerShareDiluted",))
    if eps_node:
        for unit_key, pts in (eps_node.get("units") or {}).items():
            if not isinstance(pts, list):
                continue
            if "share" not in unit_key.lower() and unit_key not in ("USD/shares", "pure"):
                continue
            fy_pts = _fy_10k_points(pts, min_duration_days=350)
            for end, p in _dedupe_end_latest(fy_pts).items():
                eps_dil[end] = _as_float(p.get("val"))

    debt_lt = _val_map_for_tag(us_gaap, ("LongTermDebt", "LongTermDebtNoncurrent"))
    debt_st = _val_map_for_tag(us_gaap, ("DebtCurrent", "ShortTermBorrowings"))

    rows: list[dict[str, Any]] = []
    for end in sorted(revenue.keys(), reverse=True):
        rev = revenue.get(end)
        if rev is None:
            continue
        gp = gross.get(end)
        op = op_inc.get(end)
        ni = net.get(end)
        ast = assets.get(end)
        lb = liab.get(end)
        eq = equity.get(end)
        oc = ocf.get(end)
        cx = capex.get(end)
        ep = eps_dil.get(end)
        dlt = debt_lt.get(end)
        dst = debt_st.get(end)
        total_debt = None
        if dlt is not None or dst is not None:
            total_debt = (dlt or 0.0) + (dst or 0.0)

        fcf = None
        if oc is not None and cx is not None:
            fcf = oc - abs(cx)

        row: dict[str, Any] = {
            "period_end": end,
            "fiscal_year": rev_fy.get(end),
            "revenue": rev,
            "gross_profit": gp,
            "operating_income": op,
            "net_income": ni,
            "eps_diluted": ep,
            "total_assets": ast,
            "total_liabilities": lb,
            "shareholders_equity": eq,
            "operating_cash_flow": oc,
            "capex": cx,
            "free_cash_flow": fcf,
            "total_debt": total_debt,
        }
        if rev and rev != 0:
            row["gross_margin"] = (gp / rev) if gp is not None else None
            row["operating_margin"] = (op / rev) if op is not None else None
            row["net_margin"] = (ni / rev) if ni is not None else None
        else:
            row["gross_margin"] = row["operating_margin"] = row["net_margin"] = None
        if eq not in (None, 0) and ni is not None:
            row["roe"] = ni / eq
        else:
            row["roe"] = None
        rows.append(row)

    rows.sort(key=lambda r: str(r["period_end"]), reverse=True)
    return rows


def fetch_company_metrics(ticker: str, max_years: int = 5) -> dict[str, Any]:
    """
    Return structured annual metrics from SEC Company Facts (network).

    This does **not** read files under ``data/raw``; use ingest + analysis for narrative AI.
    Requires network and a descriptive SEC_USER_AGENT in production.
    """
    with httpx.Client() as client:
        cik = ticker_to_cik(ticker, client=client)
        if not cik:
            return {"error": f"Unknown ticker {ticker!r} (not in SEC company tickers map)"}

        url = _cik_url_path(cik)
        try:
            r = client.get(url, headers=_headers(), timeout=45.0)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPStatusError as e:
            logger.warning("SEC company facts HTTP error: %s", e)
            return {"error": f"SEC returned {e.response.status_code} for CIK {cik}"}
        except httpx.HTTPError as e:
            logger.warning("SEC company facts request failed: %s", e)
            return {"error": "Could not reach SEC data.sec.gov"}

    entity = str(payload.get("entityName", "")).strip()
    us_gaap = payload.get("facts", {}).get("us-gaap")
    if not isinstance(us_gaap, dict):
        return {
            "ticker": ticker.upper(),
            "cik": cik,
            "entity_name": entity or None,
            "error": "No us-gaap facts in SEC response",
        }

    annual = _merge_annual(us_gaap)[:max_years]

    latest = annual[0] if annual else None
    yoy_revenue_growth: float | None = None
    if latest and len(annual) > 1:
        prev = annual[1]
        r0, r1 = latest.get("revenue"), prev.get("revenue")
        if isinstance(r0, (int, float)) and isinstance(r1, (int, float)) and r1:
            yoy_revenue_growth = (r0 - r1) / r1

    return {
        "ticker": ticker.upper(),
        "cik": cik,
        "entity_name": entity or None,
        "currency": "USD",
        "source": "https://data.sec.gov/api/xbrl/companyfacts/",
        "annual": annual,
        "latest": latest,
        "yoy_revenue_growth": yoy_revenue_growth,
    }

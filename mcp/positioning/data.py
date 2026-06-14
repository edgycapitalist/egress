"""Free positioning-data backend for the Positioning MCP.

The functions here are deliberately plain Python: no ADK, no MCP SDK, no cloud.
They give Egress a first-class source for peer-crowding assumptions without paid
positioning feeds. Source precedence is:

1. User-uploaded holdings CSV (explicit user evidence).
2. SEC Form 13F structured-data lookup (opt-in, no API key, cached and throttled).
3. Curated historical episode fixtures.
4. Deterministic synthetic assumptions.

SEC access is gated by ``EGRESS_ENABLE_SEC_EDGAR`` or ``SEC_USER_AGENT`` so the
offline test suite never touches the network by accident. When enabled, calls use
SEC's public data with a lower-than-guidance internal rate cap, an in-process/
Postgres cache, and a small per-process cap. SEC issuer submissions do not expose
a simple "all holders of this issuer" endpoint, so SEC only turns into peer
crowding when the quarterly 13F bulk data yields actual holder rows for a CUSIP.
Otherwise it records the SEC lookup as evidence and falls through to curated/
synthetic assumptions for the actual peer-crowding profile.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import time
import urllib.request
from datetime import UTC, datetime
from typing import Any

import numpy as np

_log = logging.getLogger(__name__)

EvidenceSource = str
Confidence = str

_SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# SEC fair-access guidance is 10 requests/second. Use a lower cap internally.
SEC_MIN_CALL_SPACING_S = float(os.environ.get("SEC_EDGAR_MIN_CALL_SPACING_S", "0.35"))
SEC_MAX_CALLS_PER_RUN = int(os.environ.get("SEC_EDGAR_MAX_CALLS_PER_RUN", "4"))
_LAST_SEC_TS = 0.0
_PROC_SEC_CALLS = 0
_SEC_RATE_LIMITED = False

_MEMO: dict[str, dict[str, Any]] = {}


def _sec13f_module():
    """Import the local 13F parser in package and path-script MCP modes."""
    try:
        from mcp.positioning import sec13f
    except Exception:
        import sec13f  # type: ignore[no-redef]

    return sec13f


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _symbol(symbol: str | None) -> str:
    return (symbol or "").strip().upper() or "UNKNOWN"


def _symbol_seed(symbol: str, salt: str = "") -> int:
    digest = hashlib.sha256(f"{symbol.upper()}:{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rng(symbol: str, salt: str = "") -> np.random.Generator:
    return np.random.default_rng(_symbol_seed(symbol, salt))


def _sec_enabled() -> bool:
    enabled = os.environ.get("EGRESS_ENABLE_SEC_EDGAR", "").lower() in {"1", "true", "yes"}
    return enabled or bool(os.environ.get("SEC_USER_AGENT"))


def _sec_user_agent() -> str:
    return (
        os.environ.get("SEC_USER_AGENT")
        or "EgressAI/0.4 product-accuracy research contact=unset"
    )


def _ensure_tables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mcp_cache ("
        "provider text NOT NULL, cache_key text NOT NULL, "
        "payload jsonb NOT NULL, fetched_at timestamptz NOT NULL DEFAULT now(), "
        "PRIMARY KEY (provider, cache_key))"
    )


def _cache_get(provider: str, key: str) -> dict[str, Any] | None:
    memo = _MEMO.get(f"{provider}|{key}")
    if memo is not None:
        return memo
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            _ensure_tables(conn)
            row = conn.execute(
                "SELECT payload FROM mcp_cache WHERE provider = %s AND cache_key = %s",
                (provider, key),
            ).fetchone()
        if row:
            payload = row[0]
            _MEMO[f"{provider}|{key}"] = payload
            return payload
    except Exception:
        return None
    return None


def _cache_put(provider: str, key: str, payload: dict[str, Any]) -> None:
    _MEMO[f"{provider}|{key}"] = payload
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return
    try:
        import psycopg
        from psycopg.types.json import Json

        with psycopg.connect(dsn, connect_timeout=3) as conn:
            _ensure_tables(conn)
            conn.execute(
                "INSERT INTO mcp_cache (provider, cache_key, payload, fetched_at) "
                "VALUES (%s, %s, %s, now()) "
                "ON CONFLICT (provider, cache_key) DO UPDATE "
                "SET payload = EXCLUDED.payload, fetched_at = now()",
                (provider, key, Json(payload)),
            )
            conn.commit()
    except Exception:
        return


def _sec_get_json(url: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _sec_user_agent(),
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 (SEC HTTPS)
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _sec_get_bytes(url: str, label: str) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _sec_user_agent(),
            "Accept": "application/zip, application/octet-stream",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (SEC HTTPS)
            return resp.read()
    except Exception:
        _log.warning("SEC EDGAR request failed for %s - serving fallback", label)
        return None


def _sec_call(cache_key: str, url: str, label: str) -> dict[str, Any] | None:
    """Cached, throttled SEC JSON call. Never raises."""
    global _LAST_SEC_TS, _PROC_SEC_CALLS, _SEC_RATE_LIMITED
    cached = _cache_get("sec_edgar", cache_key)
    if cached is not None:
        return cached
    if not _sec_enabled() or _SEC_RATE_LIMITED:
        return None
    if _PROC_SEC_CALLS >= SEC_MAX_CALLS_PER_RUN:
        _log.warning(
            "SEC EDGAR per-run cap (%d) reached - serving fallback for %s",
            SEC_MAX_CALLS_PER_RUN,
            label,
        )
        return None
    spacing = SEC_MIN_CALL_SPACING_S - (time.monotonic() - _LAST_SEC_TS)
    if spacing > 0:
        time.sleep(spacing)
    payload = _sec_get_json(url)
    _LAST_SEC_TS = time.monotonic()
    if payload is None:
        _log.warning("SEC EDGAR request failed for %s - serving fallback", label)
        return None
    if payload.get("error") or payload.get("status") == 429:
        _SEC_RATE_LIMITED = True
        _log.warning("SEC EDGAR limited request for %s - serving fallback", label)
        return None
    _PROC_SEC_CALLS += 1
    normalised = {"fetched_at": _now_iso(), "payload": payload}
    _cache_put("sec_edgar", cache_key, normalised)
    return normalised


def _sec_binary_call(cache_key: str, url: str, label: str) -> bytes | None:
    """Throttled SEC binary call. Local ZIP cache is handled by ``sec13f``."""
    global _LAST_SEC_TS, _PROC_SEC_CALLS, _SEC_RATE_LIMITED
    if not _sec_enabled() or _SEC_RATE_LIMITED:
        return None
    if _PROC_SEC_CALLS >= SEC_MAX_CALLS_PER_RUN:
        _log.warning(
            "SEC EDGAR per-run cap (%d) reached - serving fallback for %s",
            SEC_MAX_CALLS_PER_RUN,
            label,
        )
        return None
    spacing = SEC_MIN_CALL_SPACING_S - (time.monotonic() - _LAST_SEC_TS)
    if spacing > 0:
        time.sleep(spacing)
    payload = _sec_get_bytes(url, label)
    _LAST_SEC_TS = time.monotonic()
    if payload is None:
        return None
    _PROC_SEC_CALLS += 1
    return payload


def _sec_lookup_company(symbol: str) -> dict[str, Any] | None:
    data = _sec_call("company_tickers", _SEC_COMPANY_TICKERS_URL, "company_tickers")
    payload = (data or {}).get("payload") or {}
    for row in payload.values():
        if str(row.get("ticker", "")).upper() == symbol:
            cik = int(row.get("cik_str", 0))
            return {
                "symbol": symbol,
                "cik": f"{cik:010d}",
                "name": row.get("title") or symbol,
                "as_of": (data or {}).get("fetched_at"),
            }
    return None


def _sec_company_submissions(cik: str) -> dict[str, Any] | None:
    return _sec_call(
        f"submissions:{cik}",
        _SEC_SUBMISSIONS_URL.format(cik=cik),
        f"submissions {cik}",
    )


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    is_pct = text.endswith("%")
    if is_pct:
        text = text[:-1]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed / 100.0 if is_pct else parsed


def _row_get(row: dict[str, Any], *names: str) -> Any:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _liquidity(symbol: str) -> dict[str, Any]:
    """Best-effort ADV/free-float context. Never touches paid data."""
    try:
        from mcp.market_data.data import get_liquidity_profile

        liq = get_liquidity_profile(symbol)
        return {
            "adv": max(1, int(liq.get("adv") or 1_000_000)),
            "free_float": max(1, int(liq.get("free_float") or 30_000_000)),
            "source": liq.get("source", "synthetic"),
        }
    except Exception:
        return {"adv": 1_000_000, "free_float": 30_000_000, "source": "synthetic"}


def ingest_user_holdings_csv(csv_text: str, instrument: str) -> dict[str, Any]:
    """Parse an uploaded holdings CSV into a normalised holder snapshot.

    Accepted columns are intentionally flexible: ``symbol``/``ticker``,
    ``manager``/``holder``/``fund``, ``shares``/``shares_held``,
    ``market_value``/``value_usd``, ``pct_adv``/``position_pct_adv``, and optional
    stress columns such as ``leverage_sensitivity`` or ``redemption_pressure``.
    Rows with no symbol column are assumed to belong to the requested instrument.
    """
    sym = _symbol(instrument)
    text = (csv_text or "").strip()
    if not text:
        return _empty_snapshot(sym, "user_upload", "low", "No holdings CSV was supplied.")

    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for raw in reader:
        row_symbol = _row_get(raw, "symbol", "ticker", "instrument")
        if row_symbol and str(row_symbol).strip().upper() != sym:
            continue
        shares = _safe_number(_row_get(raw, "shares", "shares_held", "quantity"))
        pct_adv = _safe_number(
            _row_get(raw, "pct_adv", "position_pct_adv", "avg_peer_position_pct_adv")
        )
        market_value = _safe_number(_row_get(raw, "market_value", "value", "value_usd"))
        rows.append(
            {
                "holder": str(
                    _row_get(raw, "manager", "holder", "fund", "filer", "name")
                    or "Uploaded holder"
                ).strip(),
                "shares": int(shares) if shares is not None and shares >= 0 else None,
                "market_value": market_value,
                "pct_adv": pct_adv,
                "as_of": str(_row_get(raw, "as_of", "date", "period") or "").strip() or None,
                "leverage_sensitivity": _safe_number(_row_get(raw, "leverage_sensitivity")),
                "redemption_pressure": _safe_number(_row_get(raw, "redemption_pressure")),
                "etf_flow_pressure": _safe_number(_row_get(raw, "etf_flow_pressure")),
            }
        )

    if not rows:
        return _empty_snapshot(
            sym,
            "user_upload",
            "low",
            "CSV parsed, but no rows matched the requested instrument.",
        )

    return _snapshot_from_rows(
        sym,
        rows,
        source="user_upload",
        confidence="high",
        notes="User-uploaded holdings take precedence over public or inferred evidence.",
    )


def _empty_snapshot(
    symbol: str, source: EvidenceSource, confidence: Confidence, notes: str
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "source": source,
        "confidence": confidence,
        "as_of": None,
        "holder_count": 0,
        "total_shares": 0,
        "average_position_shares": 0,
        "avg_position_pct_adv": 0.0,
        "overlap_pct": 0.0,
        "holders": [],
        "notes": notes,
    }


def _snapshot_from_rows(
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    source: EvidenceSource,
    confidence: Confidence,
    notes: str,
) -> dict[str, Any]:
    liq = _liquidity(symbol)
    shares = [int(row["shares"]) for row in rows if row.get("shares") is not None]
    pct_advs = [float(row["pct_adv"]) for row in rows if row.get("pct_adv") is not None]
    total_shares = sum(shares)
    avg_shares = int(total_shares / len(shares)) if shares else 0
    avg_pct_adv = (
        sum(pct_advs) / len(pct_advs)
        if pct_advs
        else (avg_shares / liq["adv"] if avg_shares else 0.0)
    )
    overlap = _clamp(total_shares / liq["free_float"]) if total_shares else _clamp(avg_pct_adv)
    as_of_values = sorted({row["as_of"] for row in rows if row.get("as_of")})
    return {
        "symbol": symbol,
        "source": source,
        "confidence": confidence,
        "as_of": as_of_values[-1] if as_of_values else None,
        "holder_count": len(rows),
        "total_shares": total_shares,
        "average_position_shares": avg_shares,
        "avg_position_pct_adv": round(avg_pct_adv, 6),
        "overlap_pct": round(overlap, 6),
        "holders": rows[:50],
        "notes": notes,
    }


_CURATED_POSITIONING: dict[str, dict[str, Any]] = {
    "CVNA": {
        "source": "curated_fixture",
        "confidence": "medium",
        "as_of": "2022-12",
        "holder_count": 18,
        "total_shares": 42_000_000,
        "average_position_shares": 2_333_333,
        "avg_position_pct_adv": 0.19,
        "overlap_pct": 0.47,
        "leverage_sensitivity": 0.58,
        "redemption_pressure": 0.52,
        "etf_flow_pressure": 0.18,
        "notes": "Curated late-2022 crowded-unwind assumption bundle for the demo episode.",
    },
    "SIVB": {
        "source": "curated_fixture",
        "confidence": "medium",
        "as_of": "2023-03",
        "holder_count": 14,
        "total_shares": 18_000_000,
        "average_position_shares": 1_285_714,
        "avg_position_pct_adv": 0.99,
        "overlap_pct": 0.31,
        "leverage_sensitivity": 0.50,
        "redemption_pressure": 0.68,
        "etf_flow_pressure": 0.12,
        "notes": "Curated bank-run stress bundle; high average holder size relative to ADV.",
    },
    "AAPL": {
        "source": "curated_fixture",
        "confidence": "medium",
        "as_of": "2024",
        "holder_count": 40,
        "total_shares": 850_000_000,
        "average_position_shares": 21_250_000,
        "avg_position_pct_adv": 0.39,
        "overlap_pct": 0.06,
        "leverage_sensitivity": 0.15,
        "redemption_pressure": 0.16,
        "etf_flow_pressure": 0.22,
        "notes": "Curated mega-cap ownership dispersion case; crowding is broad but liquid.",
    },
    "SPY": {
        "source": "curated_fixture",
        "confidence": "medium",
        "as_of": "2024",
        "holder_count": 35,
        "total_shares": 120_000_000,
        "average_position_shares": 3_428_571,
        "avg_position_pct_adv": 0.05,
        "overlap_pct": 0.13,
        "leverage_sensitivity": 0.18,
        "redemption_pressure": 0.18,
        "etf_flow_pressure": 0.45,
        "notes": "Curated broad ETF case; ETF-flow pressure matters more than issuer crowding.",
    },
}


def _curated_snapshot(symbol: str) -> dict[str, Any] | None:
    fixture = _CURATED_POSITIONING.get(symbol)
    if fixture is None:
        return None
    return {
        "symbol": symbol,
        "holders": [],
        **fixture,
    }


def _synthetic_snapshot(symbol: str) -> dict[str, Any]:
    rng = _rng(symbol, "positioning")
    liq = _liquidity(symbol)
    holder_count = int(rng.integers(6, 18))
    avg_pct_adv = float(rng.uniform(0.025, 0.075))
    avg_shares = int(avg_pct_adv * liq["adv"])
    total_shares = avg_shares * holder_count
    overlap = _clamp(total_shares / liq["free_float"])
    return {
        "symbol": symbol,
        "source": "synthetic_assumption",
        "confidence": "low",
        "as_of": None,
        "holder_count": holder_count,
        "total_shares": total_shares,
        "average_position_shares": avg_shares,
        "avg_position_pct_adv": round(avg_pct_adv, 6),
        "overlap_pct": round(overlap, 6),
        "holders": [],
        "notes": "Deterministic fallback assumption; not evidence-backed.",
    }


def _sec13f_holder_snapshot(
    symbol: str,
    *,
    period: str,
    cusip: str = "",
) -> dict[str, Any] | None:
    sec13f = _sec13f_module()
    resolution = sec13f.resolve_cusip(symbol, cusip)
    if resolution is None:
        return {
            **_empty_snapshot(
                symbol,
                "sec_edgar",
                "low",
                "SEC 13F lookup needs a CUSIP. Enter one, or use a demo symbol with "
                "a curated ticker-to-CUSIP mapping.",
            ),
            "cusip": None,
            "sec_13f_lookup": "missing_cusip",
        }

    quarter = sec13f.period_to_quarter(period)
    cache_key = sec13f.holder_cache_key(resolution.cusip, quarter)
    cached = _cache_get("sec_13f", cache_key)
    if isinstance(cached, dict) and isinstance(cached.get("rows"), list):
        payload = cached
    else:
        label = f"13F structured data {quarter.key}"
        payload = sec13f.get_13f_holder_rows(
            resolution.cusip,
            period=period,
            user_agent=_sec_user_agent(),
            fetch_bytes=lambda url: _sec_binary_call(cache_key, url, label),
        )
        if isinstance(payload, dict) and payload.get("rows"):
            _cache_put("sec_13f", cache_key, payload)

    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    if not rows:
        if not (isinstance(payload, dict) and payload.get("dataset_loaded")):
            return None
        return {
            **_empty_snapshot(
                symbol,
                "sec_edgar",
                "low",
                "SEC 13F structured-data lookup found no holder rows for "
                f"CUSIP {resolution.cusip} in {quarter.key}; falling back for peer "
                "assumptions.",
            ),
            "cusip": resolution.cusip,
            "cusip_source": resolution.source,
            "as_of": payload.get("period_end") if isinstance(payload, dict) else quarter.period_end,
            "sec_13f_dataset": quarter.key,
            "sec_13f_source_url": quarter.url,
        }

    snap = _snapshot_from_rows(
        symbol,
        rows,
        source="sec_edgar",
        confidence="medium",
        notes=(
            "SEC Form 13F structured data filtered by CUSIP "
            f"{resolution.cusip} ({resolution.source}). 13F is free public holder "
            "data, reported quarterly with a delay."
        ),
    )
    snap["cusip"] = resolution.cusip
    snap["cusip_source"] = resolution.source
    snap["sec_13f_dataset"] = payload.get("dataset") if isinstance(payload, dict) else quarter.key
    snap["sec_13f_source_url"] = (
        payload.get("source_url") if isinstance(payload, dict) else quarter.url
    )
    return snap


def get_sec_holder_snapshot(
    instrument: str, period: str = "recent", cusip: str = ""
) -> dict[str, Any]:
    """Best-effort SEC EDGAR issuer/holder snapshot for ``instrument``.

    No API key is required. Network access is opt-in via ``EGRESS_ENABLE_SEC_EDGAR``
    or ``SEC_USER_AGENT`` and every request is cached/throttled. SEC 13F holder rows
    are used when a CUSIP resolves; an issuer-only match is returned as lookup
    evidence, not as peer crowding.
    """
    sym = _symbol(instrument)
    if not _sec_enabled():
        return _empty_snapshot(
            sym,
            "none",
            "low",
            "SEC EDGAR access is disabled; set EGRESS_ENABLE_SEC_EDGAR=true and "
            "SEC_USER_AGENT to enable no-key public lookups.",
        )

    sec13f_snapshot = _sec13f_holder_snapshot(sym, period=period, cusip=cusip)
    if sec13f_snapshot and int(sec13f_snapshot.get("holder_count") or 0) > 0:
        return sec13f_snapshot

    company = _sec_lookup_company(sym)
    if not company:
        if sec13f_snapshot is not None:
            return sec13f_snapshot
        return _empty_snapshot(sym, "none", "low", "SEC ticker lookup returned no match.")

    submissions = _sec_company_submissions(company["cik"])
    payload = (submissions or {}).get("payload") or {}

    # Future-ready: if a cached parser or test fixture supplies holder rows or a
    # direct peer profile, surface them as SEC evidence before falling through.
    if isinstance(payload.get("holders"), list) and payload["holders"]:
        rows = [
            {
                "holder": row.get("holder") or row.get("manager") or "SEC holder",
                "shares": int(_safe_number(row.get("shares")) or 0),
                "market_value": _safe_number(row.get("market_value")),
                "pct_adv": _safe_number(row.get("pct_adv")),
                "as_of": row.get("as_of") or row.get("date") or period,
                "leverage_sensitivity": _safe_number(row.get("leverage_sensitivity")),
                "redemption_pressure": _safe_number(row.get("redemption_pressure")),
                "etf_flow_pressure": _safe_number(row.get("etf_flow_pressure")),
            }
            for row in payload["holders"]
        ]
        snap = _snapshot_from_rows(
            sym,
            rows,
            source="sec_edgar",
            confidence="medium",
            notes="SEC EDGAR holder rows from cached/free public-data parser.",
        )
        snap["issuer"] = company
        return snap

    recent = (
        ((payload.get("filings") or {}).get("recent") or {})
        if isinstance(payload, dict)
        else {}
    )
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    latest = dates[0] if dates else company.get("as_of")
    form_count = len(forms) if isinstance(forms, list) else 0
    note = (
        "SEC issuer identity resolved, but no free public holder rows were found "
        "from the 13F structured-data lookup; falling back for peer assumptions."
    )
    if sec13f_snapshot and sec13f_snapshot.get("notes"):
        note = f"{sec13f_snapshot['notes']} SEC issuer identity also resolved."
    return {
        **_empty_snapshot(
            sym,
            "sec_edgar",
            "low",
            note,
        ),
        "issuer": company,
        "cusip": (sec13f_snapshot or {}).get("cusip"),
        "cusip_source": (sec13f_snapshot or {}).get("cusip_source"),
        "as_of": latest,
        "recent_filing_count": form_count,
    }


def _profile_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(snapshot.get("peer_crowding"), dict):
        profile = dict(snapshot["peer_crowding"])
        profile.setdefault("evidence_source", snapshot.get("source", "sec_edgar"))
        profile.setdefault("confidence", snapshot.get("confidence", "medium"))
        profile.setdefault("notes", snapshot.get("notes", "Peer profile from positioning data."))
        return _normalise_profile(profile)

    holder_count = int(snapshot.get("holder_count") or 0)
    if holder_count <= 0:
        return None

    overlap = _clamp(float(snapshot.get("overlap_pct") or 0.0))
    avg_pct_adv = max(0.0, float(snapshot.get("avg_position_pct_adv") or 0.0))
    pressure = _clamp((overlap * 1.4) + (avg_pct_adv * 3.0), 0.0, 1.0)

    def avg_optional(field: str, default: float) -> float:
        values = [
            float(row[field])
            for row in snapshot.get("holders", [])
            if row.get(field) is not None
        ]
        return _clamp(sum(values) / len(values)) if values else default

    leverage = avg_optional(
        "leverage_sensitivity",
        _clamp(float(snapshot.get("leverage_sensitivity", 0.20 + pressure * 0.35))),
    )
    redemption = avg_optional(
        "redemption_pressure",
        _clamp(float(snapshot.get("redemption_pressure", 0.15 + pressure * 0.40))),
    )
    etf_flow = avg_optional(
        "etf_flow_pressure",
        _clamp(float(snapshot.get("etf_flow_pressure", 0.10 + pressure * 0.20))),
    )
    return _normalise_profile(
        {
            "case": "base",
            "peer_fund_count": holder_count,
            "overlap_pct": overlap,
            "avg_peer_position_pct_adv": avg_pct_adv,
            "shared_trigger_drawdown_pct": _clamp(0.10 - pressure * 0.055, 0.025, 0.12),
            "correlated_exit_probability": _clamp(0.28 + pressure * 0.65),
            "leverage_sensitivity": leverage,
            "redemption_pressure": redemption,
            "etf_flow_pressure": etf_flow,
            "evidence_source": snapshot.get("source", "synthetic_assumption"),
            "confidence": snapshot.get("confidence", "low"),
            "notes": snapshot.get("notes", ""),
        }
    )


def _normalise_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": profile.get("case", "base"),
        "peer_fund_count": max(0, int(profile.get("peer_fund_count") or 0)),
        "overlap_pct": _clamp(float(profile.get("overlap_pct") or 0.0)),
        "avg_peer_position_pct_adv": max(
            0.0, float(profile.get("avg_peer_position_pct_adv") or 0.0)
        ),
        "shared_trigger_drawdown_pct": _clamp(
            float(profile.get("shared_trigger_drawdown_pct") or 0.0)
        ),
        "correlated_exit_probability": _clamp(
            float(profile.get("correlated_exit_probability") or 0.0)
        ),
        "leverage_sensitivity": _clamp(float(profile.get("leverage_sensitivity") or 0.0)),
        "redemption_pressure": _clamp(float(profile.get("redemption_pressure") or 0.0)),
        "etf_flow_pressure": _clamp(float(profile.get("etf_flow_pressure") or 0.0)),
        "evidence_source": profile.get("evidence_source", "synthetic_assumption"),
        "confidence": profile.get("confidence", "low"),
        "notes": str(profile.get("notes") or ""),
    }


def _summary(
    symbol: str,
    profile: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    mode: str,
    sec_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = [
        {
            "field": "peer_crowding",
            "source": profile["evidence_source"],
            "confidence": profile["confidence"],
            "label": f"{symbol} peer crowding",
            "as_of": snapshot.get("as_of"),
            "notes": profile["notes"],
        }
    ]
    if sec_snapshot and sec_snapshot.get("source") == "sec_edgar":
        items.append(
            {
                "field": "sec_lookup",
                "source": "sec_edgar",
                "confidence": sec_snapshot.get("confidence", "low"),
                "label": f"{symbol} SEC EDGAR",
                "as_of": sec_snapshot.get("as_of"),
                "notes": sec_snapshot.get("notes", ""),
            }
        )
    return {
        "summary": (
            f"Peer-crowding assumptions for {symbol} came from "
            f"{profile['evidence_source']} ({profile['confidence']} confidence, mode {mode})."
        ),
        "items": items,
    }


def _mode(value: str | None) -> str:
    text = (value or "auto").strip().lower().replace("-", "_")
    aliases = {
        "assumption": "assumption_led",
        "assumptions": "assumption_led",
        "synthetic": "assumption_led",
        "sec": "sec_evidence",
        "edgar": "sec_evidence",
        "user": "user_upload",
        "upload": "user_upload",
        "curated": "curated_fixture",
    }
    return aliases.get(text, text)


def get_public_positioning_summary(
    instrument: str,
    period: str = "recent",
    source_mode: str = "auto",
    user_holdings_csv: str = "",
    cusip: str = "",
) -> dict[str, Any]:
    """Return the selected positioning evidence, before RunConfig shaping."""
    sym = _symbol(instrument)
    mode = _mode(source_mode)

    user_snapshot = None
    if user_holdings_csv:
        user_snapshot = ingest_user_holdings_csv(user_holdings_csv, sym)
        if user_snapshot["holder_count"] > 0:
            return {
                "symbol": sym,
                "mode": mode,
                "selected_source": "user_upload",
                "snapshot": user_snapshot,
            }

    if mode == "user_upload":
        fallback = user_snapshot or _empty_snapshot(
            sym, "user_upload", "low", "User-upload mode selected but no usable CSV was supplied."
        )
        return {"symbol": sym, "mode": mode, "selected_source": "none", "snapshot": fallback}

    if mode == "assumption_led":
        snap = _synthetic_snapshot(sym)
        return {"symbol": sym, "mode": mode, "selected_source": snap["source"], "snapshot": snap}

    sec_snapshot = None
    if mode in {"auto", "sec_evidence"}:
        sec_snapshot = get_sec_holder_snapshot(sym, period, cusip=cusip)
        if sec_snapshot.get("holder_count", 0) > 0 or sec_snapshot.get("peer_crowding"):
            return {
                "symbol": sym,
                "mode": mode,
                "selected_source": "sec_edgar",
                "snapshot": sec_snapshot,
            }

    if mode in {"auto", "sec_evidence", "curated_fixture"}:
        curated = _curated_snapshot(sym)
        if curated is not None:
            return {
                "symbol": sym,
                "mode": mode,
                "selected_source": "curated_fixture",
                "snapshot": curated,
                "sec_snapshot": sec_snapshot,
            }

    synthetic = _synthetic_snapshot(sym)
    return {
        "symbol": sym,
        "mode": mode,
        "selected_source": synthetic["source"],
        "snapshot": synthetic,
        "sec_snapshot": sec_snapshot,
    }


def get_peer_crowding_evidence(
    instrument: str,
    period: str = "recent",
    source_mode: str = "auto",
    user_holdings_csv: str = "",
    cusip: str = "",
) -> dict[str, Any]:
    """Return a RunConfig-ready peer profile plus an evidence summary."""
    summary = get_public_positioning_summary(
        instrument,
        period=period,
        source_mode=source_mode,
        user_holdings_csv=user_holdings_csv,
        cusip=cusip,
    )
    snapshot = summary["snapshot"]
    sec_snapshot = summary.get("sec_snapshot")
    profile = _profile_from_snapshot(snapshot)
    if profile is None:
        synthetic = _synthetic_snapshot(summary["symbol"])
        profile = _profile_from_snapshot(synthetic)
        snapshot = synthetic
    assert profile is not None
    evidence = _summary(
        summary["symbol"],
        profile,
        snapshot,
        mode=summary["mode"],
        sec_snapshot=sec_snapshot,
    )
    return {
        "symbol": summary["symbol"],
        "source_mode": summary["mode"],
        "selected_source": profile["evidence_source"],
        "confidence": profile["confidence"],
        "peer_crowding": profile,
        "evidence_summary": evidence,
        "holder_snapshot": snapshot,
    }

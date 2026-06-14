"""SEC Form 13F structured-data parser for free public holder evidence.

SEC's quarterly Form 13F bulk data sets are ZIP files containing TSV tables. The
issuer-level EDGAR submissions endpoint can identify a company, but the useful
"who else owns this CUSIP?" evidence lives in the 13F information table. This
module keeps that parsing isolated from the MCP/ADK wrappers so it is easy to
test offline with tiny fixture ZIPs.
"""

from __future__ import annotations

import csv
import io
import os
import re
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

SEC_13F_URL_TEMPLATE = (
    "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/"
    "{year}q{quarter}_form13f.zip"
)
SEC_13F_LOCAL_ZIP_ENV = "EGRESS_SEC13F_LOCAL_ZIP"
SEC_13F_CACHE_DIR_ENV = "EGRESS_SEC13F_CACHE_DIR"

# Demo-symbol mappings. Users can override this by entering a CUSIP in the UI.
TICKER_CUSIP_MAP: dict[str, str] = {
    "CVNA": "146869102",
    "SIVB": "78486Q101",
    "AAPL": "037833100",
    "SPY": "78462F103",
}


@dataclass(frozen=True)
class Sec13FQuarter:
    year: int
    quarter: int

    @property
    def key(self) -> str:
        return f"{self.year}q{self.quarter}"

    @property
    def period_end(self) -> str:
        month = self.quarter * 3
        day = 31 if month in {3, 12} else 30
        return f"{self.year}-{month:02d}-{day:02d}"

    @property
    def url(self) -> str:
        return SEC_13F_URL_TEMPLATE.format(year=self.year, quarter=self.quarter)


@dataclass(frozen=True)
class CusipResolution:
    cusip: str
    source: str
    symbol: str


def normalise_cusip(value: str | None) -> str:
    """Return a comparable uppercase CUSIP string with punctuation removed."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def resolve_cusip(symbol: str | None, cusip: str | None = None) -> CusipResolution | None:
    """Resolve a CUSIP from an explicit user value or the demo ticker map."""
    sym = str(symbol or "").strip().upper()
    explicit = normalise_cusip(cusip)
    if explicit:
        return CusipResolution(cusip=explicit, source="user_supplied", symbol=sym)
    mapped = TICKER_CUSIP_MAP.get(sym)
    if mapped:
        return CusipResolution(cusip=mapped, source="curated_ticker_map", symbol=sym)
    return None


def period_to_quarter(period: str | None, *, today: date | None = None) -> Sec13FQuarter:
    """Translate ``recent``, ``YYYY-Qn``, or a date-like period into a 13F quarter."""
    text = str(period or "recent").strip().lower()
    match = re.search(r"(?P<year>20\d{2})\s*[-_/]?\s*q(?P<quarter>[1-4])", text)
    if match:
        return Sec13FQuarter(int(match.group("year")), int(match.group("quarter")))

    match = re.search(r"(?P<year>20\d{2})[-_/](?P<month>\d{1,2})(?:[-_/]\d{1,2})?", text)
    if match:
        month = max(1, min(12, int(match.group("month"))))
        return Sec13FQuarter(int(match.group("year")), ((month - 1) // 3) + 1)

    # 13F is quarterly and filed with a delay. Use a conservative completed quarter
    # so "recent" does not point at a not-yet-published ZIP.
    anchor = today or datetime.now(UTC).date()
    available = anchor - timedelta(days=75)
    return Sec13FQuarter(available.year, ((available.month - 1) // 3) + 1)


def holder_cache_key(cusip: str, quarter: Sec13FQuarter) -> str:
    return f"holders:{quarter.key}:{normalise_cusip(cusip)}"


def get_13f_holder_rows(
    cusip: str,
    period: str = "recent",
    *,
    user_agent: str,
    cache_dir: str | Path | None = None,
    fetch_bytes: Callable[[str], bytes | None] | None = None,
) -> dict[str, Any]:
    """Load a quarterly 13F ZIP and return holder rows for one CUSIP.

    ``fetch_bytes`` lets the caller enforce SEC rate limits. If a local override or
    cache file exists, no network fetch is attempted.
    """
    wanted = normalise_cusip(cusip)
    quarter = period_to_quarter(period)
    raw = _dataset_bytes(quarter, user_agent, cache_dir=cache_dir, fetch_bytes=fetch_bytes)
    rows = holder_rows_from_zip(raw, wanted) if raw else []
    return {
        "cusip": wanted,
        "dataset": quarter.key,
        "dataset_loaded": raw is not None,
        "period_end": quarter.period_end,
        "source_url": quarter.url,
        "rows": rows,
        "fetched_at": _now_iso(),
    }


def holder_rows_from_zip(raw_zip: bytes, cusip: str) -> list[dict[str, Any]]:
    """Parse holder rows for ``cusip`` from a Form 13F structured-data ZIP."""
    wanted = normalise_cusip(cusip)
    if not raw_zip or not wanted:
        return []

    with zipfile.ZipFile(io.BytesIO(raw_zip)) as zf:
        info_member = _find_member(zf, ("infotable", "informationtable"))
        if info_member is None:
            return []
        submission_member = _find_member(zf, ("submission",))
        submission_by_accession = (
            _submission_lookup(_read_table(zf, submission_member)) if submission_member else {}
        )

        grouped: dict[str, dict[str, Any]] = {}
        for row in _read_table(zf, info_member):
            row_cusip = normalise_cusip(_field(row, "cusip"))
            if row_cusip != wanted:
                continue
            if _is_derivative_or_principal_amount(row):
                continue

            shares = _number(_field(row, "sshprnamt", "shares", "sharesheld"))
            if shares is None or shares <= 0:
                continue

            accession = str(
                _field(row, "accession_number", "accessionnumber", "accessionno") or ""
            ).strip()
            submission = submission_by_accession.get(accession, {})
            holder = str(
                submission.get("holder")
                or _field(row, "filingmanager_name", "manager", "holder", "name")
                or f"13F manager {accession or len(grouped) + 1}"
            ).strip()
            key = accession or holder.upper()
            current = grouped.setdefault(
                key,
                {
                    "holder": holder,
                    "shares": 0,
                    "market_value": 0.0,
                    "pct_adv": None,
                    "as_of": submission.get("as_of") or None,
                    "accession_number": accession or None,
                    "cik": submission.get("cik"),
                    "cusip": wanted,
                    "issuer": _field(row, "nameofissuer", "name_of_issuer", "issuer"),
                    "title": _field(row, "titleofclass", "title_of_class", "class"),
                    "source": "sec_13f_structured_data",
                    "leverage_sensitivity": None,
                    "redemption_pressure": None,
                    "etf_flow_pressure": None,
                },
            )
            current["shares"] = int(current["shares"] or 0) + int(shares)
            value_thousands = _number(_field(row, "value", "marketvalue", "valuex1000"))
            if value_thousands is not None:
                current["market_value"] = float(current["market_value"] or 0.0) + (
                    value_thousands * 1000.0
                )
            current["as_of"] = current.get("as_of") or _date_text(
                _field(row, "periodofreport", "reportcalendarorquarter", "report_period")
            )

    return sorted(grouped.values(), key=lambda item: int(item["shares"] or 0), reverse=True)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _dataset_bytes(
    quarter: Sec13FQuarter,
    user_agent: str,
    *,
    cache_dir: str | Path | None,
    fetch_bytes: Callable[[str], bytes | None] | None,
) -> bytes | None:
    override = os.environ.get(SEC_13F_LOCAL_ZIP_ENV)
    if override:
        path = Path(override).expanduser()
        return path.read_bytes() if path.exists() else None

    cache_path = _cache_path(quarter, cache_dir)
    if cache_path.exists():
        return cache_path.read_bytes()

    fetcher = fetch_bytes or (lambda url: _download(url, user_agent))
    raw = fetcher(quarter.url)
    if raw:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
    return raw


def _cache_path(quarter: Sec13FQuarter, cache_dir: str | Path | None) -> Path:
    root = Path(
        cache_dir
        or os.environ.get(SEC_13F_CACHE_DIR_ENV)
        or "~/.cache/egress/sec13f"
    ).expanduser()
    return root / f"{quarter.key}_form13f.zip"


def _download(url: str, user_agent: str) -> bytes | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/zip, application/octet-stream",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (SEC HTTPS)
            return resp.read()
    except Exception:
        return None


def _find_member(zf: zipfile.ZipFile, needles: tuple[str, ...]) -> str | None:
    members = zf.namelist()
    for member in members:
        name = Path(member).name.lower()
        if name.endswith((".tsv", ".txt", ".csv")) and any(n in name for n in needles):
            return member
    return None


def _read_table(zf: zipfile.ZipFile, member: str) -> list[dict[str, str]]:
    raw = zf.read(member).decode("utf-8-sig", errors="replace")
    first = raw.splitlines()[0] if raw.splitlines() else ""
    delimiter = "\t" if "\t" in first else ","
    return list(csv.DictReader(io.StringIO(raw), delimiter=delimiter))


def _norm_col(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _field(row: dict[str, Any], *names: str) -> Any:
    normalised = {_norm_col(str(k)): v for k, v in row.items()}
    for name in names:
        value = normalised.get(_norm_col(name))
        if value not in {None, ""}:
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _submission_lookup(rows: list[dict[str, str]]) -> dict[str, dict[str, str | None]]:
    lookup: dict[str, dict[str, str | None]] = {}
    for row in rows:
        accession = str(
            _field(row, "accession_number", "accessionnumber", "accessionno") or ""
        ).strip()
        if not accession:
            continue
        holder = str(
            _field(
                row,
                "filingmanager_name",
                "filingmanagername",
                "manager_name",
                "manager",
                "name",
            )
            or ""
        ).strip()
        lookup[accession] = {
            "holder": holder or None,
            "cik": str(_field(row, "cik", "filer_cik", "filercik") or "").strip() or None,
            "as_of": _date_text(
                _field(
                    row,
                    "periodofreport",
                    "reportcalendarorquarter",
                    "report_period",
                    "period",
                )
            ),
        }
    return lookup


def _is_derivative_or_principal_amount(row: dict[str, Any]) -> bool:
    put_call = str(_field(row, "putcall", "put_call") or "").strip().upper()
    if put_call in {"PUT", "CALL"}:
        return True
    amount_type = str(
        _field(row, "sshprnamttype", "ssh_prnamt_type", "sharetype") or ""
    ).strip().upper()
    return bool(amount_type and amount_type not in {"SH", "SHS", "SHRS", "SHARES"})

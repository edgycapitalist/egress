"""Offline tests for the Positioning MCP backend."""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO

import mcp.positioning.data as pos
import mcp.positioning.sec13f as sec13f
import pytest
from gateway.run_config import build_run_config
from mcp.positioning.data import (
    get_peer_crowding_evidence,
    get_public_positioning_summary,
    get_sec_holder_snapshot,
    ingest_user_holdings_csv,
)
from mcp.positioning.tools import POSITIONING_TOOLS


@pytest.fixture(autouse=True)
def _reset_positioning_state(monkeypatch):
    pos._MEMO.clear()
    pos._LAST_SEC_TS = 0.0
    pos._PROC_SEC_CALLS = 0
    pos._SEC_RATE_LIMITED = False
    monkeypatch.delenv("EGRESS_ENABLE_SEC_EDGAR", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.delenv("EGRESS_SEC13F_LOCAL_ZIP", raising=False)
    monkeypatch.delenv("EGRESS_SEC13F_CACHE_DIR", raising=False)
    yield


def _fake_13f_zip() -> bytes:
    info = "\n".join(
        [
            "ACCESSION_NUMBER\tNAMEOFISSUER\tTITLEOFCLASS\tCUSIP\tVALUE\tSSHPRNAMT\tSSHPRNAMTTYPE\tPUTCALL",
            "0001\tCARVANA CO\tCL A\t146869102\t12000\t1000000\tSH\t",
            "0001\tCARVANA CO\tCL A\t146869102\t3000\t250000\tSH\t",
            "0002\tCARVANA CO\tCL A\t146869102\t6000\t500000\tSH\t",
            "0003\tSVB FINANCIAL GROUP\tCOM\t78486Q101\t2500\t100000\tSH\t",
            "0004\tCARVANA CO\tCALL\t146869102\t1000\t10000\tSH\tCALL",
            "0005\tCARVANA CO\tNOTE\t146869102\t1000\t50\tPRN\t",
        ]
    )
    submissions = "\n".join(
        [
            "ACCESSION_NUMBER\tFILINGMANAGER_NAME\tCIK\tPERIODOFREPORT",
            "0001\tAlpha Capital\t1000001\t20221231",
            "0002\tBeta Partners\t1000002\t20221231",
            "0003\tGamma Advisors\t1000003\t20230331",
        ]
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("INFOTABLE.tsv", info)
        zf.writestr("SUBMISSION.tsv", submissions)
    return buf.getvalue()


def _write_fake_13f_zip(tmp_path) -> str:
    path = tmp_path / "2022q4_form13f.zip"
    path.write_bytes(_fake_13f_zip())
    return str(path)


def test_positioning_tools_expose_spec_signatures() -> None:
    assert {tool.name for tool in POSITIONING_TOOLS} == {
        "get_sec_holder_snapshot",
        "get_public_positioning_summary",
        "get_peer_crowding_evidence",
        "ingest_user_holdings_csv",
    }


def test_assumption_led_mode_returns_synthetic_profile_without_sec() -> None:
    evidence = get_peer_crowding_evidence("ZZZ9", source_mode="assumption-led")
    profile = evidence["peer_crowding"]
    assert evidence["selected_source"] == "synthetic_assumption"
    assert profile["peer_fund_count"] > 0
    assert profile["evidence_source"] == "synthetic_assumption"
    assert evidence["evidence_summary"]["items"][0]["source"] == "synthetic_assumption"


def test_user_csv_precedes_curated_and_sec_sources() -> None:
    csv_text = """symbol,manager,shares,as_of,leverage_sensitivity,redemption_pressure
CVNA,Alpha Fund,1200000,2024-03-31,0.7,0.6
CVNA,Beta Fund,600000,2024-03-31,0.4,0.5
AAPL,Other Fund,999,2024-03-31,0.1,0.1
"""
    snapshot = ingest_user_holdings_csv(csv_text, "CVNA")
    assert snapshot["source"] == "user_upload"
    assert snapshot["holder_count"] == 2
    assert snapshot["total_shares"] == 1_800_000

    evidence = get_peer_crowding_evidence(
        "CVNA", source_mode="auto", user_holdings_csv=csv_text
    )
    profile = evidence["peer_crowding"]
    assert evidence["selected_source"] == "user_upload"
    assert profile["peer_fund_count"] == 2
    assert profile["evidence_source"] == "user_upload"
    assert profile["confidence"] == "high"


def test_sec13f_parser_filters_and_aggregates_holder_rows() -> None:
    cvna = sec13f.holder_rows_from_zip(_fake_13f_zip(), "146869102")
    assert [row["holder"] for row in cvna] == ["Alpha Capital", "Beta Partners"]
    assert cvna[0]["shares"] == 1_250_000
    assert cvna[0]["market_value"] == 15_000_000
    assert cvna[0]["as_of"] == "2022-12-31"

    sivb = sec13f.holder_rows_from_zip(_fake_13f_zip(), "78486Q101")
    assert len(sivb) == 1
    assert sivb[0]["holder"] == "Gamma Advisors"
    assert sivb[0]["shares"] == 100_000


def test_sec13f_fixture_can_drive_sec_peer_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EGRESS_ENABLE_SEC_EDGAR", "true")
    monkeypatch.setenv("EGRESS_SEC13F_LOCAL_ZIP", _write_fake_13f_zip(tmp_path))

    def boom(_url: str):
        raise AssertionError("issuer lookup should not run when 13F rows exist")

    monkeypatch.setattr(pos, "_sec_get_json", boom)
    evidence = get_peer_crowding_evidence("CVNA", source_mode="sec_evidence", period="2022-Q4")
    profile = evidence["peer_crowding"]
    snapshot = evidence["holder_snapshot"]
    assert evidence["selected_source"] == "sec_edgar"
    assert profile["peer_fund_count"] == 2
    assert profile["evidence_source"] == "sec_edgar"
    assert snapshot["cusip"] == "146869102"
    assert snapshot["holder_count"] == 2
    assert snapshot["total_shares"] == 1_750_000
    assert "13F structured data" in profile["notes"]


def test_curated_fixture_is_used_when_sec_has_no_holder_profile(monkeypatch) -> None:
    monkeypatch.setattr(
        pos,
        "get_sec_holder_snapshot",
        lambda instrument, period="recent", cusip="": {
            "symbol": instrument,
            "source": "sec_edgar",
            "confidence": "low",
            "holder_count": 0,
            "notes": "identity only",
        },
    )
    summary = get_public_positioning_summary("CVNA", source_mode="sec_evidence")
    assert summary["selected_source"] == "curated_fixture"
    assert summary["sec_snapshot"]["source"] == "sec_edgar"

    evidence = get_peer_crowding_evidence("CVNA", source_mode="sec_evidence")
    assert evidence["selected_source"] == "curated_fixture"
    assert evidence["evidence_summary"]["items"][0]["source"] == "curated_fixture"
    assert evidence["evidence_summary"]["items"][1]["field"] == "sec_lookup"


def test_sec_lookup_only_keeps_fallback_source_label(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EGRESS_ENABLE_SEC_EDGAR", "true")
    monkeypatch.setenv("EGRESS_SEC13F_LOCAL_ZIP", _write_fake_13f_zip(tmp_path))
    monkeypatch.setattr(
        pos,
        "_sec_lookup_company",
        lambda symbol: {
            "symbol": symbol,
            "cik": "0001468691",
            "name": "Carvana Co",
            "as_of": "2026-06-14T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(pos, "_sec_company_submissions", lambda cik: {"payload": {}})

    evidence = get_peer_crowding_evidence(
        "CVNA",
        source_mode="sec_evidence",
        period="2022-Q4",
        cusip="111111111",
    )
    assert evidence["selected_source"] == "curated_fixture"
    assert evidence["peer_crowding"]["evidence_source"] == "curated_fixture"
    assert evidence["holder_snapshot"]["source"] == "curated_fixture"
    ledger = evidence["evidence_summary"]["items"]
    assert ledger[0]["field"] == "peer_crowding"
    assert ledger[0]["source"] == "curated_fixture"
    assert ledger[1]["field"] == "sec_lookup"
    assert ledger[1]["source"] == "sec_edgar"
    assert "no holder rows" in ledger[1]["notes"]


def test_sec_snapshot_is_disabled_by_default_and_never_calls_network(monkeypatch) -> None:
    def boom(_url: str):
        raise AssertionError("SEC network call attempted")

    monkeypatch.setattr(pos, "_sec_get_json", boom)
    snapshot = get_sec_holder_snapshot("CVNA")
    assert snapshot["source"] == "none"
    assert snapshot["holder_count"] == 0
    assert "disabled" in snapshot["notes"]


def test_sec_cache_hit_makes_zero_network_calls(monkeypatch) -> None:
    cached = {
        "fetched_at": "2026-06-14T00:00:00+00:00",
        "payload": {"0": {"ticker": "MOCK", "cik_str": 1234, "title": "Mock Co"}},
    }
    monkeypatch.setattr(pos, "_cache_get", lambda provider, key: cached)

    def boom(_url: str):
        raise AssertionError("SEC network call attempted")

    monkeypatch.setattr(pos, "_sec_get_json", boom)
    assert pos._sec_lookup_company("MOCK") == {
        "symbol": "MOCK",
        "cik": "0000001234",
        "name": "Mock Co",
        "as_of": "2026-06-14T00:00:00+00:00",
    }


def test_sec_per_run_cap_blocks_network(monkeypatch, caplog) -> None:
    monkeypatch.setenv("EGRESS_ENABLE_SEC_EDGAR", "true")
    monkeypatch.setattr(pos, "SEC_MAX_CALLS_PER_RUN", 0)
    monkeypatch.setattr(pos, "_cache_get", lambda provider, key: None)

    def boom(_url: str):
        raise AssertionError("SEC network call attempted")

    monkeypatch.setattr(pos, "_sec_get_json", boom)
    with caplog.at_level(logging.WARNING, logger="mcp.positioning.data"):
        snapshot = get_sec_holder_snapshot("CVNA")
    assert snapshot["source"] == "none"
    assert "per-run cap" in caplog.text


def test_sec_holder_rows_take_precedence_over_curated(monkeypatch) -> None:
    monkeypatch.setenv("EGRESS_ENABLE_SEC_EDGAR", "true")
    monkeypatch.setattr(pos, "_sec13f_holder_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        pos,
        "_sec_lookup_company",
        lambda symbol: {
            "symbol": symbol,
            "cik": "0000001234",
            "name": "Mock Co",
            "as_of": "2026-06-14T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        pos,
        "_sec_company_submissions",
        lambda cik: {
            "payload": {
                "holders": [
                    {"holder": "SEC Fund A", "shares": 250_000, "as_of": "2024-03-31"},
                    {"holder": "SEC Fund B", "shares": 150_000, "as_of": "2024-03-31"},
                ]
            }
        },
    )
    evidence = get_peer_crowding_evidence("CVNA", source_mode="sec_evidence")
    assert evidence["selected_source"] == "sec_edgar"
    assert evidence["peer_crowding"]["peer_fund_count"] == 2
    assert evidence["peer_crowding"]["evidence_source"] == "sec_edgar"


def test_gateway_build_run_config_uses_positioning_user_upload() -> None:
    csv_text = """symbol,manager,shares,as_of
CVNA,Alpha Fund,1200000,2024-03-31
CVNA,Beta Fund,600000,2024-03-31
"""
    cfg = build_run_config(
        {
            "symbol": "CVNA",
            "peer_source_mode": "user_upload",
            "user_holdings_csv": csv_text,
        }
    )
    assert cfg.scenario_mode == "user_upload"
    assert cfg.peer_crowding is not None
    assert cfg.peer_crowding.evidence_source == "user_upload"
    assert cfg.evidence_summary is not None
    assert {item.field for item in cfg.evidence_summary.items} == {
        "instrument",
        "peer_crowding",
    }


def test_gateway_build_run_config_uses_sec13f_cusip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EGRESS_ENABLE_SEC_EDGAR", "true")
    monkeypatch.setenv("EGRESS_SEC13F_LOCAL_ZIP", _write_fake_13f_zip(tmp_path))
    cfg = build_run_config(
        {
            "symbol": "CVNA",
            "peer_source_mode": "sec_evidence",
            "cusip": "146869102",
        }
    )
    assert cfg.scenario_mode == "sec_evidence"
    assert cfg.peer_crowding is not None
    assert cfg.peer_crowding.evidence_source == "sec_edgar"
    assert cfg.peer_crowding.peer_fund_count == 2
    assert cfg.evidence_summary is not None
    assert any(item.source == "sec_edgar" for item in cfg.evidence_summary.items)


def test_gateway_assumption_led_controls_remain_available() -> None:
    cfg = build_run_config(
        {
            "symbol": "CVNA",
            "peer_source_mode": "assumption_led",
            "peer_crowding": {
                "peer_fund_count": 3,
                "overlap_pct": 0.2,
                "avg_peer_position_pct_adv": 0.03,
                "shared_trigger_drawdown_pct": 0.07,
                "correlated_exit_probability": 0.4,
            },
        }
    )
    assert cfg.scenario_mode == "assumption_led"
    assert cfg.peer_crowding is not None
    assert cfg.peer_crowding.peer_fund_count == 3
    assert cfg.peer_crowding.evidence_source == "synthetic_assumption"

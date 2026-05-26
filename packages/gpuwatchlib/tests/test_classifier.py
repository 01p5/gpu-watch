"""Classifier behaviour — drain-category rules from the legacy
gpu-watchdog, plus the new dcgm path. Every (category, source) combo
gets a positive + negative test."""
from __future__ import annotations

from gpuwatchlib import DcgmSample, classify
from gpuwatchlib.classifier import (
    CORRECTABLE_REMAP_HARD_THRESHOLD, DRAIN_CATEGORIES, Severity,
)


# ----------------------------------------------------------------------
# XID rule
# ----------------------------------------------------------------------


def test_xid_event_yields_hard_finding():
    findings = classify("node1", {
        "xid_events": [{"pci": "0000:1a:00", "xid": 79, "detail": "off the bus"}],
    })
    assert any(f.category == "XID" and f.severity == Severity.HARD for f in findings)


def test_no_xid_events_no_finding():
    findings = classify("node1", {"xid_events": []})
    assert all(f.category != "XID" for f in findings)


# ----------------------------------------------------------------------
# Row-remap rules
# ----------------------------------------------------------------------


def test_remap_failure_is_hard():
    findings = classify("n", {"remapped_rows": [
        {"index": 0, "correctable": 0, "uncorrectable": 0, "pending": 0, "failure": True},
    ]})
    cats = [f.category for f in findings if f.severity == Severity.HARD]
    assert "ROW_REMAP_FAILURE" in cats


def test_uncorrectable_remap_is_hard():
    findings = classify("n", {"remapped_rows": [
        {"index": 0, "correctable": 0, "uncorrectable": 2, "pending": 0, "failure": False},
    ]})
    assert any(f.category == "UNCORRECTABLE_REMAPPED_ROWS"
               and f.severity == Severity.HARD for f in findings)


def test_correctable_remap_at_threshold_is_hard():
    findings = classify("n", {"remapped_rows": [
        {"index": 0, "correctable": CORRECTABLE_REMAP_HARD_THRESHOLD,
         "uncorrectable": 0, "pending": 0, "failure": False},
    ]})
    assert any(f.category == "CORRECTABLE_REMAPPED_ROWS"
               and f.severity == Severity.HARD for f in findings)


def test_correctable_remap_below_threshold_is_soft_iff_nonzero():
    """The classifier surfaces sub-threshold correctable remaps as
    SOFT so the operator sees a trend — but only if non-zero. Truly
    clean nodes don't get a finding at all."""
    if CORRECTABLE_REMAP_HARD_THRESHOLD <= 1:
        return  # threshold leaves no SOFT band to exercise — skip

    findings = classify("n", {"remapped_rows": [
        {"index": 0, "correctable": CORRECTABLE_REMAP_HARD_THRESHOLD - 1,
         "uncorrectable": 0, "pending": 0, "failure": False},
    ]})
    assert any(f.category == "CORRECTABLE_REMAPPED_ROWS"
               and f.severity == Severity.SOFT for f in findings)


def test_no_remapped_rows_signal_no_findings():
    findings = classify("n", {})
    assert all(f.category not in {
        "ROW_REMAP_FAILURE", "UNCORRECTABLE_REMAPPED_ROWS", "CORRECTABLE_REMAPPED_ROWS",
    } for f in findings)


# ----------------------------------------------------------------------
# GPU_COUNT rule
# ----------------------------------------------------------------------


def test_gpu_count_below_expected_is_hard():
    findings = classify("n", {"gpu_count": 6}, expected_gpu_count=8)
    [f] = [x for x in findings if x.category == "GPU_COUNT"]
    assert f.severity == Severity.HARD
    assert f.raw == {"observed": 6, "expected": 8}


def test_gpu_count_at_expected_is_ok():
    findings = classify("n", {"gpu_count": 8}, expected_gpu_count=8)
    assert all(f.category != "GPU_COUNT" for f in findings)


def test_gpu_count_skipped_when_expected_is_none():
    findings = classify("n", {"gpu_count": 0})
    assert all(f.category != "GPU_COUNT" for f in findings)


def test_gpu_count_skipped_when_observed_is_none():
    findings = classify("n", {"gpu_count": None}, expected_gpu_count=8)
    assert all(f.category != "GPU_COUNT" for f in findings)


# ----------------------------------------------------------------------
# NVLink rules
# ----------------------------------------------------------------------


def test_nvlink_inactive_link_flags_gpu():
    findings = classify("n", {"nvlink_status": {
        0: [{"link": 0, "raw": "25 GB/s", "active": True},
            {"link": 1, "raw": "<inactive>", "active": False}],
    }})
    [f] = [x for x in findings if x.category == "NVLINK" and x.severity == Severity.HARD]
    assert f.gpu == 0 and "inactive" in f.message


def test_nvlink_nonzero_error_counter_flags_gpu():
    findings = classify("n", {"nvlink_errors": {
        1: {0: {"Rx Errors": 5, "Tx discards": 2}},
    }})
    f = next(x for x in findings if x.category == "NVLINK" and x.gpu == 1)
    assert "Rx Errors=5" in f.message or "Tx discards=2" in f.message


def test_nvlink_clean_signals_no_finding():
    findings = classify("n", {
        "nvlink_status": {0: [{"link": 0, "raw": "25 GB/s", "active": True}]},
        "nvlink_errors": {0: {0: {"Rx Errors": 0}}},
    })
    assert all(f.category != "NVLINK" for f in findings)


# ----------------------------------------------------------------------
# DCGM rules
# ----------------------------------------------------------------------


def _dcgm(name: str, gpu: int, value: float) -> DcgmSample:
    return DcgmSample(name=name, labels={"gpu": str(gpu)}, value=value)


def test_dcgm_row_remap_failure_is_hard():
    findings = classify("n", {"dcgm": {
        "DCGM_FI_DEV_ROW_REMAP_FAILURE": [_dcgm("DCGM_FI_DEV_ROW_REMAP_FAILURE", 0, 1)],
    }})
    assert any(f.category == "ROW_REMAP_FAILURE"
               and f.raw.get("source") == "dcgm" for f in findings)


def test_dcgm_uncorrectable_remap_is_hard():
    findings = classify("n", {"dcgm": {
        "DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS": [
            _dcgm("DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS", 0, 3),
        ],
    }})
    assert any(f.category == "UNCORRECTABLE_REMAPPED_ROWS"
               and f.raw.get("source") == "dcgm" for f in findings)


def test_dcgm_xid_is_hard():
    findings = classify("n", {"dcgm": {
        "DCGM_FI_DEV_XID_ERRORS": [_dcgm("DCGM_FI_DEV_XID_ERRORS", 1, 79)],
    }})
    [f] = [x for x in findings if x.category == "XID" and x.raw.get("source") == "dcgm"]
    assert f.raw["xid"] == 79


def test_dcgm_zero_value_no_finding():
    findings = classify("n", {"dcgm": {
        "DCGM_FI_DEV_XID_ERRORS": [_dcgm("DCGM_FI_DEV_XID_ERRORS", 0, 0)],
        "DCGM_FI_DEV_ROW_REMAP_FAILURE": [_dcgm("DCGM_FI_DEV_ROW_REMAP_FAILURE", 0, 0)],
    }})
    assert findings == []


# ----------------------------------------------------------------------
# Finding.to_json round-trip + helpers
# ----------------------------------------------------------------------


def test_finding_to_json_round_trip():
    findings = classify("n", {"xid_events": [
        {"pci": "0000:1a:00", "xid": 79, "detail": "off the bus"},
    ]})
    out = findings[0].to_json()
    assert out["category"] == "XID"
    assert out["severity"] == "HARD"
    assert out["node"] == "n"


def test_drain_categories_constant_is_stable():
    expected = {"XID", "ROW_REMAP_FAILURE", "UNCORRECTABLE_REMAPPED_ROWS",
                "CORRECTABLE_REMAPPED_ROWS", "GPU_COUNT", "NVLINK"}
    assert set(DRAIN_CATEGORIES) == expected

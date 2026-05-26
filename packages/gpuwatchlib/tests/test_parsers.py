"""Pure-parser tests for the nvidia-smi + nvlink + xid outputs.

These are the riskiest part of the SDK — formats vary slightly between
driver versions and quirks (``[N/A]`` for unsupported counters, units
suffixes, inactive-link wording) hide bugs that only show up on real
nodes. Lock the behaviour here so the dashboard's classifier can trust
its inputs.
"""
from __future__ import annotations

import pytest

from gpuwatchlib import (
    parse_nvlink_errors, parse_nvlink_status,
    parse_query_gpu_csv, parse_remapped_rows_csv, parse_xid_log,
)


# ----------------------------------------------------------------------
# parse_query_gpu_csv
# ----------------------------------------------------------------------


def test_query_gpu_parses_typical_eight_h100_row():
    csv = (
        "0, NVIDIA H100 80GB HBM3, GPU-aaa, 35, 65.55, 0, 81920, 0, 0, 0, 0\n"
        "1, NVIDIA H100 80GB HBM3, GPU-bbb, 41, 320.00, 12345, 81920, 75, 80, 0, 12\n"
    )
    rows = parse_query_gpu_csv(csv)
    assert len(rows) == 2
    assert rows[0]["index"] == 0
    assert rows[0]["name"] == "NVIDIA H100 80GB HBM3"
    assert rows[0]["temperature_c"] == 35
    assert rows[0]["power_w"] == 65.55
    assert rows[0]["memory_used_mib"] == 0
    assert rows[0]["memory_total_mib"] == 81920
    assert rows[1]["util_gpu_pct"] == 75
    assert rows[1]["util_mem_pct"] == 80
    assert rows[1]["ecc_corrected_total"] == 12


def test_query_gpu_handles_na_cells():
    """Driver/hw combinations sometimes report ``[N/A]`` for ECC. We
    want None there, not 0 — the classifier needs to distinguish
    "unmeasurable" from "zero errors"."""
    csv = "0, H100, uuid, 30, 50, 0, 81920, 0, 0, [N/A], [N/A]\n"
    [row] = parse_query_gpu_csv(csv)
    assert row["ecc_uncorrected_total"] is None
    assert row["ecc_corrected_total"] is None


def test_query_gpu_skips_blank_and_truncated_lines():
    csv = "\n0, x, u, 30, 50, 0, 81920, 0, 0, 0, 0\ntoo,short\n"
    rows = parse_query_gpu_csv(csv)
    assert [r["index"] for r in rows] == [0]


def test_query_gpu_returns_empty_for_empty_input():
    assert parse_query_gpu_csv("") == []
    assert parse_query_gpu_csv("\n\n") == []


# ----------------------------------------------------------------------
# parse_remapped_rows_csv
# ----------------------------------------------------------------------


def test_remapped_rows_parses_yes_no_flag():
    csv = "0, 0, 0, 0, No\n1, 5, 0, 0, No\n2, 0, 2, 0, Yes\n"
    rows = parse_remapped_rows_csv(csv)
    assert [r["failure"] for r in rows] == [False, False, True]
    assert rows[1]["correctable"] == 5
    assert rows[2]["uncorrectable"] == 2


def test_remapped_rows_coerces_strange_yes_variants():
    csv = "0, 0, 0, 0, yes\n1, 0, 0, 0, TRUE\n2, 0, 0, 0, 1\n"
    rows = parse_remapped_rows_csv(csv)
    assert all(r["failure"] for r in rows)


def test_remapped_rows_treats_blank_count_as_zero():
    csv = "0, , , 0, No\n"
    [row] = parse_remapped_rows_csv(csv)
    assert row["correctable"] == 0 and row["uncorrectable"] == 0


# ----------------------------------------------------------------------
# parse_nvlink_status
# ----------------------------------------------------------------------


def test_nvlink_status_marks_inactive_links():
    text = """\
GPU 0: NVIDIA H100 (UUID: GPU-xxx)
\t Link 0: 25 GB/s
\t Link 1: 25 GB/s
\t Link 2: <inactive>
GPU 1: NVIDIA H100 (UUID: GPU-yyy)
\t Link 0: 25 GB/s
"""
    out = parse_nvlink_status(text)
    assert set(out.keys()) == {0, 1}
    assert len(out[0]) == 3
    actives = [link["active"] for link in out[0]]
    assert actives == [True, True, False]
    assert out[0][2]["raw"] == "<inactive>"
    assert len(out[1]) == 1


def test_nvlink_status_handles_no_link_lines_for_gpu():
    """If the controller emits a GPU header but no Link lines (a quirk
    seen on misbehaving drivers), we still record the GPU with an
    empty list so the classifier can flag it separately."""
    text = "GPU 0: NVIDIA H100 (UUID: GPU-xxx)\n"
    out = parse_nvlink_status(text)
    assert out == {0: []}


def test_nvlink_status_recognises_down_disabled_off():
    for word in ("down", "disabled", "off"):
        out = parse_nvlink_status(f"GPU 0:\n\t Link 0: {word}\n")
        assert out[0][0]["active"] is False, word


# ----------------------------------------------------------------------
# parse_nvlink_errors
# ----------------------------------------------------------------------


def test_nvlink_errors_extracts_only_allowed_counter_lines():
    """Counters not in the legacy allowlist (Replay, CRC, etc) are
    silently dropped — the watchdog never claimed to track them and
    we want behavioural parity. Link 1 has no surviving counter line,
    so the GPU's link map only contains link 0."""
    text = """\
GPU 0: NVIDIA H100 (UUID: GPU-xxx)
\t Link 0: Replay Errors: 0
\t Link 0: Rx Errors: 3
\t Link 1: CRC Errors: 12
\t header noise we ignore
"""
    out = parse_nvlink_errors(text)
    assert out == {0: {0: {"Rx Errors": 3}}}


def test_nvlink_errors_handles_full_legacy_counter_set():
    """All the error names the legacy watchdog's regex covers should
    parse out — that's the watchdog's source of truth, lifted verbatim."""
    counters = [
        "Malformed packet Errors", "Buffer overrun Errors", "Rx Errors",
        "Rx remote Errors", "Rx General Errors", "Local link integrity Errors",
        "Tx discards", "Link recovery failed events", "Effective Errors",
        "Symbol Errors",
    ]
    lines = ["GPU 0: x"] + [f"\t Link 0: {n}: 1" for n in counters]
    out = parse_nvlink_errors("\n".join(lines))
    assert len(out[0][0]) == len(counters)


def test_nvlink_errors_skips_zero_counter_lines():
    """The legacy regex requires [1-9][0-9]* on the count — zero
    counters are intentionally NOT captured, since "zero errors" is
    boring and we don't want to bloat the response."""
    text = """\
GPU 0: NVIDIA H100
\t Link 0: Rx Errors: 0
\t Link 0: Tx discards: 5
"""
    out = parse_nvlink_errors(text)
    assert out[0][0] == {"Tx discards": 5}


# ----------------------------------------------------------------------
# parse_xid_log
# ----------------------------------------------------------------------


def test_xid_parses_dmesg_ctime_format():
    text = (
        "[Mon May 25 12:34:56 2026] NVRM: Xid (PCI:0000:1a:00): 79, GPU has fallen off the bus.\n"
        "[Mon May 25 12:35:00 2026] kernel: unrelated noise\n"
        "[Mon May 25 12:35:01 2026] NVRM: Xid (PCI:0000:1b:00): 13, Graphics SM Warp Exception\n"
    )
    out = parse_xid_log(text)
    assert len(out) == 2
    assert out[0]["xid"] == 79 and out[0]["pci"] == "0000:1a:00"
    assert out[1]["xid"] == 13 and "Warp" in out[1]["detail"]


def test_xid_parses_journalctl_format():
    text = (
        "May 25 12:34:56 gpu-host kernel: NVRM: Xid (PCI:0000:01:00): 31, Ch 00000010\n"
    )
    out = parse_xid_log(text)
    assert len(out) == 1
    assert out[0]["xid"] == 31


def test_xid_returns_empty_on_clean_log():
    text = "[time] kernel: nothing\n[time] kernel: still nothing\n"
    assert parse_xid_log(text) == []

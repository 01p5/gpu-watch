"""SSH-based GPU health probes.

NodeProbe wraps `nvidia-smi` over SSH. All parsing lives in pure
functions at the bottom of the module so they can be unit-tested
without a runner — that's where the real risk is (Slurm-format
quirks, blank cells, "N/A" instead of a number, etc).

Field shape mirrors the legacy gpu-watchdog.py: every probe returns
``{gpu_index: {...}}`` so the classifier can iterate uniformly. The
GPU count is exposed as a top-level int because the GPU_COUNT drain
category is about the *node*, not a per-GPU concern.

XID scrape: we read the kernel log (dmesg or journalctl -k) and grep
for "NVRM: Xid" lines. This is the same source the legacy watchdog
uses via its check-gpu-health.sh script — direct, no Prometheus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .connection import CommandResult


class _RunnerProto(Protocol):
    cluster: Any
    def run(self, argv: list[str], timeout: int = 60,
            stdin_data: str | None = None) -> CommandResult: ...


class ProbeError(RuntimeError):
    """Raised when a probe fails in a way callers should surface to the
    UI (command non-zero, parse error, missing tool). Wraps the
    ``CommandResult`` so the dashboard can show stderr verbatim."""

    def __init__(self, message: str, result: CommandResult | None = None):
        self.result = result
        super().__init__(message)


# ----------------------------------------------------------------------
# The nvlink bash check — lifted verbatim from the legacy watchdog's
# NVLINK_CHECK_REMOTE. We pipe this over ``bash -s`` so a node with a
# broken /mnt/zfs can still be probed (no shared-mount dependency).
# ----------------------------------------------------------------------

NVLINK_CHECK_REMOTE = r'''
set -u
fail() { echo "FAIL: $1"; exit "$2"; }

nvls=$(nvidia-smi nvlink --status 2>&1)
nvls_rc=$?
if [[ $nvls_rc -ne 0 ]]; then echo "${nvls:0:200}" >&2; fail "nvlink --status exit $nvls_rc" 1; fi
total_links=$(printf "%s\n" "$nvls" | grep -cE '^[[:space:]]*Link [0-9]+:')
inactive=$(printf "%s\n" "$nvls" | grep -ciE 'inactive|down|disabled|off')
[[ $total_links -eq 0 ]] && fail "nvlink --status returned no Link lines" 2
[[ $inactive -gt 0 ]] && fail "$inactive nvlink line(s) report inactive/down/off" 3

nvle=$(nvidia-smi nvlink -e 2>&1)
nvle_rc=$?
if [[ $nvle_rc -ne 0 ]]; then echo "${nvle:0:200}" >&2; fail "nvlink -e exit $nvle_rc" 4; fi
err_re='^[[:space:]]*Link [0-9]+:[[:space:]]*(Malformed packet Errors|Buffer overrun Errors|Rx Errors|Rx remote Errors|Rx General Errors|Local link integrity Errors|Tx discards|Link recovery failed events|Effective Errors|Symbol Errors):[[:space:]]*[1-9][0-9]*[[:space:]]*$'
nonzero=$(printf "%s\n" "$nvle" | grep -cE "$err_re")
if [[ $nonzero -gt 0 ]]; then
    sample=$(printf "%s\n" "$nvle" | grep -E "$err_re" | head -3 | tr '\n' ';' | sed 's/;$//')
    echo "sample: $sample" >&2
    fail "$nonzero nvlink error counter(s) non-zero" 5
fi
echo "ok: $total_links nvlinks up"
'''


# ----------------------------------------------------------------------
# nvidia-smi --query-gpu field set.
# Order matters: it's the column order in the CSV we ask for. Keep the
# list aligned with the per-GPU dict shape parse_query_gpu_csv emits.
# ----------------------------------------------------------------------

QUERY_GPU_FIELDS = [
    "index", "name", "uuid",
    "temperature.gpu",
    "power.draw",
    "memory.used", "memory.total",
    "utilization.gpu", "utilization.memory",
    "ecc.errors.uncorrected.aggregate.total",
    "ecc.errors.corrected.aggregate.total",
]

QUERY_REMAPPED_ROWS_FIELDS = [
    "index",
    "remapped_rows.correctable",
    "remapped_rows.uncorrectable",
    "remapped_rows.pending",
    "remapped_rows.failure",
]


# ----------------------------------------------------------------------
# NodeProbe
# ----------------------------------------------------------------------


@dataclass
class NodeProbe:
    runner: _RunnerProto
    default_timeout: int = 30

    def _run(self, argv: list[str], **kw) -> CommandResult:
        return self.runner.run(argv, timeout=self.default_timeout, **kw)

    def _run_ok(self, argv: list[str], **kw) -> CommandResult:
        r = self._run(argv, **kw)
        if not r.ok:
            raise ProbeError(
                f"{argv[0]} exited {r.returncode}: {r.stderr.strip() or r.stdout.strip()}",
                result=r,
            )
        return r

    # ---- Read-only probes ----

    def query_gpu(self) -> list[dict[str, Any]]:
        """Per-GPU snapshot via ``nvidia-smi --query-gpu``. Returns a
        list of dicts in GPU-index order."""
        argv = [
            "nvidia-smi",
            f"--query-gpu={','.join(QUERY_GPU_FIELDS)}",
            "--format=csv,noheader,nounits",
        ]
        return parse_query_gpu_csv(self._run_ok(argv).stdout)

    def query_remapped_rows(self) -> list[dict[str, Any]]:
        argv = [
            "nvidia-smi",
            f"--query-remapped-rows={','.join(QUERY_REMAPPED_ROWS_FIELDS)}",
            "--format=csv,noheader,nounits",
        ]
        return parse_remapped_rows_csv(self._run_ok(argv).stdout)

    def list_gpus(self) -> int:
        """`nvidia-smi -L` line count = GPU count this node sees."""
        out = self._run_ok(["nvidia-smi", "-L"]).stdout
        return sum(1 for line in out.splitlines() if line.startswith("GPU "))

    def nvlink_status(self) -> dict[int, list[dict]]:
        return parse_nvlink_status(
            self._run_ok(["nvidia-smi", "nvlink", "--status"]).stdout,
        )

    def nvlink_errors(self) -> dict[int, dict[int, dict[str, int]]]:
        return parse_nvlink_errors(
            self._run_ok(["nvidia-smi", "nvlink", "-e"]).stdout,
        )

    def nvlink_check_remote(self) -> tuple[bool, str]:
        """Run the legacy watchdog's inline bash check via ``bash -s``.
        Returns (passed, detail) — same contract as the legacy
        ``check_nvlink_on_host()``."""
        r = self.runner.run(
            ["bash", "-s"], timeout=self.default_timeout,
            stdin_data=NVLINK_CHECK_REMOTE,
        )
        if r.ok:
            last = (r.stdout.strip().splitlines() or [""])[-1]
            return True, last or "ok"
        detail = (r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")
        return False, detail.replace("\n", "; ")[:200]

    def xid_errors_from_dmesg(self, lines: int = 2000) -> list[dict]:
        """Read recent kernel-log lines and parse ``NVRM: Xid`` events.
        Uses ``dmesg``; falls back to ``journalctl -k`` if dmesg refuses
        (some hosts gate dmesg behind capabilities)."""
        r = self.runner.run(["dmesg", "--ctime"], timeout=self.default_timeout)
        if not r.ok:
            r = self.runner.run(
                ["journalctl", "-k", "--no-pager", "-n", str(lines)],
                timeout=self.default_timeout,
            )
        if not r.ok:
            raise ProbeError("dmesg and journalctl both failed", result=r)
        return parse_xid_log(r.stdout)


# ----------------------------------------------------------------------
# Pure parsers — all the format quirks live here.
# ----------------------------------------------------------------------


def _to_int_or_none(s: str) -> int | None:
    s = s.strip()
    if not s or s in {"[N/A]", "N/A"}:
        return None
    try:
        return int(s)
    except ValueError:
        try:
            return int(float(s))
        except ValueError:
            return None


def _to_float_or_none(s: str) -> float | None:
    s = s.strip()
    if not s or s in {"[N/A]", "N/A"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_query_gpu_csv(text: str) -> list[dict[str, Any]]:
    """Parse the CSV nvidia-smi emits for ``--query-gpu=<fields> --format=csv,noheader,nounits``.

    Columns are positional, matching ``QUERY_GPU_FIELDS``. We tolerate
    "[N/A]" for missing values (driver / hardware combinations where
    a counter is unsupported) by mapping them to None rather than 0 —
    so the classifier can distinguish "0 errors" from "unmeasurable".
    """
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < len(QUERY_GPU_FIELDS):
            continue                    # truncated row — skip
        rec: dict[str, Any] = {}
        rec["index"]                = _to_int_or_none(cells[0])
        rec["name"]                 = cells[1]
        rec["uuid"]                 = cells[2]
        rec["temperature_c"]        = _to_int_or_none(cells[3])
        rec["power_w"]              = _to_float_or_none(cells[4])
        rec["memory_used_mib"]      = _to_int_or_none(cells[5])
        rec["memory_total_mib"]     = _to_int_or_none(cells[6])
        rec["util_gpu_pct"]         = _to_int_or_none(cells[7])
        rec["util_mem_pct"]         = _to_int_or_none(cells[8])
        rec["ecc_uncorrected_total"] = _to_int_or_none(cells[9])
        rec["ecc_corrected_total"]   = _to_int_or_none(cells[10])
        out.append(rec)
    return out


def parse_remapped_rows_csv(text: str) -> list[dict[str, Any]]:
    """``--query-remapped-rows`` CSV → per-GPU dict.

    ``remapped_rows.failure`` is "Yes"/"No" (not 0/1), so we coerce to
    bool. The other three fields are integer counts.
    """
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < len(QUERY_REMAPPED_ROWS_FIELDS):
            continue
        out.append({
            "index":         _to_int_or_none(cells[0]),
            "correctable":   _to_int_or_none(cells[1]) or 0,
            "uncorrectable": _to_int_or_none(cells[2]) or 0,
            "pending":       _to_int_or_none(cells[3]) or 0,
            "failure":       cells[4].lower() in {"yes", "true", "1"},
        })
    return out


_GPU_HEADER_RE = re.compile(r"^GPU\s+(\d+):", re.IGNORECASE)
_LINK_LINE_RE = re.compile(r"^\s*Link\s+(\d+):\s*(.*)$")


def parse_nvlink_status(text: str) -> dict[int, list[dict]]:
    """Parse ``nvidia-smi nvlink --status``.

    Returns ``{gpu_index: [{link: int, raw: str, active: bool}, ...]}``.
    Same active-detection heuristic as the legacy watchdog: a link is
    inactive if its line matches /inactive|down|disabled|off/i.
    """
    out: dict[int, list[dict]] = {}
    current_gpu: int | None = None
    inactive_re = re.compile(r"inactive|down|disabled|off", re.IGNORECASE)
    for line in text.splitlines():
        m = _GPU_HEADER_RE.match(line)
        if m:
            current_gpu = int(m.group(1))
            out.setdefault(current_gpu, [])
            continue
        m = _LINK_LINE_RE.match(line)
        if m and current_gpu is not None:
            link_idx = int(m.group(1))
            detail = m.group(2).strip()
            out[current_gpu].append({
                "link": link_idx,
                "raw": detail,
                "active": not bool(inactive_re.search(detail)),
            })
    return out


_NVLINK_ERR_RE = re.compile(
    # Counter set + count regex lifted verbatim from the legacy
    # watchdog's bash check. The ``[1-9]\d*`` clause is deliberate:
    # zero counters are uninteresting and the parser drops them on
    # the floor so the dashboard only ever sees "this thing went wrong".
    r"^\s*Link\s+(\d+):\s*"
    r"(Malformed packet Errors|Buffer overrun Errors|Rx Errors|"
    r"Rx remote Errors|Rx General Errors|Local link integrity Errors|"
    r"Tx discards|Link recovery failed events|Effective Errors|Symbol Errors)"
    r":\s*([1-9]\d*)\s*$"
)


def parse_nvlink_errors(text: str) -> dict[int, dict[int, dict[str, int]]]:
    """``nvidia-smi nvlink -e`` → {gpu: {link: {error_name: count}}}.

    Only the error-counter line shapes the legacy watchdog cares about
    are captured; everything else (headers, blank lines) is ignored.
    """
    out: dict[int, dict[int, dict[str, int]]] = {}
    current_gpu: int | None = None
    for line in text.splitlines():
        m = _GPU_HEADER_RE.match(line)
        if m:
            current_gpu = int(m.group(1))
            out.setdefault(current_gpu, {})
            continue
        m = _NVLINK_ERR_RE.match(line)
        if m and current_gpu is not None:
            link, name, count = int(m.group(1)), m.group(2), int(m.group(3))
            out[current_gpu].setdefault(link, {})[name] = count
    return out


_XID_RE = re.compile(
    # dmesg --ctime: "[Sun May 25 ...]" prefix then "NVRM: Xid (PCI:0000:1a:00): 79, ..."
    # journalctl -k: "May 25 12:34:56 host kernel: NVRM: Xid (PCI:...): 79, ..."
    r"NVRM:\s*Xid\s*\(PCI:([0-9a-fA-F:.]+)\):\s*(\d+)\s*,?\s*(.*)$"
)


def parse_xid_log(text: str) -> list[dict]:
    """Find ``NVRM: Xid`` lines and return one dict per occurrence.

    Doesn't try to deduplicate — the legacy watchdog leaves that to its
    severity classifier. We just emit the raw events in order.
    """
    out: list[dict] = []
    for line in text.splitlines():
        m = _XID_RE.search(line)
        if not m:
            continue
        pci, xid, detail = m.group(1), int(m.group(2)), m.group(3).strip()
        out.append({
            "pci": pci,
            "xid": xid,
            "detail": detail,
            "raw": line.strip(),
        })
    return out

"""Map raw probe output to drain-candidate findings.

Categories + semantics are kept verbatim from the legacy
``gpu-watchdog.py`` so an ops team migrating from Prometheus + the
shell health-check to gpu-watch sees identical drain advice for
identical underlying state. The categories::

  XID                          recent NVRM Xid event
  ROW_REMAP_FAILURE            row-remap engine itself failed
  UNCORRECTABLE_REMAPPED_ROWS  uncorrectable HBM rows mapped out (any > 0)
  CORRECTABLE_REMAPPED_ROWS    correctable HBM rows mapped out beyond threshold
  GPU_COUNT                    node sees fewer GPUs than configured
  NVLINK                       added by gpu-watch — the legacy watchdog
                                runs nvlink as a separate pipeline; here
                                we treat it as a regular finding.

Severity: HARD findings are drain candidates. SOFT findings are surfaced
in the UI but don't recommend an action. The dashboard's "Drain
advisor" page filters on ``severity == HARD``.

Inputs are intentionally a generic ``Signals`` dict rather than coupled
to NodeProbe/DcgmExporterClient, so:
  - the SSH path can pre-fill nvidia-smi-derived fields,
  - the dcgm path can pre-fill dcgm-derived fields,
  - both can be merged into the same Signals before classification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


DRAIN_CATEGORIES = (
    "XID", "ROW_REMAP_FAILURE",
    "UNCORRECTABLE_REMAPPED_ROWS", "CORRECTABLE_REMAPPED_ROWS",
    "GPU_COUNT", "NVLINK",
)


@dataclass(slots=True)
class Finding:
    node: str
    category: str
    severity: Severity
    gpu: int | None
    message: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "node": self.node,
            "category": self.category,
            "severity": self.severity.value,
            "gpu": self.gpu,
            "message": self.message,
            "raw": self.raw,
        }


# Threshold for CORRECTABLE_REMAPPED_ROWS HARD-vs-SOFT. The legacy
# watchdog's check-gpu-health.sh treats >= 1 as HARD because correctable
# remaps are still a sign of degrading HBM; we keep that posture but
# expose the threshold here so an operator can tune it without
# code-spelunking.
CORRECTABLE_REMAP_HARD_THRESHOLD = 1


def classify(node: str, signals: dict[str, Any],
             expected_gpu_count: int | None = None) -> list[Finding]:
    """Apply every classifier rule to ``signals`` and return findings.

    ``signals`` keys we look at:

      query_gpu          : list[dict]   (per-GPU snapshot)
      remapped_rows      : list[dict]   (per-GPU remapped-rows counters)
      gpu_count          : int          (from `nvidia-smi -L`)
      xid_events         : list[dict]   (parsed dmesg/journalctl)
      nvlink_status      : dict         (parsed `nvlink --status`)
      nvlink_errors      : dict         (parsed `nvlink -e`)
      dcgm               : dict[str, list[DcgmSample]]  (optional)

    ``expected_gpu_count`` is per-node (e.g. an 8x H100 node expects 8).
    If None, the GPU_COUNT rule is skipped.
    """
    findings: list[Finding] = []

    _classify_xid(node, signals, findings)
    _classify_remapped_rows(node, signals, findings)
    _classify_gpu_count(node, signals, expected_gpu_count, findings)
    _classify_nvlink(node, signals, findings)
    _classify_dcgm(node, signals, findings)

    return findings


# ----------------------------------------------------------------------
# Per-category rules
# ----------------------------------------------------------------------


def _classify_xid(node: str, signals: dict, findings: list[Finding]) -> None:
    """Any NVRM Xid event in the scraped log → HARD/XID per event.

    The legacy watchdog also dedupes by (host, gpu) before draining;
    we leave the events un-deduped here and let the advisor collapse
    them in the UI (so the operator sees how many occurred)."""
    for event in signals.get("xid_events") or []:
        findings.append(Finding(
            node=node, category="XID", severity=Severity.HARD,
            gpu=None,
            message=f"Xid {event.get('xid')} on {event.get('pci')}: {event.get('detail', '')[:120]}",
            raw=dict(event),
        ))


def _classify_remapped_rows(node: str, signals: dict, findings: list[Finding]) -> None:
    """Three rules from --query-remapped-rows::

      failure=Yes → HARD/ROW_REMAP_FAILURE
      uncorrectable > 0 → HARD/UNCORRECTABLE_REMAPPED_ROWS
      correctable >= threshold → HARD/CORRECTABLE_REMAPPED_ROWS
                       (below threshold → SOFT)
    """
    for row in signals.get("remapped_rows") or []:
        gpu = row.get("index")
        if row.get("failure"):
            findings.append(Finding(
                node=node, category="ROW_REMAP_FAILURE",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: row remap engine reported failure",
                raw=dict(row),
            ))
        if (row.get("uncorrectable") or 0) > 0:
            findings.append(Finding(
                node=node, category="UNCORRECTABLE_REMAPPED_ROWS",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: {row['uncorrectable']} uncorrectable row(s) remapped",
                raw=dict(row),
            ))
        correctable = row.get("correctable") or 0
        if correctable >= CORRECTABLE_REMAP_HARD_THRESHOLD:
            findings.append(Finding(
                node=node, category="CORRECTABLE_REMAPPED_ROWS",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: {correctable} correctable row(s) remapped",
                raw=dict(row),
            ))
        elif correctable > 0:  # below threshold but non-zero — note it
            findings.append(Finding(
                node=node, category="CORRECTABLE_REMAPPED_ROWS",
                severity=Severity.SOFT, gpu=gpu,
                message=f"GPU{gpu}: {correctable} correctable row(s) remapped (below threshold)",
                raw=dict(row),
            ))


def _classify_gpu_count(node: str, signals: dict, expected: int | None,
                        findings: list[Finding]) -> None:
    if expected is None:
        return
    observed = signals.get("gpu_count")
    if observed is None:
        return
    if observed < expected:
        findings.append(Finding(
            node=node, category="GPU_COUNT", severity=Severity.HARD, gpu=None,
            message=f"node sees {observed} GPUs, expected {expected}",
            raw={"observed": observed, "expected": expected},
        ))


def _classify_nvlink(node: str, signals: dict, findings: list[Finding]) -> None:
    """Two sub-rules driven by the parsed nvlink output:

      - any link reports active=False → HARD/NVLINK
      - any error counter > 0 across all (gpu, link, counter) tuples → HARD/NVLINK

    The legacy watchdog also rejects nodes whose ``nvidia-smi nvlink
    --status`` returns no Link lines at all — we let the caller catch
    that as an empty dict and surface it separately.
    """
    status = signals.get("nvlink_status") or {}
    for gpu, links in status.items():
        inactive = [link for link in links if not link.get("active", True)]
        if inactive:
            findings.append(Finding(
                node=node, category="NVLINK", severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: {len(inactive)} nvlink(s) inactive/down/disabled/off",
                raw={"inactive_links": [link["link"] for link in inactive]},
            ))

    errors = signals.get("nvlink_errors") or {}
    for gpu, link_map in errors.items():
        nonzero_counters: list[str] = []
        for link, counters in link_map.items():
            for counter_name, count in counters.items():
                if count > 0:
                    nonzero_counters.append(f"link{link}.{counter_name}={count}")
        if nonzero_counters:
            findings.append(Finding(
                node=node, category="NVLINK", severity=Severity.HARD, gpu=gpu,
                message=(f"GPU{gpu}: {len(nonzero_counters)} non-zero nvlink counter(s): "
                         + ", ".join(nonzero_counters[:3])
                         + (" …" if len(nonzero_counters) > 3 else "")),
                raw={"counters": nonzero_counters},
            ))


def _classify_dcgm(node: str, signals: dict, findings: list[Finding]) -> None:
    """When the dcgm-exporter signal is present, it can independently
    confirm the SSH path's findings — but it also catches cases where
    SSH was skipped (e.g. dcgm-only deployments).

    We surface:
      DCGM_FI_DEV_ROW_REMAP_FAILURE > 0 → HARD/ROW_REMAP_FAILURE
      DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS > 0 → HARD/UNCORRECTABLE_REMAPPED_ROWS
      DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS >= threshold → HARD/CORRECTABLE_REMAPPED_ROWS
      DCGM_FI_DEV_XID_ERRORS > 0 → HARD/XID

    We do NOT also dedupe against the SSH-side findings; we want the
    advisor to surface "both sources see this" loudly. The dashboard
    can collapse duplicates per (node, gpu, category) at render time
    if it wants — but most ops teams prefer the redundancy.
    """
    dcgm = signals.get("dcgm") or {}

    def _per_gpu(name: str) -> list[tuple[int | None, float]]:
        return [(s.gpu, s.value) for s in dcgm.get(name, [])]

    for gpu, val in _per_gpu("DCGM_FI_DEV_ROW_REMAP_FAILURE"):
        if val > 0:
            findings.append(Finding(
                node=node, category="ROW_REMAP_FAILURE",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: dcgm reports row remap failure",
                raw={"source": "dcgm", "value": val},
            ))
    for gpu, val in _per_gpu("DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS"):
        if val > 0:
            findings.append(Finding(
                node=node, category="UNCORRECTABLE_REMAPPED_ROWS",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: dcgm reports {int(val)} uncorrectable remapped rows",
                raw={"source": "dcgm", "value": val},
            ))
    for gpu, val in _per_gpu("DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS"):
        if val >= CORRECTABLE_REMAP_HARD_THRESHOLD:
            findings.append(Finding(
                node=node, category="CORRECTABLE_REMAPPED_ROWS",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: dcgm reports {int(val)} correctable remapped rows",
                raw={"source": "dcgm", "value": val},
            ))
    for gpu, val in _per_gpu("DCGM_FI_DEV_XID_ERRORS"):
        if val > 0:
            findings.append(Finding(
                node=node, category="XID",
                severity=Severity.HARD, gpu=gpu,
                message=f"GPU{gpu}: dcgm reports Xid {int(val)}",
                raw={"source": "dcgm", "xid": int(val)},
            ))

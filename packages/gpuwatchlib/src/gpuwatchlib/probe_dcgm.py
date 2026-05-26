"""dcgm-exporter `/metrics` HTTP client + Prometheus-text parser.

The exporter speaks the standard Prometheus exposition format on
``http://<host>:9400/metrics`` by default. We fetch it with stdlib
``urllib`` (no requests dep, no prometheus_client dep) and parse with
a minimal hand-rolled parser — the format is dead simple for what we
need (counters + gauges, label dicts).

This is the "no Prometheus required" path: query the exporter directly,
get the same metrics that would otherwise hit a Prometheus scrape job.
The dashboard then surfaces them per-GPU just like the SSH probe.

Selected DCGM metrics worth surfacing (not exhaustive):

  DCGM_FI_DEV_GPU_TEMP                — GPU temperature (C)
  DCGM_FI_DEV_POWER_USAGE             — power draw (W)
  DCGM_FI_DEV_GPU_UTIL                — utilization %
  DCGM_FI_DEV_MEM_COPY_UTIL           — memory util %
  DCGM_FI_DEV_FB_USED / FB_FREE       — framebuffer used / free MiB
  DCGM_FI_DEV_ECC_DBE_VOL_TOTAL       — volatile double-bit ECC
  DCGM_FI_DEV_ECC_SBE_VOL_TOTAL       — volatile single-bit ECC
  DCGM_FI_DEV_ROW_REMAP_FAILURE       — remap engine failure (bool gauge)
  DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS
  DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS
  DCGM_FI_DEV_NVLINK_REPLAY_ERROR_COUNT_TOTAL
  DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_COUNT_TOTAL
  DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL
  DCGM_FI_DEV_XID_ERRORS              — most recent XID code (gauge)
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable


# Metrics we surface in the fleet summary + classifier. Extra metrics
# parsed from /metrics are still kept (they go into the raw dict) —
# this is just the curated subset for the UI's at-a-glance view.
KEY_METRICS = (
    "DCGM_FI_DEV_GPU_TEMP",
    "DCGM_FI_DEV_POWER_USAGE",
    "DCGM_FI_DEV_GPU_UTIL",
    "DCGM_FI_DEV_MEM_COPY_UTIL",
    "DCGM_FI_DEV_FB_USED",
    "DCGM_FI_DEV_FB_FREE",
    "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL",
    "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL",
    "DCGM_FI_DEV_ROW_REMAP_FAILURE",
    "DCGM_FI_DEV_UNCORRECTABLE_REMAPPED_ROWS",
    "DCGM_FI_DEV_CORRECTABLE_REMAPPED_ROWS",
    "DCGM_FI_DEV_NVLINK_REPLAY_ERROR_COUNT_TOTAL",
    "DCGM_FI_DEV_NVLINK_RECOVERY_ERROR_COUNT_TOTAL",
    "DCGM_FI_DEV_NVLINK_CRC_FLIT_ERROR_COUNT_TOTAL",
    "DCGM_FI_DEV_XID_ERRORS",
)


@dataclass(slots=True)
class DcgmSample:
    name: str
    labels: dict[str, str]
    value: float

    @property
    def gpu(self) -> int | None:
        """DCGM labels each per-GPU series with ``gpu="N"`` (string).
        Return it as an int when present; tests + the classifier rely on
        this."""
        g = self.labels.get("gpu")
        if g is None:
            return None
        try:
            return int(g)
        except ValueError:
            return None


# ----------------------------------------------------------------------
# Prometheus text-format parser — minimal, no exemplars or histograms.
# Lines we care about look like:
#     DCGM_FI_DEV_GPU_TEMP{gpu="0",UUID="GPU-xxx",device="nvidia0",modelName="..."} 35
# Comments + blank lines are skipped. Counters/gauges only.
# ----------------------------------------------------------------------


_LABELS_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
_SAMPLE_RE = re.compile(
    r'^([A-Za-z_:][A-Za-z0-9_:]*)'      # metric name
    r'(?:\{([^}]*)\})?'                  # optional {labels}
    r'\s+'
    r'(-?[0-9.eE+\-]+|NaN|\+Inf|-Inf)'   # value
    r'(?:\s+\d+)?'                       # optional timestamp (ms) — ignored
    r'\s*$'
)


def parse_prometheus_text(text: str) -> dict[str, list[DcgmSample]]:
    """Return ``{metric_name: [DcgmSample, ...]}``.

    Tolerates malformed lines (skipped silently — the exporter
    sometimes emits partial samples during init). Doesn't validate
    metric types from ``# TYPE`` lines — we trust the exporter.
    """
    out: dict[str, list[DcgmSample]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name, label_blob, val_str = m.group(1), m.group(2), m.group(3)
        try:
            value = float(val_str)
        except ValueError:
            # NaN / +Inf / -Inf parse fine via float() in CPython, so
            # this should never fire for the values above. Skip if it does.
            continue
        labels: dict[str, str] = {}
        if label_blob:
            for lm in _LABELS_RE.finditer(label_blob):
                labels[lm.group(1)] = lm.group(2).replace(r'\"', '"').replace(r"\\", "\\")
        out.setdefault(name, []).append(DcgmSample(name=name, labels=labels, value=value))
    return out


# ----------------------------------------------------------------------
# HTTP client
# ----------------------------------------------------------------------


class DcgmExporterError(RuntimeError):
    pass


class DcgmExporterClient:
    """One client per node URL. ``timeout`` covers the whole request
    (connect + read). Reuses urllib so we stay dep-free."""

    def __init__(self, base_url: str, timeout: float = 5.0,
                 headers: dict[str, str] | None = None):
        # Strip trailing slash so concatenation is predictable.
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = headers or {}

    def fetch_text(self, path: str = "/metrics") -> str:
        url = self.base_url + path
        req = urllib.request.Request(url, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise DcgmExporterError(f"HTTP {exc.code} from {url}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise DcgmExporterError(f"URL error fetching {url}: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise DcgmExporterError(f"timeout fetching {url}: {exc}") from exc

    def fetch(self) -> dict[str, list[DcgmSample]]:
        return parse_prometheus_text(self.fetch_text())

    def fetch_selected(self, names: Iterable[str] = KEY_METRICS) -> dict[str, list[DcgmSample]]:
        """Convenience: parse the whole thing, return only the keys we
        actually surface. Strips the noise so the dashboard doesn't
        render 200 metric families per node."""
        all_metrics = self.fetch()
        wanted = set(names)
        return {k: v for k, v in all_metrics.items() if k in wanted}

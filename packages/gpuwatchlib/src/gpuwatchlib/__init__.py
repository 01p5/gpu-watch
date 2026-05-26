"""gpuwatchlib — GPU health probes + classifier."""
from __future__ import annotations

from .audit import AuditRecord, JsonlAuditLogger, NullAuditLogger
from .classifier import (
    DRAIN_CATEGORIES, Finding, Severity, classify,
)
from .connection import (
    Cluster as Node,         # alias for symmetry with slurm-mgr name
    ClusterRegistry as NodeRegistry,
    CommandResult, LocalRunner, SSHRunner, _FakeRunner,
)
from .probe_dcgm import DcgmExporterClient, DcgmSample, parse_prometheus_text
from .probe_ssh import (
    NodeProbe, ProbeError,
    parse_query_gpu_csv, parse_remapped_rows_csv,
    parse_nvlink_status, parse_nvlink_errors, parse_xid_log,
)

__all__ = [
    "AuditRecord",
    "DRAIN_CATEGORIES",
    "DcgmExporterClient",
    "DcgmSample",
    "Finding",
    "JsonlAuditLogger",
    "LocalRunner",
    "Node",
    "NodeProbe",
    "NodeRegistry",
    "NullAuditLogger",
    "ProbeError",
    "SSHRunner",
    "Severity",
    "CommandResult",
    "_FakeRunner",
    "classify",
    "parse_nvlink_errors",
    "parse_nvlink_status",
    "parse_prometheus_text",
    "parse_query_gpu_csv",
    "parse_remapped_rows_csv",
    "parse_xid_log",
]

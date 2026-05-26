"""Per-node orchestration of the SSH + dcgm probes.

A single ``NodeService`` per call encapsulates "run all the probes you
can against this node, return a normalized dict". The routes layer
calls it once per request and translates the dict into a JSON
response.

Both probes are best-effort: if the dcgm-exporter URL isn't set on
the node, that branch is skipped; if SSH fails for one query, the
others still run. The returned dict carries an ``errors`` list with
``{probe, error}`` entries so the UI can show what worked vs what
didn't.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gpuwatchlib import (
    DcgmExporterClient, Node, NodeProbe, ProbeError, classify,
)
from gpuwatchlib.probe_dcgm import DcgmExporterError


@dataclass
class NodeService:
    """Bundles the probes for one node. ``runner_factory`` and
    ``dcgm_factory`` are injected so tests can swap in fakes."""
    node: Node
    runner_factory: Callable[[Node], Any]
    dcgm_factory: Callable[[str], DcgmExporterClient] = lambda url: DcgmExporterClient(url)

    def _probe(self) -> NodeProbe:
        return NodeProbe(self.runner_factory(self.node))

    def _dcgm(self) -> DcgmExporterClient | None:
        return self.dcgm_factory(self.node.dcgm_url) if self.node.dcgm_url else None

    # ---- Per-probe wrappers ----

    def ssh_status(self) -> dict:
        probe = self._probe()
        result: dict[str, Any] = {"errors": []}
        for label, fn in (
            ("query_gpu",      probe.query_gpu),
            ("remapped_rows",  probe.query_remapped_rows),
            ("gpu_count",      probe.list_gpus),
        ):
            try:
                result[label] = fn()
            except ProbeError as exc:
                result[label] = None
                result["errors"].append({"probe": label, "error": str(exc)})
        return result

    def ssh_nvlink(self) -> dict:
        probe = self._probe()
        result: dict[str, Any] = {"errors": []}
        try:
            result["nvlink_status"] = probe.nvlink_status()
        except ProbeError as exc:
            result["nvlink_status"] = None
            result["errors"].append({"probe": "nvlink_status", "error": str(exc)})
        try:
            result["nvlink_errors"] = probe.nvlink_errors()
        except ProbeError as exc:
            result["nvlink_errors"] = None
            result["errors"].append({"probe": "nvlink_errors", "error": str(exc)})
        try:
            ok, detail = probe.nvlink_check_remote()
            result["nvlink_check"] = {"passed": ok, "detail": detail}
        except ProbeError as exc:
            result["nvlink_check"] = None
            result["errors"].append({"probe": "nvlink_check_remote", "error": str(exc)})
        return result

    def ssh_xid(self) -> dict:
        probe = self._probe()
        try:
            return {"events": probe.xid_errors_from_dmesg()}
        except ProbeError as exc:
            return {"events": [], "error": str(exc)}

    def dcgm_metrics(self) -> dict:
        client = self._dcgm()
        if client is None:
            return {"available": False, "reason": "no dcgm_url configured for this node"}
        try:
            samples = client.fetch_selected()
        except DcgmExporterError as exc:
            return {"available": False, "reason": str(exc)}
        return {
            "available": True,
            "metrics": {
                name: [{"labels": s.labels, "value": s.value, "gpu": s.gpu} for s in entries]
                for name, entries in samples.items()
            },
        }

    # ---- Aggregated views ----

    def collect_signals(self, include_xid: bool = True) -> dict:
        """All probes → one signals dict suitable for ``classify()``.

        Skips probes that error so the classifier still runs on the
        successful ones (the legacy watchdog has the same posture:
        partial telemetry is better than no telemetry)."""
        status = self.ssh_status()
        nvlink = self.ssh_nvlink()
        signals: dict[str, Any] = {
            "query_gpu": status.get("query_gpu"),
            "remapped_rows": status.get("remapped_rows"),
            "gpu_count": status.get("gpu_count"),
            "nvlink_status": nvlink.get("nvlink_status"),
            "nvlink_errors": nvlink.get("nvlink_errors"),
        }
        if include_xid:
            xid = self.ssh_xid()
            signals["xid_events"] = xid.get("events", [])
        client = self._dcgm()
        if client is not None:
            try:
                signals["dcgm"] = client.fetch_selected()
            except DcgmExporterError:
                pass
        return signals

    def findings(self, expected_gpu_count: int | None = None) -> list[dict]:
        signals = self.collect_signals()
        return [f.to_json() for f in classify(
            self.node.name, signals, expected_gpu_count=expected_gpu_count,
        )]

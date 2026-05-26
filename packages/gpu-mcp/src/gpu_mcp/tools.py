"""Tool descriptors + dispatch table.

Same shape as slurm-mgr's tools.py: each tool is described once in
``TOOLS``, paired with a handler in ``HANDLERS``, and dispatched via
``dispatch_tool``. Tests assert catalog↔handler agreement.

All tools are read-only — gpu-watch's MCP advertises no destructive
set. Olympus's MCPServerConfig.destructive can therefore be omitted.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from gpuwatchlib import (
    DcgmExporterClient, NodeRegistry, ProbeError, SSHRunner,
    classify,
)
from gpuwatchlib.probe_dcgm import DcgmExporterError


_REGISTRY_SINGLETON: NodeRegistry | None = None


def _registry() -> NodeRegistry:
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None:
        _REGISTRY_SINGLETON = NodeRegistry()
    return _REGISTRY_SINGLETON


def set_registry(reg: NodeRegistry) -> None:
    """Test seam: override the registry the handlers use."""
    global _REGISTRY_SINGLETON
    _REGISTRY_SINGLETON = reg


_RUNNER_FACTORY: Callable[[Any], Any] = lambda node: SSHRunner(node)
_DCGM_FACTORY:   Callable[[str], DcgmExporterClient] = lambda url: DcgmExporterClient(url)


def set_runner_factory(fn: Callable[[Any], Any]) -> None:
    """Test seam: substitute the runner factory."""
    global _RUNNER_FACTORY
    _RUNNER_FACTORY = fn


def set_dcgm_factory(fn: Callable[[str], DcgmExporterClient]) -> None:
    global _DCGM_FACTORY
    _DCGM_FACTORY = fn


# ---------------------------------------------------------------------
# MCP content helpers
# ---------------------------------------------------------------------


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _json_dump(value: Any) -> str:
    s = json.dumps(value, indent=2, default=str)
    if len(s) > 30_000:
        s = s[:30_000] + "\n\n…[truncated]"
    return s


def _service_for(name: str):
    # Local import to dodge a circular: gpu_dashboard depends on
    # gpuwatchlib + we re-use the NodeService class here.
    from gpu_dashboard.service import NodeService  # noqa: PLC0415

    node = _registry().get(name)
    return NodeService(node=node, runner_factory=_RUNNER_FACTORY, dcgm_factory=_DCGM_FACTORY)


# ---------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------


def _need(args: dict, key: str) -> str:
    v = args.get(key)
    if v in (None, ""):
        raise ValueError(f"{key} is required")
    return str(v)


def h_nodes_list(_args: dict) -> dict:
    nodes = [
        {"name": n.name, "host": n.host, "user": n.user, "port": n.port,
         "dcgm_url": n.dcgm_url, "jump_host": n.jump_host}
        for n in _registry().list()
    ]
    return _ok(_json_dump({"nodes": nodes}))


def h_node_status(args: dict) -> dict:
    name = _need(args, "name")
    return _ok(_json_dump(_service_for(name).ssh_status()))


def h_node_remapped_rows(args: dict) -> dict:
    """Just the remapped-rows subset — quicker than full status when the
    LLM only cares about HBM health."""
    name = _need(args, "name")
    full = _service_for(name).ssh_status()
    return _ok(_json_dump({"remapped_rows": full.get("remapped_rows")}))


def h_node_ecc(args: dict) -> dict:
    """ECC counters from the SSH probe. Pulls the ecc_* fields out of
    query_gpu so we don't bury them in the bigger status blob."""
    name = _need(args, "name")
    qg = _service_for(name).ssh_status().get("query_gpu") or []
    ecc = [
        {"index": g.get("index"),
         "ecc_uncorrected_total": g.get("ecc_uncorrected_total"),
         "ecc_corrected_total":   g.get("ecc_corrected_total")}
        for g in qg
    ]
    return _ok(_json_dump({"ecc": ecc}))


def h_node_nvlink(args: dict) -> dict:
    name = _need(args, "name")
    return _ok(_json_dump(_service_for(name).ssh_nvlink()))


def h_node_xid(args: dict) -> dict:
    name = _need(args, "name")
    return _ok(_json_dump(_service_for(name).ssh_xid()))


def h_node_dcgm_metrics(args: dict) -> dict:
    name = _need(args, "name")
    return _ok(_json_dump(_service_for(name).dcgm_metrics()))


def h_gpu_count_check(args: dict) -> dict:
    name = _need(args, "name")
    expected = args.get("expected")
    if expected is None:
        raise ValueError("expected is required")
    expected_i = int(expected)
    observed = _service_for(name).ssh_status().get("gpu_count")
    ok = observed is not None and observed >= expected_i
    return _ok(_json_dump({"node": name, "expected": expected_i,
                           "observed": observed, "ok": ok}))


def h_fleet_summary(_args: dict) -> dict:
    """Per-node summary row — same fields as the dashboard's
    /fleet/summary, computed inline so the LLM can ask for the whole
    fleet in one tool call."""
    rows: list[dict] = []
    for node in _registry().list():
        svc = _service_for(node.name)
        try:
            status = svc.ssh_status()
            qg = status.get("query_gpu") or []
            rr = status.get("remapped_rows") or []
            rows.append({
                "node": node.name, "host": node.host,
                "gpu_count": status.get("gpu_count"),
                "max_temp_c": max((g.get("temperature_c") or 0 for g in qg), default=None),
                "sum_ecc_uncorrected": sum(g.get("ecc_uncorrected_total") or 0 for g in qg),
                "sum_ecc_corrected":   sum(g.get("ecc_corrected_total") or 0 for g in qg),
                "any_remap_failure": any(r.get("failure") for r in rr),
                "sum_uncorrectable_remapped": sum(r.get("uncorrectable") or 0 for r in rr),
                "sum_correctable_remapped":   sum(r.get("correctable") or 0 for r in rr),
            })
        except Exception as exc:                                  # noqa: BLE001
            rows.append({"node": node.name, "error": f"{type(exc).__name__}: {exc}"})
    return _ok(_json_dump({"rows": rows}))


def h_drain_advisor(args: dict) -> dict:
    """Run the classifier across the fleet, return only nodes with HARD
    findings. ``expected_gpu_count`` is optional — when omitted, the
    GPU_COUNT category is skipped."""
    expected = args.get("expected_gpu_count")
    expected_i = int(expected) if expected else None
    out: list[dict] = []
    for node in _registry().list():
        svc = _service_for(node.name)
        try:
            signals = svc.collect_signals()
            findings = [f.to_json() for f in classify(node.name, signals, expected_gpu_count=expected_i)]
        except Exception as exc:                                  # noqa: BLE001
            out.append({"node": node.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        hard = [f for f in findings if f["severity"] == "HARD"]
        if hard:
            out.append({"node": node.name, "findings": hard})
    return _ok(_json_dump({"nodes": out}))


# ---------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


TOOLS: list[dict[str, Any]] = [
    {"name": "nodes_list", "description": "List registered GPU nodes.",
     "inputSchema": _schema({})},

    {"name": "node_status",
     "description": "Full nvidia-smi snapshot for one node (per-GPU temp/power/mem/util/ECC plus remapped-rows table and GPU count).",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "node_remapped_rows",
     "description": "Just the row-remap counters for each GPU on the node.",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "node_ecc",
     "description": "Per-GPU ECC counters (uncorrected/corrected aggregate totals).",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "node_nvlink",
     "description": "NVLink status + error counters + the watchdog's inline NVLink check.",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "node_xid",
     "description": "Recent NVRM Xid events parsed from dmesg / journalctl -k.",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "node_dcgm_metrics",
     "description": "Fetch dcgm-exporter /metrics for the node and return a curated subset of DCGM_FI_DEV_* gauges. Empty when no dcgm_url is configured.",
     "inputSchema": _schema({"name": {"type": "string"}}, ["name"])},

    {"name": "gpu_count_check",
     "description": "Return ok=True iff the node sees at least ``expected`` GPUs.",
     "inputSchema": _schema(
         {"name": {"type": "string"}, "expected": {"type": "integer"}},
         ["name", "expected"])},

    {"name": "fleet_summary",
     "description": "Per-node summary: GPU count, hottest GPU, ECC sums, remap counts. One row per registered node.",
     "inputSchema": _schema({})},

    {"name": "drain_advisor",
     "description": "Run the watchdog's classifier across every node. Returns only nodes with >=1 HARD finding; each finding carries a category (XID, ROW_REMAP_FAILURE, UNCORRECTABLE/CORRECTABLE_REMAPPED_ROWS, GPU_COUNT, NVLINK) and a human-readable message.",
     "inputSchema": _schema(
         {"expected_gpu_count": {"type": "integer", "description": "Optional. Enables the GPU_COUNT category."}})},
]

HANDLERS: dict[str, Callable[[dict], dict]] = {
    "nodes_list":         h_nodes_list,
    "node_status":        h_node_status,
    "node_remapped_rows": h_node_remapped_rows,
    "node_ecc":           h_node_ecc,
    "node_nvlink":        h_node_nvlink,
    "node_xid":           h_node_xid,
    "node_dcgm_metrics":  h_node_dcgm_metrics,
    "gpu_count_check":    h_gpu_count_check,
    "fleet_summary":      h_fleet_summary,
    "drain_advisor":      h_drain_advisor,
}


def dispatch_tool(name: str, arguments: dict) -> dict:
    """Run a tool by name. Wraps known exceptions (KeyError when the
    node isn't registered, ProbeError + DcgmExporterError from the
    probes, ValueError from missing args) into MCP error envelopes so
    the JSON-RPC transport never sees an uncaught exception."""
    handler = HANDLERS.get(name)
    if handler is None:
        return _err(f"unknown tool {name!r}")
    try:
        return handler(arguments or {})
    except KeyError as exc:
        return _err(str(exc))
    except ValueError as exc:
        return _err(str(exc))
    except (ProbeError, DcgmExporterError) as exc:
        return _err(f"{type(exc).__name__}: {exc}")
    except Exception as exc:                                  # noqa: BLE001
        return _err(f"{type(exc).__name__}: {exc}")


def tools_descriptor() -> list[dict]:
    """The list returned by MCP ``tools/list``. No `destructive`
    annotations — every tool is read-only."""
    return [{
        "name": t["name"],
        "description": t["description"],
        "inputSchema": t["inputSchema"],
    } for t in TOOLS]

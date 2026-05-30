"""HTTP route handlers. Same shape as slurm-dashboard's routes.py:
``route(method, path, query, body, deps) → (status, headers, body)``,
testable without a socket."""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable

from gpuwatchlib import (
    DcgmExporterClient, JsonlAuditLogger, Node, NodeRegistry, NullAuditLogger,
)
from gpu_mcp.server import dispatch as _mcp_dispatch
from gpu_mcp.tools import set_registry as _mcp_set_registry

from .service import NodeService


@dataclass
class Deps:
    registry: NodeRegistry
    runner_factory: Callable[[Node], Any]
    dcgm_factory: Callable[[str], DcgmExporterClient]
    audit: JsonlAuditLogger | NullAuditLogger


# ---------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------


def _json(status: int, payload: Any) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload, default=str).encode("utf-8")
    return status, {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
    }, body


def _err(status: int, message: str, **extra) -> tuple[int, dict, bytes]:
    return _json(status, {"error": message, **extra})


def _service_for(deps: Deps, name: str) -> NodeService:
    node = deps.registry.get(name)
    return NodeService(
        node=node, runner_factory=deps.runner_factory, dcgm_factory=deps.dcgm_factory,
    )


# ---------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------


_NAME = r"(?P<name>[A-Za-z0-9_\-\.]+)"


def route(method: str, path: str, query: dict[str, str],
          body: dict[str, Any] | None, deps: Deps) -> tuple[int, dict[str, str], bytes]:
    body = body or {}

    if method == "GET" and path == "/healthz":
        return _json(200, {"ok": True, "service": "gpu-dashboard"})

    if method == "GET" and path == "/audit":
        return _audit_tail(deps)

    # ---- MCP-over-HTTP (S2.B1) ----
    # POST /mcp — JSON-RPC 2.0 envelope on the body, single response
    # on the body (no SSE). The gpu-mcp tools resolve nodes via a
    # module-level registry; we point it at the dashboard's own
    # registry before each dispatch so additions/deletions made via
    # the dashboard show up in MCP tool output immediately.
    if method == "POST" and path == "/mcp":
        return _mcp_handler(body, deps)

    # ---- Node registry ----
    if method == "GET" and path == "/nodes":
        return _json(200, {"nodes": [_redacted(n) for n in deps.registry.list()]})
    if method == "POST" and path == "/nodes":
        return _add_node(body, deps)
    m = re.fullmatch(rf"/nodes/{_NAME}", path)
    if m and method == "DELETE":
        deps.registry.remove(m.group("name"))
        return _json(200, {"ok": True})

    # ---- Per-node probes ----
    m = re.fullmatch(rf"/nodes/{_NAME}/(?P<rest>.+)", path)
    if not m:
        return _err(404, f"no route for {method} {path}")
    name, rest = m.group("name"), m.group("rest")
    try:
        deps.registry.get(name)
    except KeyError as exc:
        return _err(404, str(exc))
    return _node_subrouter(method, name, rest, query, deps)

    # ---- Fleet aggregates handled in _node_subrouter fallthrough ----


def _node_subrouter(method: str, name: str, rest: str,
                    query: dict, deps: Deps) -> tuple[int, dict, bytes]:
    svc = _service_for(deps, name)
    audit = deps.audit
    actor = "dashboard"

    def _audited(probe_name: str, fn):
        with audit.around(actor=actor, node=name, probe=probe_name):
            return _json(200, fn())

    if method == "GET" and rest == "status":
        return _audited("ssh:status", svc.ssh_status)
    if method == "GET" and rest == "nvlink":
        return _audited("ssh:nvlink", svc.ssh_nvlink)
    if method == "GET" and rest == "xid":
        return _audited("ssh:xid", svc.ssh_xid)
    if method == "GET" and rest == "dcgm":
        return _audited("dcgm:metrics", svc.dcgm_metrics)
    if method == "GET" and rest == "findings":
        expected = query.get("expected_gpu_count")
        expected_i = int(expected) if expected and expected.isdigit() else None
        return _audited("classify", lambda: {
            "findings": svc.findings(expected_gpu_count=expected_i),
        })
    return _err(404, f"no node route for {method} /nodes/{name}/{rest}")


# ---------------------------------------------------------------------
# Aggregate / fleet endpoints — registered via a second dispatch hook
# below so the top-level route() above doesn't get cluttered.
# ---------------------------------------------------------------------


def route_with_fleet(method: str, path: str, query: dict[str, str],
                     body: dict[str, Any] | None, deps: Deps) -> tuple[int, dict[str, str], bytes]:
    """Wraps ``route()`` with the fleet endpoints. Kept separate so the
    base ``route()`` stays single-purpose for unit tests that don't
    need fleet behavior. The HTTP server calls this entry point."""
    if method == "GET" and path == "/fleet/summary":
        return _fleet_summary(query, deps)
    if method == "GET" and path == "/fleet/unhealthy":
        return _fleet_unhealthy(query, deps)
    return route(method, path, query, body, deps)


def _mcp_handler(body: dict | None, deps: Deps) -> tuple[int, dict, bytes]:
    """JSON-RPC 2.0 over HTTP for the gpu-mcp tool catalog.

    Body must be a single JSON-RPC envelope. We dispatch via
    gpu_mcp.server.dispatch (the same function the stdio loop drives)
    so stdio + HTTP transports stay in lock-step. The gpu-mcp tools
    look up nodes through a module-level registry — we bind ours
    before each dispatch so the MCP view of the fleet is always the
    dashboard's view.

    Notifications (no id field) return 204 with empty body.
    """
    if not isinstance(body, dict):
        return _err(400, "expected JSON-RPC envelope body")
    _mcp_set_registry(deps.registry)
    response = _mcp_dispatch(body)
    if response is None:
        return 204, {}, b""
    return _json(200, response)


def _fleet_summary(query: dict, deps: Deps) -> tuple[int, dict, bytes]:
    """Per-node status row: GPU count, hottest temp, sum of ECC errors,
    sum of remapped rows. Best-effort across nodes — a failing probe
    yields ``error`` on that row, not a 5xx for the whole fleet."""
    out: list[dict] = []
    for node in deps.registry.list():
        svc = _service_for(deps, node.name)
        row: dict[str, Any] = {"node": node.name, "host": node.host}
        try:
            status = svc.ssh_status()
            qg = status.get("query_gpu") or []
            row["gpu_count"] = status.get("gpu_count")
            row["max_temp_c"] = max((g.get("temperature_c") or 0 for g in qg), default=None)
            row["sum_power_w"] = round(sum(g.get("power_w") or 0 for g in qg), 1)
            row["sum_ecc_uncorrected"] = sum(g.get("ecc_uncorrected_total") or 0 for g in qg)
            row["sum_ecc_corrected"]   = sum(g.get("ecc_corrected_total") or 0 for g in qg)
            rr = status.get("remapped_rows") or []
            row["any_remap_failure"] = any(r.get("failure") for r in rr)
            row["sum_uncorrectable_remapped"] = sum(r.get("uncorrectable") or 0 for r in rr)
            row["sum_correctable_remapped"]   = sum(r.get("correctable") or 0 for r in rr)
            row["errors"] = status.get("errors") or []
        except Exception as exc:                                  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        out.append(row)
    return _json(200, {"rows": out})


def _fleet_unhealthy(query: dict, deps: Deps) -> tuple[int, dict, bytes]:
    """Run the classifier across every node and return only nodes with
    ≥1 HARD finding. The "Drain advisor" page uses this."""
    expected = query.get("expected_gpu_count")
    expected_i = int(expected) if expected and expected.isdigit() else None
    out: list[dict] = []
    for node in deps.registry.list():
        svc = _service_for(deps, node.name)
        try:
            findings = svc.findings(expected_gpu_count=expected_i)
        except Exception as exc:                                  # noqa: BLE001
            out.append({"node": node.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        hard = [f for f in findings if f["severity"] == "HARD"]
        if hard:
            out.append({"node": node.name, "findings": hard})
    return _json(200, {"nodes": out})


# ---------------------------------------------------------------------
# Helpers — node CRUD + audit + parse_request
# ---------------------------------------------------------------------


def _add_node(body: dict, deps: Deps) -> tuple[int, dict, bytes]:
    try:
        node = Node(
            name=body["name"], host=body["host"], user=body["user"],
            key_path=body["key_path"], port=int(body.get("port", 22)),
            jump_host=body.get("jump_host") or None,
            dcgm_url=body.get("dcgm_url") or None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _err(400, f"invalid node: {exc}")
    deps.registry.add(node)
    return _json(201, _redacted(node))


def _redacted(node: Node) -> dict:
    return {
        "name": node.name, "host": node.host, "user": node.user,
        "port": node.port, "jump_host": node.jump_host,
        "dcgm_url": node.dcgm_url,
        "key_present": bool(node.key_path),
    }


def _audit_tail(deps: Deps) -> tuple[int, dict, bytes]:
    path = getattr(deps.audit, "path", None)
    if not path or not path.exists():
        return _json(200, {"records": []})
    lines = path.read_text(encoding="utf-8").splitlines()[-200:]
    records: list[dict] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return _json(200, {"records": records})


def parse_request(raw_path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlsplit(raw_path)
    return parsed.path, {
        k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()
    }

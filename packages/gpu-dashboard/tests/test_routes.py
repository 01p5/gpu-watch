"""HTTP route handler tests.

Drives ``route_with_fleet`` directly against a ``_FakeRunner`` + a fake
DCGM factory, no socket. The dashboard server tests cover the
BaseHTTPRequestHandler shell separately."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from gpuwatchlib import (
    Node, NodeRegistry, NullAuditLogger, _FakeRunner,
)
from gpu_dashboard.routes import Deps, parse_request, route, route_with_fleet


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@dataclass
class _FakeDcgm:
    """Minimal stand-in for DcgmExporterClient. ``fetch_selected``
    returns whatever ``payload`` was set; ``raises`` short-circuits to
    DcgmExporterError to exercise the failure branch."""
    payload: dict
    raises: Exception | None = None

    def fetch_selected(self, *_a, **_kw):
        if self.raises:
            raise self.raises
        return self.payload

    def fetch(self):
        return self.payload


@pytest.fixture
def runners_by_name():
    return {}


@pytest.fixture
def make_deps(tmp_path, runners_by_name):
    def _make(*, dcgm_payload: dict | None = None, dcgm_raises: Exception | None = None,
              with_node_dcgm_url: bool = False):
        reg = NodeRegistry(tmp_path / "hosts.json")
        reg.add(Node(
            name="g1", host="h", user="u", key_path="/k",
            dcgm_url="http://h:9400" if with_node_dcgm_url else None,
        ))

        def runner_factory(node: Node):
            runners_by_name.setdefault(node.name, _FakeRunner(cluster=node))
            return runners_by_name[node.name]

        def dcgm_factory(url: str):
            return _FakeDcgm(payload=dcgm_payload or {}, raises=dcgm_raises)

        return Deps(
            registry=reg, runner_factory=runner_factory,
            dcgm_factory=dcgm_factory, audit=NullAuditLogger(),
        )
    return _make


def _call(method: str, path: str, deps, query=None, body=None):
    status, _, payload = route_with_fleet(method, path, query or {}, body, deps)
    parsed = json.loads(payload.decode("utf-8")) if payload else None
    return status, parsed


# ----------------------------------------------------------------------
# Healthz + registry + audit
# ----------------------------------------------------------------------


def test_healthz(make_deps):
    status, body = _call("GET", "/healthz", make_deps())
    assert status == 200 and body["ok"] is True


def test_list_nodes_redacts_key_path(make_deps):
    status, body = _call("GET", "/nodes", make_deps())
    assert status == 200
    assert body["nodes"][0]["key_present"] is True
    assert "key_path" not in body["nodes"][0]


def test_add_and_delete_node(make_deps):
    deps = make_deps()
    status, body = _call("POST", "/nodes", deps, body={
        "name": "g2", "host": "h", "user": "u", "key_path": "/k",
        "dcgm_url": "http://x:9400",
    })
    assert status == 201 and body["dcgm_url"] == "http://x:9400"
    status, _ = _call("DELETE", "/nodes/g2", deps)
    assert status == 200


def test_add_node_validation_error(make_deps):
    status, body = _call("POST", "/nodes", make_deps(), body={"name": "x"})
    assert status == 400 and "invalid node" in body["error"]


def test_unknown_node_returns_404(make_deps):
    status, _ = _call("GET", "/nodes/missing/status", make_deps())
    assert status == 404


def test_unknown_top_level_route_is_404(make_deps):
    status, body = _call("GET", "/nope", make_deps())
    assert status == 404 and "no route" in body["error"]


def test_audit_empty_when_logger_is_null(make_deps):
    status, body = _call("GET", "/audit", make_deps())
    assert status == 200 and body == {"records": []}


# ----------------------------------------------------------------------
# Per-node probes (against _FakeRunner)
# ----------------------------------------------------------------------


def _seed(runners_by_name, name: str, responses):
    """The runner is lazy-created by the factory; we can't seed it
    before the route call. So this helper builds the runner up-front
    and stuffs it into the dict the factory reads from."""
    n = Node(name=name, host="h", user="u", key_path="/k")
    runners_by_name[name] = _FakeRunner(cluster=n, responses=list(responses))


def test_status_calls_nvidia_smi(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
        (0, "0, 0, 0, 0, No\n", ""),
        (0, "GPU 0: H100 (UUID: x)\n", ""),
    ])
    status, body = _call("GET", "/nodes/g1/status", make_deps())
    assert status == 200
    assert body["gpu_count"] == 1
    assert body["query_gpu"][0]["temperature_c"] == 35
    assert body["remapped_rows"][0]["failure"] is False


def test_status_surfaces_partial_probe_errors(make_deps, runners_by_name):
    """The first nvidia-smi call fails; the remap + list_gpus calls
    succeed. We expect the status to include the others + an `errors`
    entry for the failed probe."""
    _seed(runners_by_name, "g1", [
        (1, "", "nvidia-smi: command not found"),
        (0, "0, 0, 0, 0, No\n", ""),
        (0, "GPU 0: H100 (UUID: x)\n", ""),
    ])
    status, body = _call("GET", "/nodes/g1/status", make_deps())
    assert status == 200
    assert body["query_gpu"] is None
    assert body["remapped_rows"] is not None
    assert any("query_gpu" in e["probe"] for e in body["errors"])


def test_nvlink_route(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
        (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
        (0, "ok\n", ""),
    ])
    status, body = _call("GET", "/nodes/g1/nvlink", make_deps())
    assert status == 200
    assert "0" in body["nvlink_status"] or 0 in body["nvlink_status"]
    assert body["nvlink_check"]["passed"] is True


def test_xid_route(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "[time] NVRM: Xid (PCI:0000:01:00): 79, off bus\n", ""),
    ])
    status, body = _call("GET", "/nodes/g1/xid", make_deps())
    assert status == 200 and body["events"][0]["xid"] == 79


def test_dcgm_route_unavailable_when_node_has_no_url(make_deps):
    status, body = _call("GET", "/nodes/g1/dcgm", make_deps())
    assert status == 200 and body["available"] is False
    assert "no dcgm_url" in body["reason"]


def test_dcgm_route_returns_metrics(make_deps):
    from gpuwatchlib import DcgmSample
    deps = make_deps(
        dcgm_payload={"DCGM_FI_DEV_GPU_TEMP": [
            DcgmSample(name="DCGM_FI_DEV_GPU_TEMP", labels={"gpu": "0"}, value=35.0),
        ]},
        with_node_dcgm_url=True,
    )
    status, body = _call("GET", "/nodes/g1/dcgm", deps)
    assert status == 200 and body["available"] is True
    assert body["metrics"]["DCGM_FI_DEV_GPU_TEMP"][0]["value"] == 35.0
    assert body["metrics"]["DCGM_FI_DEV_GPU_TEMP"][0]["gpu"] == 0


def test_dcgm_route_handles_fetch_failure(make_deps):
    from gpuwatchlib.probe_dcgm import DcgmExporterError
    deps = make_deps(dcgm_raises=DcgmExporterError("timeout"), with_node_dcgm_url=True)
    status, body = _call("GET", "/nodes/g1/dcgm", deps)
    assert status == 200 and body["available"] is False
    assert "timeout" in body["reason"]


def test_findings_returns_hard_finding_for_remap_failure(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
        (0, "0, 0, 2, 0, Yes\n", ""),                           # uncorrectable=2, failure=Yes
        (0, "GPU 0: H100 (UUID: x)\n", ""),
        # nvlink probes
        (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
        (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
        (0, "ok\n", ""),
        # xid
        (0, "", ""),
    ])
    status, body = _call("GET", "/nodes/g1/findings", make_deps())
    assert status == 200
    cats = {f["category"] for f in body["findings"]}
    assert "ROW_REMAP_FAILURE" in cats
    assert "UNCORRECTABLE_REMAPPED_ROWS" in cats


def test_unknown_node_subroute_404(make_deps):
    status, body = _call("GET", "/nodes/g1/garbage", make_deps())
    assert status == 404 and "no node route" in body["error"]


# ----------------------------------------------------------------------
# Fleet endpoints
# ----------------------------------------------------------------------


def test_fleet_summary_aggregates_one_row_per_node(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "0, H100, uuid, 41, 90.5, 0, 81920, 0, 0, 0, 0\n"
            "1, H100, uuid, 39, 88.0, 0, 81920, 0, 0, 0, 0\n", ""),
        (0, "0, 0, 0, 0, No\n1, 0, 0, 0, No\n", ""),
        (0, "GPU 0: x\nGPU 1: x\n", ""),
    ])
    status, body = _call("GET", "/fleet/summary", make_deps())
    assert status == 200
    [row] = body["rows"]
    assert row["gpu_count"] == 2
    assert row["max_temp_c"] == 41
    assert round(row["sum_power_w"], 1) == 178.5


def test_fleet_unhealthy_includes_only_hard_findings(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
        (0, "0, 0, 0, 0, No\n", ""),                                # all clean
        (0, "GPU 0: x\n", ""),
        (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
        (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
        (0, "ok\n", ""),
        (0, "", ""),                                                # xid empty
    ])
    status, body = _call("GET", "/fleet/unhealthy", make_deps())
    assert status == 200 and body["nodes"] == []


def test_fleet_unhealthy_with_expected_gpu_count(make_deps, runners_by_name):
    _seed(runners_by_name, "g1", [
        (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
        (0, "0, 0, 0, 0, No\n", ""),
        (0, "GPU 0: x\n", ""),
        (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
        (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
        (0, "ok\n", ""),
        (0, "", ""),
    ])
    status, body = _call("GET", "/fleet/unhealthy", make_deps(),
                         query={"expected_gpu_count": "8"})
    assert status == 200
    cats = [f["category"] for f in body["nodes"][0]["findings"]]
    assert "GPU_COUNT" in cats


# ----------------------------------------------------------------------
# parse_request helper + the base route() (no fleet)
# ----------------------------------------------------------------------


def test_parse_request_helper():
    path, q = parse_request("/nodes/g1/dcgm?refresh=1")
    assert path == "/nodes/g1/dcgm"
    assert q == {"refresh": "1"}


def test_base_route_does_not_handle_fleet():
    """``route()`` alone (without route_with_fleet) returns 404 for
    /fleet/*, so unit tests that don't care about fleet behavior can
    use the lower-level entry point."""
    tmp = NodeRegistry()
    deps = Deps(registry=tmp, runner_factory=lambda n: None,
                dcgm_factory=lambda u: None, audit=NullAuditLogger())
    status, _, _ = route("GET", "/fleet/summary", {}, None, deps)
    assert status == 404

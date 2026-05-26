"""End-to-end MCP server tests + per-tool dispatch tests."""
from __future__ import annotations

import io
import json

import pytest

from gpuwatchlib import Node, NodeRegistry, _FakeRunner
from gpu_mcp.server import PROTOCOL_VERSION, dispatch, serve
from gpu_mcp.tools import (
    HANDLERS, TOOLS, dispatch_tool, set_dcgm_factory, set_registry,
    set_runner_factory, tools_descriptor,
)


# ----------------------------------------------------------------------
# Wire-shape: initialize / tools/list / notification / parse error
# ----------------------------------------------------------------------


def _req(method, msg_id=1, **params):
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}


def test_initialize_returns_supported_protocol():
    resp = dispatch(_req("initialize"))
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "gpu-mcp"


def test_notification_returns_none():
    assert dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_contains_known_tools():
    resp = dispatch(_req("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert "nodes_list" in names and "drain_advisor" in names


def test_unknown_method_returns_jsonrpc_error():
    resp = dispatch(_req("garbage"))
    assert resp["error"]["code"] == -32601


def test_serve_loop_handles_full_handshake():
    stdin = io.StringIO("\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ]) + "\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert any(t["name"] == "drain_advisor" for t in lines[1]["result"]["tools"])


def test_serve_loop_emits_parse_error_for_garbage():
    stdin = io.StringIO("not json\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    assert json.loads(stdout.getvalue().strip())["error"]["code"] == -32700


def test_serve_loop_wraps_unexpected_dispatch_exception(monkeypatch):
    import gpu_mcp.server as srv_mod
    monkeypatch.setattr(srv_mod, "dispatch",
                        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("boom")))
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "x"}) + "\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    resp = json.loads(stdout.getvalue().strip())
    assert resp["error"]["code"] == -32603 and "boom" in resp["error"]["message"]


def test_serve_loop_skips_blank_lines():
    stdin = io.StringIO("\n\n" + json.dumps(_req("initialize")) + "\n\n")
    stdout = io.StringIO()
    serve(stdin=stdin, stdout=stdout)
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    assert len(lines) == 1


# ----------------------------------------------------------------------
# tools_descriptor + catalog<->handler consistency
# ----------------------------------------------------------------------


def test_tools_descriptor_omits_destructive_annotation():
    """gpu-watch has no destructive tools, so no annotation should appear."""
    for t in tools_descriptor():
        assert "annotations" not in t


def test_every_tool_has_handler():
    cat_names = {t["name"] for t in TOOLS}
    assert cat_names == set(HANDLERS)


# ----------------------------------------------------------------------
# Per-tool dispatch — drive against a fake registry + runner + dcgm
# ----------------------------------------------------------------------


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    """Wires up the module-level singletons in gpu_mcp.tools to fakes
    so each test gets a fresh state."""
    runners: dict[str, _FakeRunner] = {}
    reg = NodeRegistry(tmp_path / "hosts.json")
    node = Node(name="g1", host="h", user="u", key_path="/k", dcgm_url="http://x:9400")
    reg.add(node)

    def runner_factory(n: Node):
        runners.setdefault(n.name, _FakeRunner(cluster=n))
        return runners[n.name]

    class _FakeDcgm:
        payload: dict = {}
        def fetch_selected(self, *_a, **_kw): return self.payload
        def fetch(self): return self.payload
    fake = _FakeDcgm()

    set_registry(reg)
    set_runner_factory(runner_factory)
    set_dcgm_factory(lambda url: fake)

    yield {"registry": reg, "runners": runners, "node": node, "dcgm": fake}

    # Reset to fresh defaults so subsequent test modules aren't tainted.
    set_registry(NodeRegistry(tmp_path / "absent.json"))
    set_runner_factory(lambda n: None)
    set_dcgm_factory(lambda url: None)


def _ok(resp):
    assert "isError" not in resp or resp["isError"] is False, resp
    return resp["content"][0]["text"]


def _err(resp):
    assert resp.get("isError") is True, resp
    return resp["content"][0]["text"]


def test_nodes_list_returns_registered_node(mcp_env):
    text = _ok(dispatch_tool("nodes_list", {}))
    assert "g1" in text


def test_node_status_routes(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 0, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("node_status", {"name": "g1"}))
    assert "H100" in text and "temperature_c" in text


def test_node_status_requires_name(mcp_env):
    text = _err(dispatch_tool("node_status", {}))
    assert "name is required" in text


def test_node_unknown_returns_error(mcp_env):
    text = _err(dispatch_tool("node_status", {"name": "missing"}))
    assert "no node registered" in text


def test_node_remapped_rows(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 3, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("node_remapped_rows", {"name": "g1"}))
    assert "correctable" in text
    assert '"correctable": 3' in text or '"correctable":3' in text


def test_node_ecc_uses_query_gpu(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 7, 12\n", ""),
            (0, "0, 0, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("node_ecc", {"name": "g1"}))
    assert "ecc_uncorrected_total" in text and "7" in text


def test_node_nvlink(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
            (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
            (0, "ok\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("node_nvlink", {"name": "g1"}))
    assert "nvlink_status" in text and "nvlink_check" in text


def test_node_xid(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "[time] NVRM: Xid (PCI:0000:01:00): 79, off bus\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("node_xid", {"name": "g1"}))
    assert '"xid": 79' in text or '"xid":79' in text


def test_node_dcgm_metrics(mcp_env):
    from gpuwatchlib import DcgmSample
    mcp_env["dcgm"].payload = {
        "DCGM_FI_DEV_GPU_TEMP": [DcgmSample(name="x", labels={"gpu": "0"}, value=35)],
    }
    text = _ok(dispatch_tool("node_dcgm_metrics", {"name": "g1"}))
    assert "DCGM_FI_DEV_GPU_TEMP" in text


def test_gpu_count_check_requires_expected(mcp_env):
    text = _err(dispatch_tool("gpu_count_check", {"name": "g1"}))
    assert "expected is required" in text


def test_gpu_count_check_returns_ok(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "", ""), (0, "", ""), (0, "GPU 0: x\nGPU 1: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("gpu_count_check", {"name": "g1", "expected": 2}))
    assert '"ok": true' in text or '"ok":true' in text


def test_gpu_count_check_returns_not_ok(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "", ""), (0, "", ""), (0, "GPU 0: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("gpu_count_check", {"name": "g1", "expected": 8}))
    assert '"ok": false' in text or '"ok":false' in text


def test_fleet_summary_iterates_registry(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 41, 90, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 0, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
        ]),
    )
    text = _ok(dispatch_tool("fleet_summary", {}))
    assert '"node": "g1"' in text and '"max_temp_c": 41' in text


def test_fleet_summary_surfaces_per_node_errors(mcp_env, monkeypatch):
    """If the runner factory blows up for one node, the summary still
    returns a row for that node with an `error` field — not a 500-style
    crash for the whole fleet."""
    def bad_factory(_n):
        raise ConnectionError("dial tcp")
    set_runner_factory(bad_factory)
    text = _ok(dispatch_tool("fleet_summary", {}))
    assert "ConnectionError" in text and "dial tcp" in text


def test_drain_advisor_returns_empty_on_clean_fleet(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 0, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
            (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
            (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
            (0, "ok\n", ""),
            (0, "", ""),
        ]),
    )
    text = _ok(dispatch_tool("drain_advisor", {}))
    assert '"nodes": []' in text or '"nodes":[]' in text


def test_drain_advisor_surfaces_hard_finding(mcp_env):
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 0, 2, 0, Yes\n", ""),                         # both uncorrectable + failure
            (0, "GPU 0: x\n", ""),
            (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
            (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
            (0, "ok\n", ""),
            (0, "", ""),
        ]),
    )
    text = _ok(dispatch_tool("drain_advisor", {}))
    assert "ROW_REMAP_FAILURE" in text
    assert "UNCORRECTABLE_REMAPPED_ROWS" in text


def test_drain_advisor_skips_gpu_count_when_expected_unset(mcp_env):
    """A node with 1 GPU should NOT produce a GPU_COUNT finding when
    expected_gpu_count is missing — the classifier short-circuits."""
    mcp_env["runners"].setdefault(
        "g1", _FakeRunner(cluster=mcp_env["node"], responses=[
            (0, "0, H100, uuid, 35, 60, 0, 81920, 0, 0, 0, 0\n", ""),
            (0, "0, 0, 0, 0, No\n", ""),
            (0, "GPU 0: x\n", ""),
            (0, "GPU 0:\n\t Link 0: 25 GB/s\n", ""),
            (0, "GPU 0:\n\t Link 0: Rx Errors: 0\n", ""),
            (0, "ok\n", ""),
            (0, "", ""),
        ]),
    )
    text = _ok(dispatch_tool("drain_advisor", {}))
    assert "GPU_COUNT" not in text


def test_unknown_tool_returns_error(mcp_env):
    text = _err(dispatch_tool("not_a_tool", {}))
    assert "unknown tool" in text


def test_tool_wraps_unexpected_exception(mcp_env, monkeypatch):
    """If a handler leaks something we didn't anticipate, dispatch
    catches it so the stdio transport never sees a Python traceback."""
    def boom(_a): raise RuntimeError("kaboom")
    monkeypatch.setitem(HANDLERS, "nodes_list", boom)
    text = _err(dispatch_tool("nodes_list", {}))
    assert "RuntimeError" in text and "kaboom" in text

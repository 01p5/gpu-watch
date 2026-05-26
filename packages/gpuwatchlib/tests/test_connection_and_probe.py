"""Connection + audit + NodeProbe-against-fake-runner tests."""
from __future__ import annotations

import sys
import types

import pytest

from gpuwatchlib import (
    JsonlAuditLogger, LocalRunner, Node, NodeProbe, NodeRegistry,
    ProbeError, SSHRunner, _FakeRunner,
)
from gpuwatchlib.audit import AuditRecord, NullAuditLogger


# ----------------------------------------------------------------------
# Node (de)serialize
# ----------------------------------------------------------------------


def test_node_round_trip_includes_dcgm_url():
    n = Node(name="g1", host="h", user="u", key_path="/k", dcgm_url="http://h:9400")
    raw = n.to_json()
    assert raw["dcgm_url"] == "http://h:9400"
    assert Node.from_json(raw) == n


def test_node_from_json_default_dcgm_url_is_none():
    n = Node.from_json({"name": "x", "host": "h", "user": "u", "key_path": "/k"})
    assert n.dcgm_url is None


# ----------------------------------------------------------------------
# NodeRegistry
# ----------------------------------------------------------------------


def test_registry_round_trip(tmp_path):
    reg = NodeRegistry(tmp_path / "hosts.json")
    reg.add(Node(name="g1", host="h", user="u", key_path="/k", dcgm_url="http://h:9400"))
    reg.add(Node(name="g2", host="h2", user="u", key_path="/k"))
    assert [n.name for n in reg.list()] == ["g1", "g2"]
    assert reg.get("g1").dcgm_url == "http://h:9400"
    reg.remove("g1")
    assert [n.name for n in reg.list()] == ["g2"]


def test_registry_unknown_get_raises(tmp_path):
    reg = NodeRegistry(tmp_path / "hosts.json")
    with pytest.raises(KeyError):
        reg.get("missing")


def test_registry_default_path_resolves_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GPU_WATCH_HOSTS", str(tmp_path / "alt.json"))
    reg = NodeRegistry()
    reg.add(Node(name="x", host="h", user="u", key_path="/k"))
    assert (tmp_path / "alt.json").exists()


# ----------------------------------------------------------------------
# LocalRunner — exercises subprocess path
# ----------------------------------------------------------------------


def test_local_runner_success_and_stdin():
    runner = LocalRunner()
    # stdin_data is forwarded to the subprocess
    r = runner.run([sys.executable, "-c", "import sys; print(sys.stdin.read().strip())"],
                   stdin_data="hello")
    assert r.ok and r.stdout.strip() == "hello"


def test_local_runner_handles_missing_binary():
    r = LocalRunner().run(["nonexistent-binary-12345"])
    assert r.returncode == 127


def test_local_runner_handles_timeout():
    r = LocalRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert r.returncode == 124


def test_local_runner_env_merges():
    r = LocalRunner(env={"GPU_WATCH_TEST_X": "1"}).run(
        [sys.executable, "-c", "import os; print(os.environ.get('GPU_WATCH_TEST_X'))"],
    )
    assert r.stdout.strip() == "1"


# ----------------------------------------------------------------------
# SSHRunner — stubbed paramiko
# ----------------------------------------------------------------------


def test_ssh_runner_quotes_args_and_returns_result(monkeypatch):
    import gpuwatchlib.connection as conn

    received: dict = {}

    class FakeChannel:
        def recv_exit_status(self): return 0

    class FakeStdout:
        channel = FakeChannel()
        def read(self): return b"hi"

    class FakeStdin:
        def write(self, data): received["stdin"] = data
        def flush(self): pass
        def close(self): pass

    class FakeStderr:
        def read(self): return b""

    class FakeClient:
        def set_missing_host_key_policy(self, _): pass
        def connect(self, **kw): received["connect"] = kw
        def exec_command(self, cmd, timeout=60):
            received["cmd"] = cmd
            return FakeStdin(), FakeStdout(), FakeStderr()
        def close(self): pass

    monkeypatch.setattr(conn, "paramiko", types.SimpleNamespace(
        SSHClient=FakeClient, AutoAddPolicy=lambda: None,
    ))

    runner = SSHRunner(Node(name="t", host="h", user="u", key_path="/k"))
    r = runner.run(["nvidia-smi", "-q", "name with space"], stdin_data="payload")
    assert r.stdout == "hi"
    assert received["cmd"] == "nvidia-smi -q 'name with space'"
    assert received["stdin"] == "payload"
    assert received["connect"]["look_for_keys"] is False


def test_ssh_runner_blows_up_without_paramiko(monkeypatch):
    import gpuwatchlib.connection as conn
    monkeypatch.setattr(conn, "paramiko", None)
    with pytest.raises(RuntimeError, match="paramiko is required"):
        SSHRunner(Node(name="t", host="h", user="u", key_path="/k"))


# ----------------------------------------------------------------------
# Audit logger
# ----------------------------------------------------------------------


def test_jsonl_audit_writes_pre_and_post(tmp_path):
    audit = JsonlAuditLogger(tmp_path / "a.jsonl")

    class R: returncode = 0; stdout = ""; stderr = ""; duration_s = 0.01

    with audit.around("dashboard", "g1", "ssh:status") as ctx:
        ctx.set_result(R())

    lines = (tmp_path / "a.jsonl").read_text().splitlines()
    assert len(lines) == 2
    import json
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["phase"] == "pre" and parsed[1]["phase"] == "post"
    assert parsed[1]["returncode"] == 0


def test_null_audit_logger_is_quiet():
    audit = NullAuditLogger()
    with audit.around("a", "b", "c"):
        pass
    audit.write(AuditRecord(record_id="x", timestamp=0, node="n", probe="p", phase="pre", actor="a"))
    # No assertion needed — the test passes if nothing crashed.


# ----------------------------------------------------------------------
# NodeProbe — driven by _FakeRunner so we don't shell out
# ----------------------------------------------------------------------


@pytest.fixture
def runner():
    return _FakeRunner(cluster=Node(name="g", host="h", user="u", key_path="/k"))


@pytest.fixture
def probe(runner):
    return NodeProbe(runner)


def test_probe_query_gpu_builds_argv_and_parses(runner, probe):
    runner.responses = [(0, "0, H100, GPU-x, 35, 60, 0, 81920, 0, 0, 0, 0\n", "")]
    out = probe.query_gpu()
    assert runner.calls[0][0] == "nvidia-smi"
    assert "--query-gpu=" in runner.calls[0][1]
    assert out[0]["temperature_c"] == 35


def test_probe_query_remapped_rows(runner, probe):
    runner.responses = [(0, "0, 0, 0, 0, No\n", "")]
    out = probe.query_remapped_rows()
    assert out[0]["failure"] is False
    assert "--query-remapped-rows=" in runner.calls[0][1]


def test_probe_list_gpus_counts_GPU_lines(runner, probe):
    runner.responses = [(0, "GPU 0: H100 (UUID: x)\nGPU 1: H100 (UUID: y)\nnoise\n", "")]
    assert probe.list_gpus() == 2


def test_probe_nvlink_status_calls_correct_command(runner, probe):
    runner.responses = [(0, "GPU 0:\n\t Link 0: 25 GB/s\n", "")]
    out = probe.nvlink_status()
    assert runner.calls[-1] == ["nvidia-smi", "nvlink", "--status"]
    assert out[0][0]["active"] is True


def test_probe_nvlink_errors_calls_correct_command(runner, probe):
    runner.responses = [(0, "GPU 0:\n\t Link 0: Rx Errors: 3\n", "")]
    out = probe.nvlink_errors()
    assert runner.calls[-1] == ["nvidia-smi", "nvlink", "-e"]
    assert out[0][0]["Rx Errors"] == 3


def test_probe_nvlink_check_remote_pipes_script_via_bash(runner, probe):
    runner.responses = [(0, "ok: 18 nvlinks up\n", "")]
    ok, detail = probe.nvlink_check_remote()
    assert ok and "nvlinks up" in detail
    assert runner.calls[-1] == ["bash", "-s"]


def test_probe_nvlink_check_remote_fail_collapses_output(runner, probe):
    runner.responses = [(3, "", "FAIL: 2 nvlink line(s) report inactive/down/off\n")]
    ok, detail = probe.nvlink_check_remote()
    assert ok is False and "inactive" in detail


def test_probe_xid_falls_back_to_journalctl_when_dmesg_fails(runner, probe):
    runner.responses = [
        (1, "", "dmesg: read kernel buffer failed: Operation not permitted"),
        (0, "May 25 12:34:56 host kernel: NVRM: Xid (PCI:0000:01:00): 13, x\n", ""),
    ]
    events = probe.xid_errors_from_dmesg()
    assert events == [{"pci": "0000:01:00", "xid": 13, "detail": "x",
                       "raw": "May 25 12:34:56 host kernel: NVRM: Xid (PCI:0000:01:00): 13, x"}]
    assert runner.calls[0][0] == "dmesg"
    assert runner.calls[1][0] == "journalctl"


def test_probe_xid_raises_when_both_logs_fail(runner, probe):
    runner.responses = [(1, "", "nope"), (1, "", "nope2")]
    with pytest.raises(ProbeError):
        probe.xid_errors_from_dmesg()


def test_probe_raises_with_result_attached(runner, probe):
    runner.responses = [(1, "", "nvidia-smi: command not found")]
    with pytest.raises(ProbeError) as exc:
        probe.query_gpu()
    assert exc.value.result is not None
    assert exc.value.result.returncode == 1

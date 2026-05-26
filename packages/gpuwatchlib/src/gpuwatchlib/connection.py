"""SSH connection layer for gpu-watch.

Same shape as slurm-mgr's slurmlib/connection.py but the registry holds
GPU nodes — each carries an optional ``dcgm_url`` (where the
dcgm-exporter is reachable, typically ``http://<host>:9400``) alongside
the SSH coordinates. Nodes can be probed via SSH, dcgm, or both.

Two runners (``SSHRunner``, ``LocalRunner``) + a ``_FakeRunner`` for
tests. Connection-per-call: GPU health probes are short-lived and
the connect cost is dominated by the sampled output. Naming
``Cluster``/``ClusterRegistry`` is kept identical to slurm-mgr's
shape so tooling that knows one knows both — the package re-exports
them as ``Node``/``NodeRegistry`` for readability.
"""
from __future__ import annotations

import json
import os
import shlex
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None  # type: ignore[assignment]


# ---------------------------------------------------------------------
# Node model + registry
# ---------------------------------------------------------------------


@dataclass(slots=True)
class Cluster:
    """A reachable GPU node.

    ``dcgm_url`` is optional — set it when dcgm-exporter is running on
    the node and reachable from wherever this dashboard lives.
    """
    name: str
    host: str
    user: str
    key_path: str
    port: int = 22
    jump_host: str | None = None
    dcgm_url: str | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, raw: dict) -> "Cluster":
        return cls(
            name=raw["name"],
            host=raw["host"],
            user=raw["user"],
            key_path=raw["key_path"],
            port=int(raw.get("port", 22)),
            jump_host=raw.get("jump_host"),
            dcgm_url=raw.get("dcgm_url"),
        )


def _default_registry_path() -> Path:
    """Resolved at call time so tests can override GPU_WATCH_HOSTS."""
    return Path(
        os.environ.get("GPU_WATCH_HOSTS", Path.home() / ".gpu-watch" / "hosts.json")
    )


DEFAULT_REGISTRY_PATH = _default_registry_path()


class ClusterRegistry:
    """JSON-on-disk registry. Concurrency-naive (last-writer-wins)."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else _default_registry_path()

    def _load(self) -> list[Cluster]:
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text() or "{}")
        return [Cluster.from_json(c) for c in raw.get("clusters", [])]

    def _save(self, clusters: Iterable[Cluster]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"clusters": [c.to_json() for c in clusters]}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(self.path)

    def list(self) -> list[Cluster]:
        return self._load()

    def get(self, name: str) -> Cluster:
        for c in self._load():
            if c.name == name:
                return c
        raise KeyError(f"no node registered named {name!r}")

    def add(self, cluster: Cluster) -> None:
        current = [c for c in self._load() if c.name != cluster.name]
        current.append(cluster)
        self._save(current)

    def remove(self, name: str) -> None:
        self._save([c for c in self._load() if c.name != name])


# ---------------------------------------------------------------------
# CommandResult + runners
# ---------------------------------------------------------------------


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    stdout: str
    stderr: str
    returncode: int
    duration_s: float
    cluster: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class SSHRunner:
    """One-shot SSH command executor for a single node."""

    def __init__(self, cluster: Cluster, connect_timeout: int = 10):
        if paramiko is None:  # pragma: no cover
            raise RuntimeError("paramiko is required for SSHRunner")
        self.cluster = cluster
        self.connect_timeout = connect_timeout

    def _connect(self) -> "paramiko.SSHClient":
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        sock = None
        if self.cluster.jump_host:
            sock = self._open_jump_channel()
        client.connect(
            hostname=self.cluster.host,
            port=self.cluster.port,
            username=self.cluster.user,
            key_filename=self.cluster.key_path,
            timeout=self.connect_timeout,
            banner_timeout=self.connect_timeout,
            auth_timeout=self.connect_timeout,
            allow_agent=False,
            look_for_keys=False,
            sock=sock,
        )
        return client

    def _open_jump_channel(self):
        jump_user, _, jump_rest = (self.cluster.jump_host or "").partition("@")
        jump_host, _, jump_port = jump_rest.partition(":")
        jump_port_i = int(jump_port) if jump_port else 22
        jump = paramiko.SSHClient()
        jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        jump.connect(
            hostname=jump_host,
            port=jump_port_i,
            username=jump_user or self.cluster.user,
            key_filename=self.cluster.key_path,
            timeout=self.connect_timeout,
            allow_agent=False, look_for_keys=False,
        )
        transport = jump.get_transport()
        if transport is None:  # pragma: no cover
            raise RuntimeError("jump host transport not established")
        return transport.open_channel(
            kind="direct-tcpip",
            dest_addr=(self.cluster.host, self.cluster.port),
            src_addr=("127.0.0.1", 0),
        )

    def run(self, argv: list[str], timeout: int = 60,
            stdin_data: str | None = None) -> CommandResult:
        """Execute argv on the remote. ``stdin_data`` is optional — used
        when piping a script over ``bash -s`` (the watchdog's nvlink
        check shape).
        """
        cmd = " ".join(shlex.quote(a) for a in argv)
        started = time.monotonic()
        client = self._connect()
        try:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            if stdin_data is not None:
                stdin.write(stdin_data)
                stdin.flush()
            stdin.close()
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            rc = stdout.channel.recv_exit_status()
        finally:
            client.close()
        return CommandResult(
            argv=list(argv), stdout=out, stderr=err, returncode=rc,
            duration_s=time.monotonic() - started, cluster=self.cluster.name,
        )


class LocalRunner:
    """``subprocess`` drop-in for tests and on-node deploys."""

    def __init__(self, cluster_name: str = "local", env: dict | None = None):
        self.cluster = Cluster(name=cluster_name, host="localhost", user="local", key_path="", port=0)
        self.env = env

    def run(self, argv: list[str], timeout: int = 60,
            stdin_data: str | None = None) -> CommandResult:
        import subprocess  # noqa: PLC0415 - lazy
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, **(self.env or {})},
                input=stdin_data, check=False,
            )
            out, err, rc = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as exc:
            out, err, rc = exc.stdout or "", (exc.stderr or "") + "\n[timeout]", 124
        except FileNotFoundError as exc:
            out, err, rc = "", f"{exc}", 127
        return CommandResult(
            argv=list(argv),
            stdout=out if isinstance(out, str) else out.decode("utf-8", "replace"),
            stderr=err if isinstance(err, str) else err.decode("utf-8", "replace"),
            returncode=rc,
            duration_s=time.monotonic() - started,
            cluster=self.cluster.name,
        )


Runner = SSHRunner | LocalRunner


@dataclass(slots=True)
class _FakeRunner:
    """Test seam. Pops canned (returncode, stdout, stderr) tuples per
    ``run()`` call. Records calls in ``self.calls`` for assertions."""
    cluster: Cluster
    responses: list[tuple[int, str, str]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def run(self, argv: list[str], timeout: int = 60,
            stdin_data: str | None = None) -> CommandResult:
        self.calls.append(list(argv))
        if not self.responses:
            rc, out, err = 0, "", ""
        else:
            rc, out, err = self.responses.pop(0)
        return CommandResult(
            argv=list(argv), stdout=out, stderr=err,
            returncode=rc, duration_s=0.001, cluster=self.cluster.name,
        )

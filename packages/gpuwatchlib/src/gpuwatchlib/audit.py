"""Append-only JSONL audit log of every GPU probe.

Same shape as slurm-mgr's JsonlAuditLogger so cross-project tooling stays
uniform. Probes are read-only here so the log mostly carries timing +
return code, not destructive context.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_AUDIT_PATH = Path(
    os.environ.get("GPU_WATCH_AUDIT", Path.home() / ".gpu-watch" / "audit.jsonl")
)


@dataclass(slots=True)
class AuditRecord:
    record_id: str
    timestamp: float
    node: str
    probe: str                  # "ssh:nvidia-smi-query" | "dcgm:metrics" | …
    phase: str                  # "pre" | "post"
    actor: str                  # "dashboard" | "mcp"
    returncode: int | None = None
    duration_s: float | None = None
    note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None or k == "returncode"}


class NullAuditLogger:
    path = None

    def write(self, record: AuditRecord) -> None:
        return None

    def around(self, actor: str, node: str, probe: str,
               note: str | None = None) -> "AuditContext":
        return AuditContext(self, actor, node, probe, note)  # type: ignore[arg-type]


class JsonlAuditLogger:
    """One JSON object per line, fsync'd. Never rewritten in place."""

    def __init__(self, path: Path | str = DEFAULT_AUDIT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        line = json.dumps(record.to_json(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def around(self, actor: str, node: str, probe: str,
               note: str | None = None) -> "AuditContext":
        return AuditContext(self, actor, node, probe, note)


@dataclass(slots=True)
class AuditContext:
    """Pre/post records around a probe. ``set_result`` is optional —
    if no result is set, the post record records only timing."""
    logger: JsonlAuditLogger
    actor: str
    node: str
    probe: str
    note: str | None = None
    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    _result: Any = None
    _started: float = field(default_factory=time.monotonic)

    def __enter__(self) -> "AuditContext":
        self.logger.write(AuditRecord(
            record_id=self.record_id,
            timestamp=time.time(),
            node=self.node,
            probe=self.probe,
            phase="pre",
            actor=self.actor,
            note=self.note,
        ))
        return self

    def set_result(self, result) -> None:
        self._result = result

    def __exit__(self, exc_type, exc, tb) -> None:
        result = self._result
        self.logger.write(AuditRecord(
            record_id=self.record_id,
            timestamp=time.time(),
            node=self.node,
            probe=self.probe,
            phase="post",
            actor=self.actor,
            returncode=getattr(result, "returncode", None),
            duration_s=time.monotonic() - self._started,
            note=str(exc) if exc else self.note,
        ))

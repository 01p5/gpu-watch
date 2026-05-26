# gpuwatchlib

The pure-Python core for gpu-watch. Two probe paths + one classifier:

- **`probe_ssh.py`** — `NodeProbe`: shells `nvidia-smi` over SSH and parses the CSV. Covers `--query-gpu`, `--query-remapped-rows`, `-L`, `nvlink --status`, `nvlink -e`, `topo -m`, and an `xid_errors_from_dmesg()` scrape.
- **`probe_dcgm.py`** — `DcgmExporterClient`: HTTP-fetches `/metrics` from dcgm-exporter (default port 9400), parses the Prometheus text format with no `prometheus_client` dep, returns `{metric_name: [Sample(labels, value)]}`.
- **`classifier.py`** — `classify(node, signals) -> list[Finding]`: maps raw probe output into the same drain categories as the legacy `gpu-watchdog.py`: `XID`, `ROW_REMAP_FAILURE`, `UNCORRECTABLE_REMAPPED_ROWS`, `CORRECTABLE_REMAPPED_ROWS`, `GPU_COUNT`, plus NVLink subchecks.

Plus the shared `Node`, `NodeRegistry`, `SSHRunner`, `LocalRunner`, `JsonlAuditLogger` that the dashboard and MCP both depend on.

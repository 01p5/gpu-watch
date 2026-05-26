# gpu-watch

GPU health monitoring for HPC clusters. Two data sources, no Prometheus required:

1. **Direct SSH** — `nvidia-smi` queries (basic state + ECC + row-remaps + NVLink + topology) and `dmesg` / `journalctl` XID scrape.
2. **dcgm-exporter `/metrics` HTTP** — fetched and parsed directly (Prometheus text format), no scrape pipeline in the middle.

Sibling project to [slurm-mgr](https://github.com/01p5/slurm-mgr) — same shape, same look-and-feel. Two surfaces:

- **Dashboard.** React + Tailwind console for inspecting GPU health across a fleet of nodes. Per-node detail, NVLink matrix, ECC + row-remap counters, XID feed, dcgm-exporter metrics, drain advisor.
- **MCP server.** Hand-rolled JSON-RPC 2.0 stdio MCP (protocol `2024-11-05`) exposing the same checks as tools — plugs into [Olympus](https://github.com/01p5/01p5) via `MCPServerConfig`.

Read-only. This project diagnoses; it does not drain or reboot. Use [slurm-mgr](https://github.com/01p5/slurm-mgr) to act on what it surfaces.

## What it watches

Drain-candidate categories (same shape as the legacy Prometheus-backed `gpu-watchdog.py`, kept verbatim for ops continuity):

| Category | Source | Meaning |
|----------|--------|---------|
| `XID` | `dmesg` / `journalctl -k` / dcgm `DCGM_FI_DEV_XID_ERRORS` | Recent XID errors (NVRM driver-level GPU faults) |
| `ROW_REMAP_FAILURE` | `nvidia-smi --query-remapped-rows=remapped_rows.failure` | Row remap engine itself failed |
| `UNCORRECTABLE_REMAPPED_ROWS` | `remapped_rows.uncorrectable` | Uncorrectable ECC errors mapped out HBM rows |
| `CORRECTABLE_REMAPPED_ROWS` | `remapped_rows.correctable` | Correctable HBM ECC errors above threshold |
| `GPU_COUNT` | `nvidia-smi -L` | Node sees fewer GPUs than configured |

Plus the NVLink subchecks (`nvidia-smi nvlink --status` / `-e` / `topo -m`) lifted directly from the legacy watchdog's inline bash, so failure semantics stay identical.

## Layout

```
gpu-watch/
├── packages/
│   ├── gpuwatchlib/        # SDK — SSH + NodeProbe + DcgmExporterClient + classifier
│   ├── gpu-dashboard/      # HTTP backend + React SPA
│   └── gpu-mcp/            # MCP server (stdio JSON-RPC 2024-11-05)
└── .github/workflows/
```

## Quick start

```bash
git clone git@github.com:01p5/gpu-watch.git && cd gpu-watch
pip install -e packages/gpuwatchlib -e packages/gpu-dashboard -e packages/gpu-mcp

# Run the dashboard (backend on :8780)
python -m gpu_dashboard.server

# In another terminal: vite dev (:5175 → proxies to :8780)
cd packages/gpu-dashboard/frontend
npm install
npm run dev
```

Register a node on the Hosts page (host + SSH user + key path + optional dcgm-exporter URL on port 9400) and the rest of the pages light up.

## MCP

```python
from agentlib import MCPServerConfig
cfg = MCPServerConfig(
    name="gpu_prod",
    command="gpu-mcp",
    # No destructive ops — pure diagnostics.
)
```

Tools surfaced: `nodes_list`, `node_status`, `node_ecc`, `node_remapped_rows`, `node_nvlink`, `node_xid`, `node_dcgm_metrics`, `fleet_summary`, `fleet_unhealthy`, `drain_advisor`, `gpu_count_check`.

## Testing

```bash
pip install -e packages/gpuwatchlib -e packages/gpu-dashboard -e packages/gpu-mcp
pip install pytest pytest-cov
pytest                                   # gated at 80% via pyproject.toml fail_under
```

CI (`.github/workflows/ci.yml`) runs pytest + frontend typecheck/build on every push and PR.

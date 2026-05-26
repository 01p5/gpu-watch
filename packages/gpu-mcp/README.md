# gpu-mcp

Hand-rolled JSON-RPC 2.0 stdio MCP server (protocol `2024-11-05`)
exposing the gpu-watch probes as MCP tools. Same wire shape as the
demo-mcp-server in [Olympus](https://github.com/01p5/01p5), so it
plugs straight in.

All tools are read-only. The gpu-watch project doesn't drain or
reboot — diagnostics only. See [slurm-mgr](https://github.com/01p5/slurm-mgr)
for the destructive ops that act on what gpu-watch surfaces.

## Run

```bash
pip install -e ../gpuwatchlib -e .
gpu-mcp                         # uses ~/.gpu-watch/hosts.json registry
```

## Olympus wiring

```python
from agentlib import MCPServerConfig
cfg = MCPServerConfig(
    name="gpu",
    command="gpu-mcp",
    # No destructive set — every gpu-watch tool is read-only.
)
```

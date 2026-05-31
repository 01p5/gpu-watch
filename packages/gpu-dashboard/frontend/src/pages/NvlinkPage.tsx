import { RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox } from "../components/Atoms";
import { useFetch } from "../hooks";
import { useNodes, useNodePath } from "../NodeContext";

type NvlinkResponse = {
  nvlink_status?: Record<string, { link: number; raw: string; active: boolean }[]>;
  nvlink_errors?: Record<string, Record<string, Record<string, number>>>;
  nvlink_check?: { passed: boolean; detail: string } | null;
  errors?: { probe: string; error: string }[];
};

export function NvlinkPage() {
  const { active } = useNodes();
  const path = useNodePath();
  const { data, error, loading, reload } = useFetch<NvlinkResponse>(active ? path("/nvlink") : null, 30_000);

  return (
    <div className="p-4 h-full grid grid-rows-[auto_auto_1fr_1fr] gap-3 min-h-0">
      <div className="flex items-center justify-between">
        <h2 className="text-sm text-text-secondary font-mono">
          {active ? `NVLink on ${active}` : "no node selected"}
        </h2>
        <Button size="sm" variant="ghost" icon={<RefreshCw size={14} strokeWidth={2.25} />} onClick={reload} loading={loading}>reload</Button>
      </div>

      {data?.nvlink_check && (
        <div className="px-3 py-2 bg-dark-secondary border border-border-subtle rounded-sm flex items-center gap-3">
          <span className="text-xs font-display uppercase tracking-[0.5px] text-text-secondary">Watchdog inline check</span>
          {data.nvlink_check.passed
            ? <Badge tone="green">passed</Badge>
            : <Badge tone="red">failed</Badge>}
          <span className="font-mono text-xs text-text-primary truncate">{data.nvlink_check.detail}</span>
        </div>
      )}

      <Card title="Link status" className="h-full">
        {!active && <EmptyState message="Pick a node from the topbar." />}
        {error && <ErrorBox message={error} />}
        {data?.nvlink_status && Object.keys(data.nvlink_status).length === 0 && (
          <EmptyState message="No link data — nvidia-smi nvlink --status returned no Link lines." />
        )}
        {data?.nvlink_status && Object.entries(data.nvlink_status).map(([gpu, links]) => (
          <div key={gpu} className="px-3 py-2 border-b border-border-subtle">
            <div className="text-xs text-text-secondary font-mono mb-1">GPU {gpu}</div>
            <div className="grid grid-cols-12 gap-1.5">
              {links.map((l) => (
                <div key={l.link} className={`px-1.5 py-1 rounded-sm border text-center font-mono text-[10px] ${
                  l.active
                    ? "bg-accent-green/10 border-accent-green/25 text-accent-green"
                    : "bg-accent-red/10 border-accent-red/25 text-accent-red"
                }`} title={l.raw}>
                  {l.link}
                </div>
              ))}
            </div>
          </div>
        ))}
      </Card>

      <Card title="Error counters (non-zero only)">
        {data?.nvlink_errors && (
          <ErrorMatrix errors={data.nvlink_errors} />
        )}
      </Card>
    </div>
  );
}

function ErrorMatrix({ errors }: {
  errors: Record<string, Record<string, Record<string, number>>>;
}) {
  const rows: { gpu: string; link: string; counter: string; count: number }[] = [];
  for (const [gpu, link_map] of Object.entries(errors)) {
    for (const [link, counters] of Object.entries(link_map)) {
      for (const [counter, count] of Object.entries(counters)) {
        if (count > 0) rows.push({ gpu, link, counter, count });
      }
    }
  }
  if (rows.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-text-muted font-mono text-xs">
        all counters are zero — no nvlink errors observed
      </div>
    );
  }
  return (
    <table className="w-full font-mono text-xs">
      <thead className="bg-dark-tertiary">
        <tr>
          {["GPU", "Link", "Counter", "Count"].map((h) => (
            <th key={h} className="text-left font-semibold text-text-secondary uppercase tracking-[0.5px] px-3 py-2 border-b border-border-subtle">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-border-subtle/50">
            <td className="px-3 py-1.5">{r.gpu}</td>
            <td className="px-3 py-1.5">{r.link}</td>
            <td className="px-3 py-1.5">{r.counter}</td>
            <td className="px-3 py-1.5 text-accent-red">{r.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

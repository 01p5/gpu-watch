import { useState } from "react";
import { RefreshCw, AlertTriangle } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox } from "../components/Atoms";
import { useFetch } from "../hooks";

// Read-only advisor: classifies every node + surfaces nodes with >=1
// HARD finding. The dashboard never drains — this just tells you what
// slurm-mgr should act on.

type Finding = {
  node: string; category: string; severity: string;
  gpu: number | null; message: string;
};

type AdvisorResponse = {
  nodes: ({ node: string; findings: Finding[] } | { node: string; error: string })[];
};

const CATEGORY_TONES: Record<string, "red" | "orange" | "yellow" | "teal" | "purple"> = {
  XID: "red",
  ROW_REMAP_FAILURE: "red",
  UNCORRECTABLE_REMAPPED_ROWS: "red",
  CORRECTABLE_REMAPPED_ROWS: "yellow",
  GPU_COUNT: "orange",
  NVLINK: "purple",
};

export function AdvisorPage() {
  const [expected, setExpected] = useState("");
  const q = expected.trim() ? `?expected_gpu_count=${encodeURIComponent(expected)}` : "";
  const { data, error, loading, reload } = useFetch<AdvisorResponse>(`/fleet/unhealthy${q}`, 0);
  const rows = data?.nodes ?? [];

  return (
    <div className="p-4 h-full grid grid-rows-[auto_1fr] gap-3 min-h-0">
      <div className="flex items-center gap-3">
        <label className="text-xs font-mono text-text-secondary flex items-center gap-2">
          Expected GPU count per node
          <input
            value={expected} onChange={(e) => setExpected(e.target.value)}
            placeholder="e.g. 8"
            className="bg-dark-tertiary border border-border-subtle rounded-sm px-2 py-1 font-mono text-xs w-24"
          />
        </label>
        <span className="text-[11px] text-text-muted">
          ↳ enables the GPU_COUNT rule when a node sees fewer GPUs than this
        </span>
        <div className="flex-1" />
        <Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={reload} loading={loading}>re-classify</Button>
      </div>

      <Card title="Nodes with HARD findings" className="h-full">
        {error && <ErrorBox message={error} />}
        {!error && rows.length === 0 && !loading && (
          <EmptyState message="Nothing to act on. All nodes pass classification or have no data." />
        )}
        {rows.map((row, i) => (
          <div key={i} className="border-b border-border-subtle px-3 py-2">
            <div className="flex items-center gap-2 mb-1.5">
              <AlertTriangle size={14} className="text-accent-orange" />
              <span className="font-mono text-sm text-accent-teal">{row.node}</span>
              {"error" in row && <span className="text-accent-red font-mono text-xs">{row.error}</span>}
            </div>
            {"findings" in row && (
              <ul className="space-y-1 font-mono text-xs">
                {row.findings.map((f, j) => (
                  <li key={j} className="flex items-start gap-2">
                    <Badge tone={CATEGORY_TONES[f.category] ?? "neutral"}>{f.category}</Badge>
                    <span className="text-text-primary">{f.message}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </Card>
    </div>
  );
}

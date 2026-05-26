import { RefreshCw } from "lucide-react";
import { Button, Card, EmptyState, ErrorBox } from "../components/Atoms";
import { useFetch } from "../hooks";
import { useNodes, useNodePath } from "../NodeContext";

type DcgmResponse = {
  available: boolean;
  reason?: string;
  metrics?: Record<string, { labels: Record<string, string>; value: number; gpu: number | null }[]>;
};

export function DcgmPage() {
  const { active } = useNodes();
  const path = useNodePath();
  const { data, error, loading, reload } = useFetch<DcgmResponse>(active ? path("/dcgm") : null, 15_000);

  return (
    <div className="p-4 h-full">
      <Card
        title={`dcgm-exporter — ${active ?? "no node"}`}
        className="h-full"
        actions={<Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={reload} loading={loading}>reload</Button>}
      >
        {!active && <EmptyState message="Pick a node from the topbar." />}
        {error && <ErrorBox message={error} />}
        {data && !data.available && <EmptyState message={data.reason ?? "dcgm-exporter unavailable"} />}
        {data?.metrics && (
          <table className="w-full font-mono text-xs">
            <thead className="sticky top-0 bg-dark-tertiary">
              <tr>
                {["Metric", "GPU", "Value"].map((h) => (
                  <th key={h} className="text-left font-semibold text-text-secondary uppercase tracking-[0.5px] px-3 py-2 border-b border-border-subtle">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.metrics).flatMap(([name, samples]) =>
                samples.map((s, i) => (
                  <tr key={`${name}-${i}`} className="border-b border-border-subtle/50 hover:bg-dark-panel/60">
                    <td className="px-3 py-1.5 text-accent-teal">{name}</td>
                    <td className="px-3 py-1.5">{s.gpu ?? "—"}</td>
                    <td className="px-3 py-1.5 text-text-primary">{s.value}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

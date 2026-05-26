import { RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox } from "../components/Atoms";
import { useFetch } from "../hooks";
import { useNodes, useNodePath } from "../NodeContext";

type XidResponse = {
  events?: { pci: string; xid: number; detail: string; raw: string }[];
  error?: string;
};

export function XidPage() {
  const { active } = useNodes();
  const path = useNodePath();
  const { data, error, loading, reload } = useFetch<XidResponse>(active ? path("/xid") : null, 30_000);
  const events = data?.events ?? [];

  return (
    <div className="p-4 h-full">
      <Card
        title={`XID events — ${active ?? "no node"}`}
        className="h-full"
        actions={<Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={reload} loading={loading}>reload</Button>}
      >
        {!active && <EmptyState message="Pick a node from the topbar." />}
        {error && <ErrorBox message={error} />}
        {data?.error && <ErrorBox message={data.error} />}
        {!error && events.length === 0 && active && (
          <EmptyState message="No NVRM Xid events in dmesg / journalctl. Clean." />
        )}
        {events.length > 0 && (
          <table className="w-full font-mono text-xs">
            <thead className="sticky top-0 bg-dark-tertiary">
              <tr>
                {["XID", "PCI", "Detail"].map((h) => (
                  <th key={h} className="text-left font-semibold text-text-secondary uppercase tracking-[0.5px] px-3 py-2 border-b border-border-subtle">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i} className="border-b border-border-subtle/50 hover:bg-dark-panel/60">
                  <td className="px-3 py-2"><Badge tone="red">{e.xid}</Badge></td>
                  <td className="px-3 py-2 text-text-secondary">{e.pci}</td>
                  <td className="px-3 py-2 text-text-primary truncate max-w-[60ch]" title={e.raw}>{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

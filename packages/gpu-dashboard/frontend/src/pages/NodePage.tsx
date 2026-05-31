import { RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox, Table, type Column, tempTone } from "../components/Atoms";
import { useFetch } from "../hooks";
import { useNodes, useNodePath } from "../NodeContext";

type Gpu = {
  index: number;
  name: string;
  uuid: string;
  temperature_c: number | null;
  power_w: number | null;
  memory_used_mib: number | null;
  memory_total_mib: number | null;
  util_gpu_pct: number | null;
  util_mem_pct: number | null;
  ecc_uncorrected_total: number | null;
  ecc_corrected_total: number | null;
};

type StatusResponse = {
  query_gpu?: Gpu[];
  remapped_rows?: { index: number; correctable: number; uncorrectable: number; pending: number; failure: boolean }[];
  gpu_count?: number;
  errors?: { probe: string; error: string }[];
};

export function NodePage() {
  const { active } = useNodes();
  const path = useNodePath();
  const { data, error, loading, reload } = useFetch<StatusResponse>(active ? path("/status") : null, 15_000);
  const gpus = data?.query_gpu ?? [];
  const rows = data?.remapped_rows ?? [];
  const probeErrors = data?.errors ?? [];

  const gpuColumns: Column<Gpu>[] = [
    { key: "i", header: "#", cell: (g) => g.index },
    { key: "name", header: "Model", cell: (g) => g.name },
    { key: "temp", header: "Temp",
      cell: (g) => g.temperature_c == null ? "—" : <Badge tone={tempTone(g.temperature_c)}>{g.temperature_c}°C</Badge> },
    { key: "pw",  header: "Power", align: "right", cell: (g) => g.power_w == null ? "—" : `${g.power_w.toFixed(0)} W` },
    { key: "mem", header: "Memory",
      cell: (g) => g.memory_used_mib == null ? "—"
        : `${g.memory_used_mib.toLocaleString()} / ${g.memory_total_mib?.toLocaleString()} MiB` },
    { key: "ug", header: "Util", align: "right", cell: (g) => g.util_gpu_pct == null ? "—" : `${g.util_gpu_pct}%` },
    { key: "um", header: "Mem util", align: "right", cell: (g) => g.util_mem_pct == null ? "—" : `${g.util_mem_pct}%` },
    { key: "eu", header: "ECC uncorr.", align: "right",
      cell: (g) => {
        const v = g.ecc_uncorrected_total;
        if (v == null) return "—";
        return v > 0 ? <span className="text-accent-red">{v}</span> : <span className="text-text-muted">0</span>;
      } },
    { key: "ec", header: "ECC corr.", align: "right",
      cell: (g) => {
        const v = g.ecc_corrected_total;
        if (v == null) return "—";
        return v > 0 ? <span className="text-accent-yellow">{v}</span> : <span className="text-text-muted">0</span>;
      } },
  ];

  const remapColumns: Column<typeof rows[number]>[] = [
    { key: "i", header: "GPU", cell: (r) => r.index },
    { key: "c", header: "Correctable",  align: "right",
      cell: (r) => r.correctable > 0
        ? <Badge tone="yellow">{r.correctable}</Badge> : <span className="text-text-muted">0</span> },
    { key: "u", header: "Uncorrectable", align: "right",
      cell: (r) => r.uncorrectable > 0
        ? <Badge tone="red">{r.uncorrectable}</Badge> : <span className="text-text-muted">0</span> },
    { key: "p", header: "Pending", align: "right",
      cell: (r) => r.pending > 0
        ? <Badge tone="orange">{r.pending}</Badge> : <span className="text-text-muted">0</span> },
    { key: "f", header: "Failure",
      cell: (r) => r.failure ? <Badge tone="red">yes</Badge> : <Badge tone="green">no</Badge> },
  ];

  return (
    <div className="p-4 h-full grid grid-rows-[auto_1fr_auto] gap-3 min-h-0">
      <div className="flex items-center justify-between">
        <h2 className="text-sm text-text-secondary font-mono">
          {active ? <>node <span className="text-accent-teal">{active}</span> · GPU count <span className="text-text-primary">{data?.gpu_count ?? "—"}</span></> : "no node selected"}
        </h2>
        <Button size="sm" variant="ghost" icon={<RefreshCw size={14} strokeWidth={2.25} />} onClick={reload} loading={loading}>reload</Button>
      </div>

      <Card title="GPUs" className="h-full">
        {!active && <EmptyState message="Pick a node from the topbar." />}
        {error && <ErrorBox message={error} />}
        {active && !error && (
          <div className="grid grid-rows-2 h-full">
            <div className="overflow-auto"><Table columns={gpuColumns} rows={gpus} rowKey={(g) => String(g.index)} empty="No GPUs returned." /></div>
            <div className="overflow-auto border-t border-border-subtle">
              <h3 className="px-3 py-2 font-display text-[11px] uppercase tracking-[0.5px] text-text-secondary">Row remap (HBM)</h3>
              <Table columns={remapColumns} rows={rows} rowKey={(r) => String(r.index)} empty="No remap data." />
            </div>
          </div>
        )}
      </Card>

      {probeErrors.length > 0 && (
        <Card title="Probe errors">
          <ul className="px-3 py-2 font-mono text-xs space-y-1">
            {probeErrors.map((e, i) => (
              <li key={i}><span className="text-accent-orange">{e.probe}</span>: {e.error}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

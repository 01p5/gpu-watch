import { RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox, Table, type Column, tempTone } from "../components/Atoms";
import { useFetch } from "../hooks";

type FleetRow = {
  node: string;
  host: string;
  gpu_count?: number;
  max_temp_c?: number | null;
  sum_power_w?: number;
  sum_ecc_uncorrected?: number;
  sum_ecc_corrected?: number;
  any_remap_failure?: boolean;
  sum_uncorrectable_remapped?: number;
  sum_correctable_remapped?: number;
  error?: string;
  errors?: { probe: string; error: string }[];
};

export function FleetPage() {
  const { data, error, loading, reload } = useFetch<{ rows: FleetRow[] }>("/fleet/summary", 30_000);
  const rows = data?.rows ?? [];

  const columns: Column<FleetRow>[] = [
    { key: "node", header: "Node", cell: (r) => <span className="text-accent-teal">{r.node}</span> },
    { key: "host", header: "Host", cell: (r) => r.host },
    { key: "gpus", header: "GPUs", align: "right", cell: (r) => r.gpu_count ?? "—" },
    { key: "temp", header: "Hottest GPU",
      cell: (r) => r.max_temp_c == null
        ? "—"
        : <Badge tone={tempTone(r.max_temp_c)}>{r.max_temp_c}°C</Badge> },
    { key: "pw", header: "Sum power (W)", align: "right",
      cell: (r) => r.sum_power_w == null ? "—" : r.sum_power_w.toFixed(0) },
    { key: "eu", header: "ECC uncorr.", align: "right",
      cell: (r) => {
        const v = r.sum_ecc_uncorrected ?? 0;
        return v > 0 ? <span className="text-accent-red">{v}</span> : <span className="text-text-muted">0</span>;
      } },
    { key: "ec", header: "ECC corr.", align: "right",
      cell: (r) => {
        const v = r.sum_ecc_corrected ?? 0;
        return v > 0 ? <span className="text-accent-yellow">{v}</span> : <span className="text-text-muted">0</span>;
      } },
    { key: "ru", header: "Remap uncorr.", align: "right",
      cell: (r) => {
        const v = r.sum_uncorrectable_remapped ?? 0;
        return v > 0 ? <Badge tone="red">{v}</Badge> : <span className="text-text-muted">0</span>;
      } },
    { key: "rc", header: "Remap corr.", align: "right",
      cell: (r) => {
        const v = r.sum_correctable_remapped ?? 0;
        return v > 0 ? <Badge tone="yellow">{v}</Badge> : <span className="text-text-muted">0</span>;
      } },
    { key: "rf", header: "Remap fail",
      cell: (r) => r.any_remap_failure ? <Badge tone="red">yes</Badge> : <Badge tone="green">no</Badge> },
    { key: "err", header: "Errors",
      cell: (r) => {
        if (r.error) return <span className="text-accent-red">{r.error}</span>;
        if (!r.errors || r.errors.length === 0) return <span className="text-text-muted">—</span>;
        return <span className="text-accent-orange">{r.errors.length} probe error(s)</span>;
      } },
  ];

  return (
    <div className="p-4 h-full">
      <Card
        title="Fleet — at-a-glance health"
        className="h-full"
        actions={<Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} onClick={reload} loading={loading}>reload</Button>}
      >
        {error && <ErrorBox message={error} />}
        {!error && rows.length === 0 && !loading && (
          <EmptyState message="No nodes registered yet — add one on the Hosts page." />
        )}
        {!error && rows.length > 0 && (
          <Table columns={columns} rows={rows} rowKey={(r) => r.node} />
        )}
      </Card>
    </div>
  );
}

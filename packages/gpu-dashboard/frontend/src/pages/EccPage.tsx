import { RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox, Table, type Column } from "../components/Atoms";
import { useFetch } from "../hooks";

// ECC + row-remap aggregated across the fleet — same shape as the fleet
// summary endpoint, but with the columns dropped to focus on memory
// health.

type FleetRow = {
  node: string;
  sum_ecc_uncorrected?: number;
  sum_ecc_corrected?: number;
  any_remap_failure?: boolean;
  sum_uncorrectable_remapped?: number;
  sum_correctable_remapped?: number;
  error?: string;
};

export function EccPage() {
  const { data, error, loading, reload } = useFetch<{ rows: FleetRow[] }>("/fleet/summary", 30_000);
  const rows = data?.rows ?? [];

  const columns: Column<FleetRow>[] = [
    { key: "n",  header: "Node", cell: (r) => <span className="text-accent-teal">{r.node}</span> },
    { key: "eu", header: "ECC uncorrected", align: "right",
      cell: (r) => (r.sum_ecc_uncorrected ?? 0) > 0
        ? <Badge tone="red">{r.sum_ecc_uncorrected}</Badge>
        : <span className="text-text-muted">0</span> },
    { key: "ec", header: "ECC corrected", align: "right",
      cell: (r) => (r.sum_ecc_corrected ?? 0) > 0
        ? <Badge tone="yellow">{r.sum_ecc_corrected}</Badge>
        : <span className="text-text-muted">0</span> },
    { key: "ru", header: "Uncorrectable remaps", align: "right",
      cell: (r) => (r.sum_uncorrectable_remapped ?? 0) > 0
        ? <Badge tone="red">{r.sum_uncorrectable_remapped}</Badge>
        : <span className="text-text-muted">0</span> },
    { key: "rc", header: "Correctable remaps", align: "right",
      cell: (r) => (r.sum_correctable_remapped ?? 0) > 0
        ? <Badge tone="yellow">{r.sum_correctable_remapped}</Badge>
        : <span className="text-text-muted">0</span> },
    { key: "rf", header: "Remap engine fail",
      cell: (r) => r.any_remap_failure ? <Badge tone="red">yes</Badge> : <Badge tone="green">no</Badge> },
    { key: "err", header: "Probe",
      cell: (r) => r.error ? <span className="text-accent-red">{r.error}</span> : <span className="text-text-muted">ok</span> },
  ];

  return (
    <div className="p-4 h-full">
      <Card
        title="ECC + row-remap (fleet)"
        className="h-full"
        actions={<Button size="sm" variant="ghost" icon={<RefreshCw size={14} strokeWidth={2.25} />} onClick={reload} loading={loading}>reload</Button>}
      >
        {error && <ErrorBox message={error} />}
        {!error && rows.length === 0 && !loading && (
          <EmptyState message="No nodes registered." />
        )}
        {!error && rows.length > 0 && (
          <Table columns={columns} rows={rows} rowKey={(r) => r.node} />
        )}
      </Card>
    </div>
  );
}

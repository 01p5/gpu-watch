import { useState } from "react";
import { Plus, Trash2, RefreshCw } from "lucide-react";
import { Badge, Button, Card, EmptyState, ErrorBox, Table, type Column } from "../components/Atoms";
import { api, type NodeSummary } from "../api";
import { useNodes } from "../NodeContext";

export function HostsPage() {
  const { nodes, reload, loading, error } = useNodes();
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);

  const columns: Column<NodeSummary>[] = [
    { key: "name", header: "Name", cell: (n) => n.name },
    { key: "host", header: "Host", cell: (n) => `${n.user}@${n.host}:${n.port}` },
    { key: "jump", header: "Jump", cell: (n) => n.jump_host || <span className="text-text-muted">—</span> },
    { key: "dcgm", header: "DCGM exporter",
      cell: (n) => n.dcgm_url ? <span className="text-accent-teal">{n.dcgm_url}</span> : <span className="text-text-muted">—</span> },
    { key: "key",  header: "Key",
      cell: (n) => n.key_present ? <Badge tone="green">present</Badge> : <Badge tone="red">missing</Badge> },
    {
      key: "act", header: "", align: "right",
      cell: (n) => (
        <Button size="sm" variant="ghost" icon={<Trash2 size={14} strokeWidth={2.25} />} onClick={() => setDeleting(n.name)}>remove</Button>
      ),
    },
  ];

  return (
    <div className="p-4 h-full">
      <Card
        title="Registered GPU nodes"
        className="h-full"
        actions={
          <>
            <Button size="sm" variant="ghost" icon={<RefreshCw size={14} strokeWidth={2.25} />} onClick={reload} loading={loading}>reload</Button>
            <Button size="sm" variant="primary" icon={<Plus size={14} strokeWidth={2.25} />} onClick={() => setAdding(true)}>add node</Button>
          </>
        }
      >
        {error && <ErrorBox message={error} />}
        {nodes.length === 0 && !loading ? (
          <EmptyState message="No nodes yet. Click 'add node' to register one." />
        ) : (
          <Table columns={columns} rows={nodes} rowKey={(n) => n.name} />
        )}
      </Card>

      {adding && (
        <AddNodeModal
          onClose={() => setAdding(false)}
          onSaved={() => { setAdding(false); reload(); }}
        />
      )}

      {deleting && (
        <ConfirmRemove
          name={deleting}
          onCancel={() => setDeleting(null)}
          onConfirm={async () => {
            await api.delete(`/nodes/${encodeURIComponent(deleting)}`);
            setDeleting(null); reload();
          }}
        />
      )}
    </div>
  );
}

function ConfirmRemove({ name, onCancel, onConfirm }: {
  name: string; onCancel: () => void; onConfirm: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-md bg-dark-secondary border border-border-subtle rounded-md">
        <header className="px-4 py-3 border-b border-border-subtle font-display text-sm font-semibold">
          Remove node?
        </header>
        <div className="px-4 py-3 text-sm text-text-secondary">
          Removes <code className="text-text-primary">{name}</code> from the registry. The node itself isn't touched.
        </div>
        <footer className="px-4 py-3 border-t border-border-subtle flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant="danger" loading={busy} onClick={async () => {
            setBusy(true);
            try { await onConfirm(); } finally { setBusy(false); }
          }}>Remove</Button>
        </footer>
      </div>
    </div>
  );
}

function AddNodeModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: "", host: "", user: "", key_path: "", port: 22, jump_host: "", dcgm_url: "",
  });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const field = (k: keyof typeof form, label: string, hint?: string, type: string = "text") => (
    <label className="block">
      <span className="text-[11px] font-mono uppercase tracking-[0.5px] text-text-secondary">{label}</span>
      <input
        type={type}
        value={String(form[k] ?? "")}
        onChange={(e) => setForm({ ...form, [k]: type === "number" ? Number(e.target.value) : e.target.value })}
        className="mt-1 w-full bg-dark-tertiary border border-border-subtle rounded-sm px-2 py-1.5 font-mono text-xs"
      />
      {hint && <span className="block text-[10px] text-text-muted mt-1">{hint}</span>}
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 backdrop-blur-sm">
      <div className="w-full max-w-lg bg-dark-secondary border border-border-subtle rounded-md">
        <header className="px-4 py-3 border-b border-border-subtle font-display text-sm font-semibold">Add GPU node</header>
        <div className="px-4 py-3 grid grid-cols-2 gap-3">
          {field("name", "Name", "Used as label + MCP prefix")}
          {field("host", "Host", "DNS name or IP of the GPU node")}
          {field("user", "User", "SSH username")}
          {field("port", "Port", undefined, "number")}
          <div className="col-span-2">{field("key_path", "Private key path", "Absolute path on this machine")}</div>
          <div className="col-span-2">{field("jump_host", "Jump host (optional)", "user@bastion[:port]")}</div>
          <div className="col-span-2">{field("dcgm_url", "dcgm-exporter URL (optional)", "e.g. http://node:9400 — leave blank for SSH-only")}</div>
        </div>
        {err && <div className="px-4 pb-2"><div className="p-2 bg-accent-red/10 border border-accent-red/30 rounded-sm font-mono text-xs text-accent-red">{err}</div></div>}
        <footer className="px-4 py-3 border-t border-border-subtle flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" loading={busy} onClick={async () => {
            setBusy(true); setErr(null);
            try {
              await api.post("/nodes", {
                ...form,
                jump_host: form.jump_host || null,
                dcgm_url: form.dcgm_url || null,
              });
              onSaved();
            } catch (e) {
              setErr(e instanceof Error ? e.message : String(e));
            } finally { setBusy(false); }
          }}>Save</Button>
        </footer>
      </div>
    </div>
  );
}

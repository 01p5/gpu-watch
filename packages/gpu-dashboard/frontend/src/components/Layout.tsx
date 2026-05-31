import { NavLink, Outlet } from "react-router-dom";
import {
  Plug, Cpu, Server, Cable, Database, Bug, Activity, AlertTriangle, Gauge,
} from "lucide-react";
import clsx from "clsx";
import { useNodes } from "../NodeContext";

const TABS = [
  { to: "/hosts",     label: "Hosts",     icon: Plug },
  { to: "/fleet",     label: "Fleet",     icon: Server },
  { to: "/node",      label: "Node",      icon: Cpu },
  { to: "/nvlink",    label: "NVLink",    icon: Cable },
  { to: "/ecc",       label: "ECC + Remap", icon: Database },
  { to: "/xid",       label: "XID",       icon: Bug },
  { to: "/dcgm",      label: "DCGM",      icon: Gauge },
  { to: "/advisor",   label: "Drain advisor", icon: AlertTriangle },
];

export function Layout() {
  const { nodes, active, setActive } = useNodes();
  return (
    <div className="h-full grid grid-rows-[auto_1fr]">
      <header className="bg-dark-secondary/80 backdrop-blur-xl border-b border-border-subtle">
        <div className="flex items-center h-14 px-5 gap-6">
          <div className="flex items-baseline gap-2">
            <Activity size={18} className="text-accent-teal self-center" />
            <span className="font-display text-base font-bold text-text-primary tracking-tight">gpu-watch</span>
            <span className="text-[10px] uppercase tracking-[1.5px] text-text-muted font-mono">gpu health</span>
          </div>
          <nav className="flex gap-0.5 ml-2 overflow-x-auto">
            {TABS.map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to}
                className={({ isActive }) => clsx(
                  "flex items-center gap-1.5 px-2.5 py-1.5 rounded-sm text-xs whitespace-nowrap transition-colors border border-transparent",
                  isActive ? "bg-dark-panel text-text-primary border-border-subtle"
                           : "text-text-secondary hover:text-text-primary",
                )}
              >
                <Icon size={16} strokeWidth={2.5} />{label}
              </NavLink>
            ))}
          </nav>
          <div className="flex-1" />
          <NodePicker nodes={nodes.map((n) => n.name)} active={active} onPick={setActive} />
        </div>
      </header>
      <main className="min-h-0 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}

function NodePicker({
  nodes, active, onPick,
}: { nodes: string[]; active: string | null; onPick: (n: string) => void }) {
  if (nodes.length === 0) {
    return (
      <span className="text-xs font-mono text-text-muted">
        no nodes — add one on <NavLink to="/hosts" className="text-accent-teal">Hosts</NavLink>
      </span>
    );
  }
  return (
    <label className="flex items-center gap-2 text-xs font-mono text-text-secondary">
      node
      <select
        value={active ?? ""}
        onChange={(e) => onPick(e.target.value)}
        className="bg-dark-panel border border-border-subtle rounded-sm px-2 py-1 text-text-primary focus:outline-none focus:border-border-active"
      >
        {nodes.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
    </label>
  );
}

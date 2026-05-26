import {
  createContext, useContext, useEffect, useState, useCallback,
  type ReactNode,
} from "react";
import { api, type NodeSummary } from "./api";

// The active node is the prefix for every per-node API call. Fleet
// pages ignore it. Stored in localStorage so refresh keeps your spot.

const STORAGE_KEY = "gpu-watch.activeNode";

type Ctx = {
  nodes: NodeSummary[];
  active: string | null;
  setActive: (name: string) => void;
  reload: () => Promise<void>;
  loading: boolean;
  error: string | null;
};

const NodeCtx = createContext<Ctx | null>(null);

export function NodeProvider({ children }: { children: ReactNode }) {
  const [nodes, setNodes] = useState<NodeSummary[]>([]);
  const [active, setActiveState] = useState<string | null>(
    () => localStorage.getItem(STORAGE_KEY),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.get<{ nodes: NodeSummary[] }>("/nodes");
      setNodes(r.nodes);
      if (r.nodes.length > 0 && !r.nodes.find((n) => n.name === active)) {
        setActiveState(r.nodes[0].name);
        localStorage.setItem(STORAGE_KEY, r.nodes[0].name);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [active]);

  const setActive = (name: string) => {
    setActiveState(name);
    localStorage.setItem(STORAGE_KEY, name);
  };

  useEffect(() => { reload(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <NodeCtx.Provider value={{ nodes, active, setActive, reload, loading, error }}>
      {children}
    </NodeCtx.Provider>
  );
}

export function useNodes(): Ctx {
  const c = useContext(NodeCtx);
  if (!c) throw new Error("useNodes must be used inside <NodeProvider>");
  return c;
}

export function useNodePath(): (suffix: string) => string {
  const { active } = useNodes();
  return (suffix) => {
    if (!active) return suffix;
    const s = suffix.startsWith("/") ? suffix : "/" + suffix;
    return `/nodes/${encodeURIComponent(active)}${s}`;
  };
}

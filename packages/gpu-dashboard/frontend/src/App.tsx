import { Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/Layout";
import { EmbedNavSync } from "./components/EmbedNavSync";
import { NodeProvider } from "./NodeContext";
import { HostsPage } from "./pages/HostsPage";
import { FleetPage } from "./pages/FleetPage";
import { NodePage } from "./pages/NodePage";
import { NvlinkPage } from "./pages/NvlinkPage";
import { EccPage } from "./pages/EccPage";
import { XidPage } from "./pages/XidPage";
import { DcgmPage } from "./pages/DcgmPage";
import { AdvisorPage } from "./pages/AdvisorPage";

export default function App() {
  return (
    <NodeProvider>
      <EmbedNavSync />
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/fleet" replace />} />
          <Route path="hosts"   element={<HostsPage />} />
          <Route path="fleet"   element={<FleetPage />} />
          <Route path="node"    element={<NodePage />} />
          <Route path="nvlink"  element={<NvlinkPage />} />
          <Route path="ecc"     element={<EccPage />} />
          <Route path="xid"     element={<XidPage />} />
          <Route path="dcgm"    element={<DcgmPage />} />
          <Route path="advisor" element={<AdvisorPage />} />
          <Route path="*" element={<Navigate to="/fleet" replace />} />
        </Route>
      </Routes>
    </NodeProvider>
  );
}

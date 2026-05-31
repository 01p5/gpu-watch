import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";

// Mirror of slurm-mgr's basename trick — derive from VITE_BASE_PATH so
// standalone build (base="/") keeps routes at /hosts, /fleet, etc.
// and Olympus embed build (base="/gpu/") gets /gpu/hosts, /gpu/fleet.
const ROUTER_BASENAME =
  (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "") || undefined;

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter basename={ROUTER_BASENAME}>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);

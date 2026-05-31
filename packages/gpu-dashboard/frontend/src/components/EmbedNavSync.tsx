import { useEffect } from "react";
import { useLocation } from "react-router-dom";


/**
 * Posts the current pathname to the parent frame on every URL change,
 * so an embedding shell (e.g. Olympus /capabilities/gpu) can mirror
 * the inner path into its own URL bar — keeps a hard refresh on the
 * parent restoring the user's place inside the iframe.
 *
 * Standalone (window.parent === window): no-op.
 *
 * Mirror of slurm-mgr's component of the same name.
 */
export function EmbedNavSync(): null {
  const location = useLocation();
  useEffect(() => {
    if (window.parent === window) return;
    window.parent.postMessage(
      {
        type: "embedded-nav",
        path: window.location.pathname,
      },
      window.location.origin,
    );
  }, [location.pathname, location.search]);
  return null;
}

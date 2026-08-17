"use client";

import { useEffect, useState } from "react";
import { RequireAuth } from "@/components/RequireAuth";
import { InstanceDetail } from "@/components/InstanceDetail";
import { TaskList } from "@/components/TaskList";

// Authenticated direct links (post-roadmap phase 29, ADR 0109) - reads
// `?instance=<id>` directly from `window.location.search` on mount (not
// `useSearchParams()`, same reasoning as user-ui's RequireAuth.tsx: avoids
// a `<Suspense>` boundary requirement under `output: "export"`, ADR 0006).
// `openInstance`/`backToTasks` also drive the "open Vorgang" link surfaced
// from TaskList.tsx without a full page reload.
export default function HomePage() {
  const [openInstanceId, setOpenInstanceId] = useState<string | null>(null);

  useEffect(() => {
    const instanceId = new URLSearchParams(window.location.search).get("instance");
    if (instanceId) setOpenInstanceId(instanceId);
  }, []);

  function openInstance(instanceId: string) {
    setOpenInstanceId(instanceId);
    window.history.replaceState({}, "", `/?instance=${encodeURIComponent(instanceId)}`);
  }

  function backToTasks() {
    setOpenInstanceId(null);
    window.history.replaceState({}, "", "/");
  }

  return (
    <RequireAuth>
      {openInstanceId ? (
        <InstanceDetail instanceId={openInstanceId} onBack={backToTasks} />
      ) : (
        <TaskList onOpenInstance={openInstance} />
      )}
    </RequireAuth>
  );
}

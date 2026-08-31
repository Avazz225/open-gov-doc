"use client";

import { useRouter } from "next/navigation";
import { RequireAuth } from "@/components/RequireAuth";
import { TeamTaskList } from "@/components/TeamTaskList";

// A separate route (not a view-state toggle on the root page like
// InstanceDetail.tsx) - TeamTaskList is its own tab (Shell.tsx), not a
// drill-down from the task inbox. Opening a Vorgang from here reuses the
// existing `?instance=` direct-link scheme (ADR 0109) on the root route
// instead of duplicating InstanceDetail's rendering logic here.
export default function TeamPage() {
  const router = useRouter();

  function openInstance(instanceId: string) {
    router.push(`/?instance=${encodeURIComponent(instanceId)}`);
  }

  return (
    <RequireAuth>
      <TeamTaskList onOpenInstance={openInstance} />
    </RequireAuth>
  );
}

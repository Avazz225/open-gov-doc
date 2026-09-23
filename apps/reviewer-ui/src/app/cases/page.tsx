import { CasesPane } from "@/components/CasesPane";
import { RequireAuth } from "@/components/RequireAuth";

// Own route (Shell.tsx's tab-nav pattern), Post-Roadmap Phase 74 Session 1.
export default function CasesPage() {
  return (
    <RequireAuth>
      <CasesPane />
    </RequireAuth>
  );
}

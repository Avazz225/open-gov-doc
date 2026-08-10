import { PairedInstallationList } from "@/components/PairedInstallationList";
import { RequireAuth } from "@/components/RequireAuth";

export default function PairedInstallationsPage() {
  return (
    <RequireAuth>
      <PairedInstallationList />
    </RequireAuth>
  );
}

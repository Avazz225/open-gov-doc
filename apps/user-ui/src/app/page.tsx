import { DocumentWorkspace } from "@/components/DocumentWorkspace";
import { RequireAuth } from "@/components/RequireAuth";

export default function HomePage() {
  return (
    <RequireAuth>
      <DocumentWorkspace />
    </RequireAuth>
  );
}

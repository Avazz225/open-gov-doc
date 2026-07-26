import { FolderBrowser } from "@/components/FolderBrowser";
import { RequireAuth } from "@/components/RequireAuth";

export default function HomePage() {
  return (
    <RequireAuth>
      <FolderBrowser />
    </RequireAuth>
  );
}

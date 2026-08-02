import { ProcessDefinitionList } from "@/components/ProcessDefinitionList";
import { RequireAuth } from "@/components/RequireAuth";

export default function HomePage() {
  return (
    <RequireAuth>
      <ProcessDefinitionList />
    </RequireAuth>
  );
}

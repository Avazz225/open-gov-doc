import { RequireAuth } from "@/components/RequireAuth";
import { TaskPane } from "@/components/TaskPane";

export default function HomePage() {
  return (
    <RequireAuth>
      <TaskPane />
    </RequireAuth>
  );
}

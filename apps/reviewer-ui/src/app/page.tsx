import { RequireAuth } from "@/components/RequireAuth";
import { TaskList } from "@/components/TaskList";

export default function HomePage() {
  return (
    <RequireAuth>
      <TaskList />
    </RequireAuth>
  );
}

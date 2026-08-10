import { ApprovalList } from "@/components/ApprovalList";
import { RequireAuth } from "@/components/RequireAuth";

export default function ApprovalsPage() {
  return (
    <RequireAuth>
      <ApprovalList />
    </RequireAuth>
  );
}

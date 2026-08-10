import { RequireAuth } from "@/components/RequireAuth";
import { TransferConsole } from "@/components/TransferConsole";

export default function HomePage() {
  return (
    <RequireAuth>
      <TransferConsole />
    </RequireAuth>
  );
}

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { I18nProvider } from "@/i18n";

// Tab-Umschalter Prozessdefinitionen/DMN-Entscheidungstabellen (P14-S4) -
// die beiden Listen-Komponenten selbst sind bereits eigenständig getestet
// (process-definition-list.test.tsx/dmn-definition-list.test.tsx), hier wird
// nur das Umschaltverhalten verifiziert.
vi.mock("@/components/ProcessDefinitionList", () => ({
  ProcessDefinitionList: () => <div data-testid="process-list" />,
}));
vi.mock("@/components/DmnDefinitionList", () => ({
  DmnDefinitionList: () => <div data-testid="dmn-list" />,
}));
vi.mock("@/components/RequireAuth", () => ({
  RequireAuth: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

describe("HomePage", () => {
  it("shows the process list by default and switches to the DMN list on tab click", () => {
    render(
      <I18nProvider>
        <HomePage />
      </I18nProvider>
    );

    expect(screen.getByTestId("process-list")).toBeInTheDocument();
    expect(screen.queryByTestId("dmn-list")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Entscheidungstabellen (DMN)"));

    expect(screen.getByTestId("dmn-list")).toBeInTheDocument();
    expect(screen.queryByTestId("process-list")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Prozessdefinitionen"));

    expect(screen.getByTestId("process-list")).toBeInTheDocument();
    expect(screen.queryByTestId("dmn-list")).not.toBeInTheDocument();
  });
});

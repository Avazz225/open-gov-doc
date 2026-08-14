import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import HomePage from "@/app/page";
import { I18nProvider } from "@/i18n";

// Tab switcher for process definitions/DMN decision tables (P14-S4) -
// the two list components themselves are already tested independently
// (process-definition-list.test.tsx/dmn-definition-list.test.tsx); here
// only the switching behavior is verified.
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

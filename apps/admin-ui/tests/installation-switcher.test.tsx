import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { InstallationSwitcher } from "@/components/InstallationSwitcher";
import { InstallationManager } from "@/components/InstallationManager";
import { I18nProvider } from "@/i18n";
import { InstallationProvider } from "@/lib/installation-context";

function renderWithSwitcher() {
  return render(
    <I18nProvider>
      <InstallationProvider>
        <InstallationSwitcher />
        <InstallationManager />
      </InstallationProvider>
    </I18nProvider>
  );
}

describe("InstallationSwitcher", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("stays hidden while only one installation is configured", () => {
    renderWithSwitcher();
    expect(screen.queryByLabelText("Installation")).not.toBeInTheDocument();
  });

  it("appears once a second installation exists and switches on selection", async () => {
    const user = userEvent.setup();
    renderWithSwitcher();

    await user.type(screen.getByLabelText("Name"), "Zweitinstanz");
    await user.type(screen.getByLabelText("Gateway-Adresse"), "https://zwei.example.org:8009");
    await user.click(screen.getByText("Anlegen"));

    const select = await screen.findByLabelText("Installation");
    await user.selectOptions(select, "Zweitinstanz");

    await waitFor(() =>
      expect((select as HTMLSelectElement).selectedOptions[0].textContent).toBe("Zweitinstanz")
    );
  });
});

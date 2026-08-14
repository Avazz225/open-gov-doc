import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { InstallationManager } from "@/components/InstallationManager";
import { I18nProvider } from "@/i18n";
import { InstallationProvider } from "@/lib/installation-context";

function renderManager() {
  return render(
    <I18nProvider>
      <InstallationProvider>
        <InstallationManager />
      </InstallationProvider>
    </I18nProvider>
  );
}

describe("InstallationManager", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("lists the default installation as active", () => {
    renderManager();

    const row = screen.getByText("Lokal").closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("aktiv")).toBeInTheDocument();
  });

  it("adds a new installation via the form", async () => {
    const user = userEvent.setup();
    renderManager();

    await user.type(screen.getByLabelText("Name"), "Zweigstelle Nord");
    await user.type(screen.getByLabelText("Gateway-Adresse"), "https://nord.example.org:8009");
    fireEvent.submit(screen.getByRole("form", { name: "Installation hinzufügen" }));

    await waitFor(() => expect(screen.getByText("Zweigstelle Nord")).toBeInTheDocument());
    expect(screen.getByText("https://nord.example.org:8009")).toBeInTheDocument();
  });

  it("rejects an invalid gateway URL", async () => {
    const user = userEvent.setup();
    renderManager();

    await user.type(screen.getByLabelText("Name"), "Kaputt");
    const urlInput = screen.getByLabelText("Gateway-Adresse");
    await user.type(urlInput, "nicht-http");
    // input[type=url] blocks the native submit on constraint violation -
    // submit directly against the form element, like the file upload in P4-S2.
    fireEvent.submit(screen.getByRole("form", { name: "Installation hinzufügen" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Bitte eine gültige URL angeben.");
    expect(screen.queryByText("Kaputt")).not.toBeInTheDocument();
  });

  it("switches the active installation and disables deleting the last remaining one", async () => {
    const user = userEvent.setup();
    renderManager();

    await user.type(screen.getByLabelText("Name"), "Zweigstelle Süd");
    await user.type(screen.getByLabelText("Gateway-Adresse"), "https://sued.example.org:8009");
    fireEvent.submit(screen.getByRole("form", { name: "Installation hinzufügen" }));
    await waitFor(() => expect(screen.getByText("Zweigstelle Süd")).toBeInTheDocument());

    const newRow = screen.getByText("Zweigstelle Süd").closest("tr") as HTMLElement;
    await user.click(within(newRow).getByText("Wechseln"));

    await waitFor(() => {
      const activeRow = screen.getByText("Zweigstelle Süd").closest("tr") as HTMLElement;
      expect(within(activeRow).getByText("aktiv")).toBeInTheDocument();
    });

    const oldRow = screen.getByText("Lokal").closest("tr") as HTMLElement;
    await user.click(within(oldRow).getByText("Löschen"));
    await waitFor(() => expect(screen.queryByText("Lokal")).not.toBeInTheDocument());

    const remainingRow = screen.getByText("Zweigstelle Süd").closest("tr") as HTMLElement;
    expect(within(remainingRow).getByText("Löschen")).toBeDisabled();
  });
});

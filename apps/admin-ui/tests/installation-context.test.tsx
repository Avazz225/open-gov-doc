import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { InstallationProvider, useInstallation } from "@/lib/installation-context";

const setGatewayBaseUrlMock = vi.fn();

vi.mock("@/lib/api", () => ({
  setGatewayBaseUrl: (...args: unknown[]) => setGatewayBaseUrlMock(...args),
}));

function Probe() {
  const { installations, activeInstallation, addInstallation, removeInstallation, switchInstallation } =
    useInstallation();
  return (
    <div>
      <span data-testid="active-name">{activeInstallation.name}</span>
      <span data-testid="count">{installations.length}</span>
      <ul>
        {installations.map((installation) => (
          <li key={installation.id}>{installation.name}</li>
        ))}
      </ul>
      <button
        onClick={() => addInstallation({ name: "Zweigstelle", gatewayBaseUrl: "http://second:8009" })}
      >
        add
      </button>
      <button
        onClick={() => {
          const target = installations.find((i) => i.name === "Zweigstelle");
          if (target) switchInstallation(target.id);
        }}
      >
        switch-to-added
      </button>
      <button
        onClick={() => {
          const target = installations.find((i) => i.id === activeInstallation.id);
          if (target) removeInstallation(target.id);
        }}
      >
        remove-active
      </button>
    </div>
  );
}

function renderProbe() {
  return render(
    <InstallationProvider>
      <Probe />
    </InstallationProvider>
  );
}

describe("InstallationProvider", () => {
  beforeEach(() => {
    window.localStorage.clear();
    setGatewayBaseUrlMock.mockReset();
  });

  it("bootstraps a single default installation pointing at the build-time gateway URL", () => {
    renderProbe();
    expect(screen.getByTestId("count").textContent).toBe("1");
    expect(screen.getByTestId("active-name").textContent).toBe("Lokal");
    expect(setGatewayBaseUrlMock).toHaveBeenCalledWith("http://localhost:8009");
  });

  it("adds a new installation without switching to it automatically", async () => {
    renderProbe();

    await act(async () => {
      screen.getByText("add").click();
    });

    expect(screen.getByTestId("count").textContent).toBe("2");
    expect(screen.getByTestId("active-name").textContent).toBe("Lokal");
  });

  it("switches the active installation and updates the gateway base URL", async () => {
    renderProbe();
    await act(async () => {
      screen.getByText("add").click();
    });

    await act(async () => {
      screen.getByText("switch-to-added").click();
    });

    await waitFor(() => expect(screen.getByTestId("active-name").textContent).toBe("Zweigstelle"));
    expect(setGatewayBaseUrlMock).toHaveBeenCalledWith("http://second:8009");
  });

  it("persists installations across remounts via localStorage", async () => {
    const { unmount } = renderProbe();
    await act(async () => {
      screen.getByText("add").click();
    });
    unmount();

    renderProbe();
    expect(screen.getByTestId("count").textContent).toBe("2");
  });

  it("falls back to a remaining installation when the active one is removed", async () => {
    renderProbe();
    await act(async () => {
      screen.getByText("add").click();
    });
    await act(async () => {
      screen.getByText("switch-to-added").click();
    });
    await waitFor(() => expect(screen.getByTestId("active-name").textContent).toBe("Zweigstelle"));

    await act(async () => {
      screen.getByText("remove-active").click();
    });

    await waitFor(() => expect(screen.getByTestId("active-name").textContent).toBe("Lokal"));
    expect(screen.getByTestId("count").textContent).toBe("1");
  });

  it("refuses to remove the last remaining installation", async () => {
    renderProbe();

    await act(async () => {
      screen.getByText("remove-active").click();
    });

    expect(screen.getByTestId("count").textContent).toBe("1");
  });
});

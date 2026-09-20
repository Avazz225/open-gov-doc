import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RequireCapability } from "@/components/RequireCapability";
import { I18nProvider } from "@/i18n";

const replaceMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
}));

let mockPermissions: string[] = [];

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      permissions: mockPermissions,
      isLoading: false,
    }),
  };
});

function renderGated(capability: string | string[]) {
  return render(
    <I18nProvider>
      <RequireCapability capability={capability}>
        <p>secret content</p>
      </RequireCapability>
    </I18nProvider>
  );
}

// Phase 52 Session 1 (user tracking, ADR 0157): the array form added for a
// page whose backend gates are split across two independent capabilities
// with no single capability covering the whole page - "ANY of these"
// semantics, same redirect-to-"/" behavior as the pre-existing single-
// string form.
describe("RequireCapability", () => {
  beforeEach(() => {
    replaceMock.mockReset();
    mockPermissions = [];
  });

  it("renders children when the single required capability is present (existing behavior)", () => {
    mockPermissions = ["admin.user_management"];

    renderGated("admin.user_management");

    expect(screen.getByText("secret content")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects when the single required capability is missing (existing behavior)", () => {
    mockPermissions = [];

    renderGated("admin.user_management");

    expect(replaceMock).toHaveBeenCalledWith("/");
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });

  it("renders children when only the SECOND of two array capabilities is present", () => {
    mockPermissions = ["admin.user_tracking_view"];

    renderGated(["admin.user_tracking", "admin.user_tracking_view"]);

    expect(screen.getByText("secret content")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("renders children when only the FIRST of two array capabilities is present", () => {
    mockPermissions = ["admin.user_tracking"];

    renderGated(["admin.user_tracking", "admin.user_tracking_view"]);

    expect(screen.getByText("secret content")).toBeInTheDocument();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects when NEITHER of two array capabilities is present", () => {
    mockPermissions = ["some.other.permission"];

    renderGated(["admin.user_tracking", "admin.user_tracking_view"]);

    expect(replaceMock).toHaveBeenCalledWith("/");
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
  });
});

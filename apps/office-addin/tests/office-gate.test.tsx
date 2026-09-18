import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { OfficeGate } from "@/components/OfficeGate";
import { useI18n } from "@/i18n";
import { LocaleProvider, useLocale } from "@/lib/locale-context";
import { installOfficeMock } from "./office-mock";

function Probe() {
  const { locale: contextLocale } = useLocale();
  const { locale: i18nLocale } = useI18n();
  return (
    <div>
      <span data-testid="context-locale">{contextLocale}</span>
      <span data-testid="i18n-locale">{i18nLocale}</span>
    </div>
  );
}

function renderGated() {
  return render(
    <LocaleProvider>
      <OfficeGate>
        <Probe />
      </OfficeGate>
    </LocaleProvider>
  );
}

// Host-locale detection (Phase 47 Session 2, ADR 0167): this add-in has no
// in-app switcher, unlike the other apps - it follows Word's own display
// language instead, detected here once Office.onReady() resolves.
describe("OfficeGate host-locale detection", () => {
  beforeEach(() => {
    document.documentElement.lang = "de";
  });

  afterEach(() => {
    document.documentElement.lang = "de";
  });

  it("keeps the default locale when Word reports a German display language", async () => {
    installOfficeMock({ displayLanguage: "de-DE" });

    renderGated();

    await waitFor(() => expect(screen.getByTestId("context-locale").textContent).toBe("de"));
    expect(screen.getByTestId("i18n-locale").textContent).toBe("de");
    expect(document.documentElement.lang).toBe("de");
  });

  it("switches to English when Word reports an English display language", async () => {
    installOfficeMock({ displayLanguage: "en-US" });

    renderGated();

    await waitFor(() => expect(screen.getByTestId("context-locale").textContent).toBe("en"));
    expect(screen.getByTestId("i18n-locale").textContent).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it("falls back to the default locale for a display language with no matching dictionary", async () => {
    installOfficeMock({ displayLanguage: "fr-FR" });

    renderGated();

    await waitFor(() => expect(screen.getByTestId("context-locale").textContent).toBe("de"));
    expect(screen.getByTestId("i18n-locale").textContent).toBe("de");
  });
});

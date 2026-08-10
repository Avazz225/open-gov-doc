import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DmnDesigner, type DmnDesignerHandle } from "@/components/DmnDesigner";

// Wie bpmn-designer.test.tsx: jsdom hat keine echte Canvas-/SVG-Layout-
// Engine (siehe Plan, "Kein Browser in dieser Entwicklungsumgebung") -
// `dmn-js` selbst wird deshalb gemockt, verifiziert wird nur, dass
// `DmnDesigner` es mit den erwarteten Argumenten instanziiert/aufruft, nicht
// das visuelle Rendering (dieses wurde stattdessen per echtem
// Playwright-Browser-Test verifiziert, siehe PROGRESS.md).
const importXMLMock = vi.fn().mockResolvedValue({ warnings: [] });
const saveXMLMock = vi.fn().mockResolvedValue({ xml: "<exported/>" });
const destroyMock = vi.fn();
const modelerConstructorMock = vi.fn();

vi.mock("dmn-js/lib/Modeler", () => ({
  default: class MockModeler {
    constructor(options: unknown) {
      modelerConstructorMock(options);
    }
    importXML = importXMLMock;
    saveXML = saveXMLMock;
    destroy = destroyMock;
  },
}));

const STARTER_XML = "<definitions />";

describe("DmnDesigner", () => {
  beforeEach(() => {
    modelerConstructorMock.mockClear();
    importXMLMock.mockClear();
    importXMLMock.mockResolvedValue({ warnings: [] });
    saveXMLMock.mockClear();
    destroyMock.mockClear();
  });

  it("instantiates the modeler and imports the initial XML", async () => {
    render(<DmnDesigner initialXml={STARTER_XML} />);

    expect(modelerConstructorMock).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(importXMLMock).toHaveBeenCalledWith(STARTER_XML));
  });

  it("reports an import error via onImportError when importXML rejects", async () => {
    importXMLMock.mockRejectedValueOnce(new Error("kaputte Datei"));
    const onImportError = vi.fn();

    render(<DmnDesigner initialXml={STARTER_XML} onImportError={onImportError} />);

    await waitFor(() => expect(onImportError).toHaveBeenCalledWith("kaputte Datei"));
  });

  it("exposes exportXml/importXml via onReady", async () => {
    let handle: DmnDesignerHandle | null = null;
    render(
      <DmnDesigner
        initialXml={STARTER_XML}
        onReady={(h) => {
          handle = h;
        }}
      />
    );

    await waitFor(() => expect(handle).not.toBeNull());

    await act(async () => {
      const xml = await handle!.exportXml();
      expect(xml).toBe("<exported/>");
    });
    expect(saveXMLMock).toHaveBeenCalledWith({ format: true });

    await act(async () => {
      await handle!.importXml("<new/>");
    });
    expect(importXMLMock).toHaveBeenCalledWith("<new/>");
  });

  it("destroys the modeler on unmount", async () => {
    const { unmount } = render(<DmnDesigner initialXml={STARTER_XML} />);
    await waitFor(() => expect(modelerConstructorMock).toHaveBeenCalledTimes(1));

    unmount();

    expect(destroyMock).toHaveBeenCalledTimes(1);
  });
});

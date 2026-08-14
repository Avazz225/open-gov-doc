import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BpmnDesigner, type BpmnDesignerHandle } from "@/components/BpmnDesigner";

// jsdom has no real canvas/SVG layout engine (see plan, "No browser
// in this development environment") - `bpmn-js` itself is therefore mocked;
// only that `BpmnDesigner` instantiates/calls it with the expected arguments
// is verified, not the visual rendering.
const importXMLMock = vi.fn().mockResolvedValue({ warnings: [] });
const saveXMLMock = vi.fn().mockResolvedValue({ xml: "<exported/>" });
const destroyMock = vi.fn();
const modelerConstructorMock = vi.fn();

vi.mock("bpmn-js/lib/Modeler", () => ({
  default: class MockModeler {
    constructor(options: unknown) {
      modelerConstructorMock(options);
    }
    importXML = importXMLMock;
    saveXML = saveXMLMock;
    destroy = destroyMock;
  },
}));

vi.mock("bpmn-js-properties-panel", () => ({
  BpmnPropertiesPanelModule: { __mock: "BpmnPropertiesPanelModule" },
  BpmnPropertiesProviderModule: { __mock: "BpmnPropertiesProviderModule" },
  CamundaPlatformPropertiesProviderModule: { __mock: "CamundaPlatformPropertiesProviderModule" },
  useService: vi.fn(),
}));

vi.mock("@bpmn-io/properties-panel", () => ({
  CheckboxEntry: vi.fn(),
  Group: vi.fn(),
  SelectEntry: vi.fn(),
  TextFieldEntry: vi.fn(),
  isCheckboxEntryEdited: vi.fn(),
  isSelectEntryEdited: vi.fn(),
  isTextFieldEntryEdited: vi.fn(),
}));

const STARTER_XML = "<bpmn:definitions />";

describe("BpmnDesigner", () => {
  beforeEach(() => {
    modelerConstructorMock.mockClear();
    importXMLMock.mockClear();
    importXMLMock.mockResolvedValue({ warnings: [] });
    saveXMLMock.mockClear();
    destroyMock.mockClear();
  });

  it("instantiates the modeler with the properties panel modules and imports the initial XML", async () => {
    render(<BpmnDesigner initialXml={STARTER_XML} federationInstallations={[]} />);

    expect(modelerConstructorMock).toHaveBeenCalledTimes(1);
    const options = modelerConstructorMock.mock.calls[0][0] as {
      additionalModules: unknown[];
    };
    // 4 previous modules + FederatedStepPropertiesProviderModule + the
    // didi inline binding for the static installation list (P6-S9).
    expect(options.additionalModules).toHaveLength(6);

    await waitFor(() => expect(importXMLMock).toHaveBeenCalledWith(STARTER_XML));
  });

  it("reports an import error via onImportError when importXML rejects", async () => {
    importXMLMock.mockRejectedValueOnce(new Error("kaputte Datei"));
    const onImportError = vi.fn();

    render(
      <BpmnDesigner
        initialXml={STARTER_XML}
        federationInstallations={[]}
        onImportError={onImportError}
      />
    );

    await waitFor(() => expect(onImportError).toHaveBeenCalledWith("kaputte Datei"));
  });

  it("exposes exportXml/importXml via onReady", async () => {
    let handle: BpmnDesignerHandle | null = null;
    render(
      <BpmnDesigner
        initialXml={STARTER_XML}
        federationInstallations={[]}
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
    const { unmount } = render(
      <BpmnDesigner initialXml={STARTER_XML} federationInstallations={[]} />
    );
    await waitFor(() => expect(modelerConstructorMock).toHaveBeenCalledTimes(1));

    unmount();

    expect(destroyMock).toHaveBeenCalledTimes(1);
  });
});

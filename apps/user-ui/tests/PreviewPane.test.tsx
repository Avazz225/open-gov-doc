import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PreviewPane } from "@/components/PreviewPane";
import { I18nProvider } from "@/i18n";
import type { DocumentSummary, DocumentVersion, RenditionSummary } from "@/lib/api";
import { ApiError } from "@/lib/api";

const listDocumentVersionsMock = vi.fn();
const listRenditionsMock = vi.fn();
const downloadRenditionContentMock = vi.fn();
const downloadDocumentVersionMock = vi.fn();
const listOcrResultsMock = vi.fn();
const downloadOcrPageImageMock = vi.fn();
const exportDocumentMock = vi.fn();

vi.mock("@/lib/api", () => ({
  listDocumentVersions: (...args: unknown[]) => listDocumentVersionsMock(...args),
  listRenditions: (...args: unknown[]) => listRenditionsMock(...args),
  downloadRenditionContent: (...args: unknown[]) => downloadRenditionContentMock(...args),
  downloadDocumentVersion: (...args: unknown[]) => downloadDocumentVersionMock(...args),
  exportDocument: (...args: unknown[]) => exportDocumentMock(...args),
  listOcrResults: (...args: unknown[]) => listOcrResultsMock(...args),
  downloadOcrPageImage: (...args: unknown[]) => downloadOcrPageImageMock(...args),
  officeLaunchInfo: vi.fn().mockReturnValue(null),
  createWebdavEditToken: vi.fn(),
  officeLaunchUrl: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

vi.mock("@/lib/auth-context", async () => {
  const actual = await vi.importActual<typeof import("@/lib/auth-context")>("@/lib/auth-context");
  return {
    ...actual,
    useAuth: () => ({
      user: { sub: "u1", username: "alice", email: null, realm_roles: [] },
      accessToken: "token-123",
      isLoading: false,
      login: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

function renderPreview(document: DocumentSummary) {
  return render(
    <I18nProvider>
      <PreviewPane document={document} />
    </I18nProvider>
  );
}

function makeDocument(overrides: Partial<DocumentSummary> = {}): DocumentSummary {
  return {
    id: "d1",
    title: "dokument",
    folder_id: "root",
    object_type_id: null,
    attributes: {},
    current_version_number: 1,
    deleted_at: null,
    deleted_by: null,
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    retention_until: null,
    full_deletion: false,
    pending_deletion_reason: null,
    ...overrides,
  };
}

function makeVersion(overrides: Partial<DocumentVersion> = {}): DocumentVersion {
  return {
    version_number: 1,
    filename: "dokument",
    content_type: "application/pdf",
    size_bytes: 1,
    checksum_sha256: "x",
    is_conflict: false,
    based_on_version_number: null,
    comment: null,
    created_by: "alice",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeRendition(overrides: Partial<RenditionSummary> = {}): RenditionSummary {
  return {
    id: "rendition-1",
    document_id: "d1",
    version_number: 1,
    rendition_type: "pdf_archive",
    source_filename: "dokument",
    source_content_type: null,
    target_filename: "dokument_archiv.pdf",
    target_content_type: "application/pdf",
    size_bytes: 1,
    status: "ready",
    error_message: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("PreviewPane - native Vorschau statt Ersatzdarstellung", () => {
  beforeEach(() => {
    listDocumentVersionsMock.mockReset();
    listRenditionsMock.mockReset();
    listRenditionsMock.mockResolvedValue([]);
    downloadRenditionContentMock.mockReset();
    downloadDocumentVersionMock.mockReset();
    exportDocumentMock.mockReset();
    listOcrResultsMock.mockReset();
    listOcrResultsMock.mockResolvedValue([]);
    downloadOcrPageImageMock.mockReset();
    URL.createObjectURL = vi.fn(() => "blob:mock-url");
    URL.revokeObjectURL = vi.fn();
  });

  it("rendert text/html gesandboxt statt im Plain-Text-Zweig", async () => {
    const doc = makeDocument({ title: "seite.html" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "text/html" })]);
    downloadDocumentVersionMock.mockResolvedValue(
      new Blob(["<script>alert(1)</script><p>Hallo</p>"], { type: "text/html" })
    );

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: seite.html");
    const iframe = await within(previewPane).findByTitle("seite.html");
    expect(iframe.tagName).toBe("IFRAME");
    expect(iframe).toHaveAttribute("sandbox", "");
    expect(iframe).toHaveAttribute("srcdoc", "<script>alert(1)</script><p>Hallo</p>");
    expect(previewPane.querySelector("pre.preview-text")).not.toBeInTheDocument();
    expect(listRenditionsMock).not.toHaveBeenCalled();
  });

  it("zeigt ein natives PDF-Embed über die Rohbytes, wenn kein OCR-Ergebnis existiert (bisher: keine Vorschau)", async () => {
    const doc = makeDocument({ title: "vertrag.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.pdf");
    const embed = await within(previewPane).findByTitle("vertrag.pdf");
    expect(embed.tagName).toBe("EMBED");
    expect(embed).toHaveAttribute("type", "application/pdf");
    expect(embed).toHaveAttribute("src", "blob:mock-url");
    expect(downloadDocumentVersionMock).toHaveBeenCalledWith("token-123", "d1", 1);
  });

  it("zeigt das OCR-Seitenbild mit Wort-Overlay nur noch als Fallback für echte Scans (engine tesseract)", async () => {
    const doc = makeDocument({ title: "scan.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    listOcrResultsMock.mockResolvedValue([
      {
        id: "ocr-1",
        document_id: "d1",
        version_number: 1,
        status: "ready",
        engine: "tesseract",
        average_confidence: 92.0,
        full_text: "Hallo",
        pages: [{ page_number: 1, width: 200, height: 100, words: [] }],
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadOcrPageImageMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: scan.pdf");
    await within(previewPane).findByRole("img");
    expect(previewPane.querySelector("embed")).not.toBeInTheDocument();
    expect(downloadDocumentVersionMock).not.toHaveBeenCalled();
  });

  it("zeigt für ein PDF mit bereits eingebettetem Textlayer (engine native_text_layer) die native Ansicht statt des Overlays", async () => {
    const doc = makeDocument({ title: "textlayer.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    listOcrResultsMock.mockResolvedValue([
      {
        id: "ocr-2",
        document_id: "d1",
        version_number: 1,
        status: "ready",
        engine: "native_text_layer",
        average_confidence: 100.0,
        full_text: "Hallo",
        pages: [{ page_number: 1, width: 200, height: 100, words: [] }],
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: textlayer.pdf");
    const embed = await within(previewPane).findByTitle("textlayer.pdf");
    expect(embed.tagName).toBe("EMBED");
    expect(downloadOcrPageImageMock).not.toHaveBeenCalled();
  });

  it("zeigt für ein Bilddokument mit OCR-Ergebnis zusätzlich einen separaten Textextrakt, ohne das Overlay zu verändern", async () => {
    const doc = makeDocument({ title: "foto.jpg" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "image/jpeg" })]);
    listOcrResultsMock.mockResolvedValue([
      {
        id: "ocr-3",
        document_id: "d1",
        version_number: 1,
        status: "ready",
        engine: "tesseract",
        average_confidence: 91.0,
        full_text: "Erkannter Bildtext",
        pages: [{ page_number: 1, width: 300, height: 200, words: [] }],
        error_message: null,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      },
    ]);
    downloadOcrPageImageMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: foto.jpg");
    await within(previewPane).findByRole("img");
    expect(within(previewPane).getByText("Erkannter Text (OCR)")).toBeInTheDocument();
    expect(within(previewPane).getByText("Erkannter Bildtext")).toBeInTheDocument();
    expect(downloadDocumentVersionMock).not.toHaveBeenCalled();
  });

  it("zeigt ein Bilddokument in voller Auflösung über die Rohbytes, nicht über eine Thumbnail-Rendition", async () => {
    const doc = makeDocument({ title: "foto.jpg" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "image/jpeg" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["fake-jpeg"], { type: "image/jpeg" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: foto.jpg");
    const image = await within(previewPane).findByRole("img");
    expect(image).toHaveAttribute("src", "blob:mock-url");
    expect(downloadDocumentVersionMock).toHaveBeenCalledWith("token-123", "d1", 1);
    expect(downloadRenditionContentMock).not.toHaveBeenCalled();
  });

  it("zeigt für ein DOCX mit fertiger pdf_archive-Rendition die formatierte PDF-Ansicht statt der Text-Extraktion", async () => {
    const doc = makeDocument({ title: "vertrag.docx" });
    listDocumentVersionsMock.mockResolvedValue([
      makeVersion({
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    ]);
    listRenditionsMock.mockResolvedValue([
      makeRendition({ id: "pdf-archive-1", rendition_type: "pdf_archive" }),
      makeRendition({
        id: "substitute-1",
        rendition_type: "substitute_text",
        target_content_type: "text/plain; charset=utf-8",
      }),
    ]);
    downloadRenditionContentMock.mockResolvedValue(
      new Blob(["%PDF-1.4"], { type: "application/pdf" })
    );

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.docx");
    const embed = await within(previewPane).findByTitle("vertrag.docx");
    expect(embed.tagName).toBe("EMBED");
    expect(downloadRenditionContentMock).toHaveBeenCalledWith("token-123", "pdf-archive-1");
    expect(previewPane.querySelector("pre.preview-text")).not.toBeInTheDocument();
  });

  it("fällt für ein DOCX ohne fertige pdf_archive-Rendition weiterhin auf substitute_text zurück", async () => {
    const doc = makeDocument({ title: "vertrag.docx" });
    listDocumentVersionsMock.mockResolvedValue([
      makeVersion({
        content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      }),
    ]);
    listRenditionsMock.mockResolvedValue([
      makeRendition({ id: "pdf-archive-1", rendition_type: "pdf_archive", status: "failed" }),
      makeRendition({
        id: "substitute-1",
        rendition_type: "substitute_text",
        target_content_type: "text/plain; charset=utf-8",
      }),
    ]);
    downloadRenditionContentMock.mockResolvedValue(
      new Blob(["Vertragsinhalt als Text"], { type: "text/plain" })
    );

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.docx");
    expect(await within(previewPane).findByText("Vertragsinhalt als Text")).toBeInTheDocument();
    expect(downloadRenditionContentMock).toHaveBeenCalledWith("token-123", "substitute-1");
  });

  it("zeigt bei einem fehlgeschlagenen Download (409, ausgesondert) eine Fehlermeldung mit Rückholungs-Hinweis statt stillschweigend nichts zu tun", async () => {
    const doc = makeDocument({ title: "archiviert.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock
      .mockResolvedValueOnce(new Blob(["%PDF-1.4"], { type: "application/pdf" }))
      .mockRejectedValueOnce(new ApiError(409, "Dokumentinhalt wurde ausgesondert"));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: archiviert.pdf");
    await within(previewPane).findByTitle("archiviert.pdf");
    expect(within(previewPane).queryByRole("alert")).not.toBeInTheDocument();

    await user.click(within(previewPane).getByText("Herunterladen"));

    expect(
      await within(previewPane).findByText(
        "Dieser Inhalt wurde archiviert und ausgesondert. Bitte zuerst über die Rückholung (Archiv) anfordern."
      )
    ).toBeInTheDocument();
  });

  it("zeigt bei einem generischen Download-Fehler die generische Fallback-Meldung", async () => {
    const doc = makeDocument({ title: "kaputt.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock
      .mockResolvedValueOnce(new Blob(["%PDF-1.4"], { type: "application/pdf" }))
      .mockRejectedValueOnce(new Error("Netzwerkfehler"));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: kaputt.pdf");
    await within(previewPane).findByTitle("kaputt.pdf");

    await user.click(within(previewPane).getByText("Herunterladen"));

    expect(
      await within(previewPane).findByText("Download fehlgeschlagen. Bitte später erneut versuchen.")
    ).toBeInTheDocument();
  });

  it("exportiert die aktuelle Version ohne Reihenfolge-Override, wenn kein Wert gewählt wurde (Phase 28)", async () => {
    const doc = makeDocument({ title: "vertrag.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
    exportDocumentMock.mockResolvedValue(new Blob(["%PDF-1.4 export"], { type: "application/pdf" }));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.pdf");
    await within(previewPane).findByTitle("vertrag.pdf");

    await user.click(within(previewPane).getByText("Exportieren"));

    expect(exportDocumentMock).toHaveBeenCalledWith("token-123", "d1", undefined);
  });

  it("exportiert mit der gewählten Reihenfolge, wenn eine explizit ausgewählt wurde (Phase 28)", async () => {
    const doc = makeDocument({ title: "vertrag.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
    exportDocumentMock.mockResolvedValue(new Blob(["%PDF-1.4 export"], { type: "application/pdf" }));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.pdf");
    await within(previewPane).findByTitle("vertrag.pdf");

    await user.selectOptions(
      within(previewPane).getByLabelText("Exporthistorie"),
      "before"
    );
    await user.click(within(previewPane).getByText("Exportieren"));

    expect(exportDocumentMock).toHaveBeenCalledWith("token-123", "d1", "before");
  });

  it("zeigt bei einem fehlgeschlagenen Export eine Fehlermeldung (Phase 28)", async () => {
    const doc = makeDocument({ title: "vertrag.pdf" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
    exportDocumentMock.mockRejectedValue(new Error("Serverfehler"));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.pdf");
    await within(previewPane).findByTitle("vertrag.pdf");

    await user.click(within(previewPane).getByText("Exportieren"));

    expect(
      await within(previewPane).findByText("Export fehlgeschlagen. Bitte später erneut versuchen.")
    ).toBeInTheDocument();
  });

  it("zeigt für eine Konfliktversion ein Badge und für einen Kommentar Text mit Tooltip (P23-S1)", async () => {
    const doc = makeDocument({ title: "vertrag.pdf", current_version_number: 2 });
    listDocumentVersionsMock.mockResolvedValue([
      makeVersion({ version_number: 1, comment: "Erste Fassung" }),
      makeVersion({ version_number: 2, is_conflict: true, comment: "Konflikt beim Check-in" }),
    ]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));

    const user = userEvent.setup();
    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: vertrag.pdf");
    await within(previewPane).findByTitle("vertrag.pdf");

    expect(within(previewPane).getByText("Konflikt")).toBeInTheDocument();
    expect(within(previewPane).getByText("Kommentar: Konflikt beim Check-in")).toBeInTheDocument();

    const select = within(previewPane).getByLabelText("Version auswählen");
    const conflictOption = within(select).getByText(/Version 2/) as HTMLOptionElement;
    expect(conflictOption).toHaveAttribute("title", "Konflikt beim Check-in");

    await user.selectOptions(select, "1");

    expect(within(previewPane).queryByText("Konflikt")).not.toBeInTheDocument();
    expect(within(previewPane).getByText("Kommentar: Erste Fassung")).toBeInTheDocument();
  });

  it("pollt eine noch nicht fertige pdf_archive-Rendition und zeigt die formatierte Ansicht, sobald sie bereit ist (P23-S3)", async () => {
    vi.useFakeTimers();
    try {
      const doc = makeDocument({ title: "vertrag.docx" });
      listDocumentVersionsMock.mockResolvedValue([
        makeVersion({
          content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }),
      ]);
      // First poll cycle: rendering-service has not yet created a rendition row
      // (processing still in progress) - only the second one delivers it ready.
      listRenditionsMock
        .mockResolvedValueOnce([])
        .mockResolvedValue([
          makeRendition({ id: "pdf-archive-1", rendition_type: "pdf_archive" }),
        ]);
      downloadRenditionContentMock.mockResolvedValue(
        new Blob(["%PDF-1.4"], { type: "application/pdf" })
      );

      renderPreview(doc);
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);

      const previewPane = screen.getByLabelText("Vorschau: vertrag.docx");
      expect(listRenditionsMock).toHaveBeenCalledTimes(1);

      await vi.advanceTimersByTimeAsync(7_000);
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);

      expect(listRenditionsMock).toHaveBeenCalledTimes(2);
      const embed = previewPane.querySelector("embed");
      expect(embed).toHaveAttribute("title", "vertrag.docx");
      expect(downloadRenditionContentMock).toHaveBeenCalledWith("token-123", "pdf-archive-1");
    } finally {
      vi.useRealTimers();
    }
  });

  it("pollt im Hintergrund auf ein noch nicht fertiges OCR-Ergebnis, ohne die bereits sichtbare native PDF-Ansicht zu unterbrechen (P23-S3)", async () => {
    vi.useFakeTimers();
    try {
      const doc = makeDocument({ title: "scan.pdf" });
      listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "application/pdf" })]);
      downloadDocumentVersionMock.mockResolvedValue(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
      listOcrResultsMock.mockResolvedValueOnce([]).mockResolvedValue([
        {
          id: "ocr-1",
          document_id: "d1",
          version_number: 1,
          status: "ready",
          engine: "tesseract",
          average_confidence: 92.0,
          full_text: "Hallo",
          pages: [{ page_number: 1, width: 200, height: 100, words: [] }],
          error_message: null,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        },
      ]);
      downloadOcrPageImageMock.mockResolvedValue(new Blob(["fake-png"], { type: "image/png" }));

      renderPreview(doc);
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);

      const previewPane = screen.getByLabelText("Vorschau: scan.pdf");
      // Native PDF embed appears immediately, without waiting for OCR.
      const embed = previewPane.querySelector("embed");
      expect(embed).toHaveAttribute("title", "scan.pdf");
      expect(downloadOcrPageImageMock).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(7_000);
      for (let i = 0; i < 10; i++) await vi.advanceTimersByTimeAsync(0);

      // OCR has since completed (engine "tesseract") - the preview now
      // correctly switches from the native PDF embed to the
      // page-image-with-word-overlay (same behavior as on initial
      // load with an already-completed OCR result).
      expect(downloadOcrPageImageMock).toHaveBeenCalled();
      expect(within(previewPane).getByRole("img")).toBeInTheDocument();
      expect(previewPane.querySelector("embed")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("zeigt text/plain unverändert als Plain-Text-Vorschau", async () => {
    const doc = makeDocument({ title: "notiz.txt" });
    listDocumentVersionsMock.mockResolvedValue([makeVersion({ content_type: "text/plain" })]);
    downloadDocumentVersionMock.mockResolvedValue(new Blob(["Hallo Welt"], { type: "text/plain" }));

    renderPreview(doc);

    const previewPane = screen.getByLabelText("Vorschau: notiz.txt");
    expect(await within(previewPane).findByText("Hallo Welt")).toBeInTheDocument();
    expect(previewPane.querySelector("pre.preview-text")).toBeInTheDocument();
    expect(listRenditionsMock).not.toHaveBeenCalled();
  });
});

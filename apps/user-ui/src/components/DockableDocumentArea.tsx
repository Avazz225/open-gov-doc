"use client";

import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  DockviewApi,
  DockviewReact,
  DockviewReadyEvent,
  type DockviewTheme,
  type IDockviewPanelProps,
} from "dockview-react";
import "dockview-react/dist/styles/dockview.css";
import { useI18n } from "@/i18n";
import type { DocumentSummary, Folder } from "@/lib/api";
import { ExplorerPane, type BreadcrumbEntry } from "./ExplorerPane";
import { MetadataPanel } from "./MetadataPanel";
import { PreviewPane } from "./PreviewPane";

// Dockbarer Arbeitsbereich (Konzept 8, P16-S1, Bibliothekswahl siehe ADR
// 0057/P16-S0) - ersetzt das feste, per Hand gebaute Splitter-Layout für
// Explorer/Metadaten/Vorschau durch ein echtes VS-Code-artiges Docking
// (`dockview-react`): Panels lassen sich frei verschieben/stapeln/abspalten,
// mehrere Dokumente sind gleichzeitig sicht- und anordenbar. Neue
// Standardanordnung: Explorer als eigenständiges Panel links, rechts die
// Dokumenttabs (jetzt echte dockview-Tabs statt der früheren handgebauten
// Tableiste in `ExplorerPane`) über der Vorschau, darunter die Metadaten des
// gerade aktiven Dokuments.
//
// State-Architektur: welche Dokumente offen sind (`openDocuments`) bleibt in
// `DocumentWorkspace` (überlebt ein Aus-/Einblenden dieser Komponente beim
// Wechsel der IconRail-Sonderbereiche, siehe dort). Alles, was NUR das
// dockview-Layout selbst betrifft (welches Panel gerade aktiv ist, die
// `DockviewApi`-Instanz) bleibt hier lokal. Panel-Inhalte lesen häufig
// wechselnde Daten (Ordnerinhalt, offene Dokumente, aktives Dokument) über
// einen React-Context statt über dockviews `params` - `params` eignet sich
// nur für zur Panel-Erzeugungszeit feste Werte (hier: `documentId`), ein
// Aktualisieren wechselnder Werte über `params` würde für jede Änderung
// `panel.api.updateParameters()` erfordern, während der Context automatisch
// mit jedem Render aktuell bleibt.
const LAYOUT_STORAGE_KEY = "dms.workspace.dockLayout";
const EMPTY_PANEL_ID = "preview-empty";
const EXPLORER_PANEL_ID = "explorer";
const METADATA_PANEL_ID = "metadata";
const DOC_PANEL_PREFIX = "doc:";

export interface DockableDocumentAreaHandle {
  openDocument: (doc: DocumentSummary) => void;
  resetLayout: () => void;
}

interface WorkspaceContextValue {
  trail: BreadcrumbEntry[];
  folders: Folder[];
  documents: DocumentSummary[];
  isLoading: boolean;
  error: string | null;
  onOpenFolder: (folder: Folder) => void;
  onNavigateToFolder: (path: Folder[]) => void;
  onBreadcrumbClick: (index: number) => void;
  onOpenDocument: (doc: DocumentSummary) => void;
  onCreateFolder: (name: string, objectTypeId?: number) => Promise<boolean>;
  onRenameFolder: (folderId: string, name: string) => Promise<boolean>;
  onMoveFolder: (folderId: string, newParentId: string) => Promise<boolean>;
  onDeleteFolder: (folderId: string) => Promise<"trashed" | "pending_approval" | false>;
  onDeleteDocument: (documentId: string) => Promise<"trashed" | "pending_approval" | false>;
  token: string;
  createdBy: string;
  currentFolderId: string;
  onUploaded: () => void;
  openDocumentsById: Map<string, DocumentSummary>;
  activeDocument: DocumentSummary | null;
  onMetadataSaved: (updated: DocumentSummary) => void;
  versionBumps: Record<string, number>;
  onDocumentVersionBump: (documentId: string) => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function useWorkspaceContext(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspaceContext muss innerhalb von DockableDocumentArea verwendet werden");
  return ctx;
}

function ExplorerPanelContent() {
  const ctx = useWorkspaceContext();
  return (
    <ExplorerPane
      trail={ctx.trail}
      folders={ctx.folders}
      documents={ctx.documents}
      isLoading={ctx.isLoading}
      error={ctx.error}
      onOpenFolder={ctx.onOpenFolder}
      onNavigateToFolder={ctx.onNavigateToFolder}
      onBreadcrumbClick={ctx.onBreadcrumbClick}
      onOpenDocument={ctx.onOpenDocument}
      onCreateFolder={ctx.onCreateFolder}
      onRenameFolder={ctx.onRenameFolder}
      onMoveFolder={ctx.onMoveFolder}
      onDeleteFolder={ctx.onDeleteFolder}
      onDeleteDocument={ctx.onDeleteDocument}
      token={ctx.token}
      createdBy={ctx.createdBy}
      currentFolderId={ctx.currentFolderId}
      onUploaded={ctx.onUploaded}
    />
  );
}

function MetadataPanelContent() {
  const ctx = useWorkspaceContext();
  return (
    <MetadataPanel
      document={ctx.activeDocument}
      onSaved={ctx.onMetadataSaved}
      onSigned={ctx.onDocumentVersionBump}
    />
  );
}

function PreviewEmptyContent() {
  return <PreviewPane document={null} />;
}

function DocumentPreviewContent(props: IDockviewPanelProps<{ documentId: string }>) {
  const ctx = useWorkspaceContext();
  const doc = ctx.openDocumentsById.get(props.params.documentId) ?? null;
  const versionBump = doc ? (ctx.versionBumps[doc.id] ?? 0) : 0;
  return <PreviewPane document={doc} versionBump={versionBump} />;
}

const PANEL_COMPONENTS = {
  explorer: ExplorerPanelContent,
  metadata: MetadataPanelContent,
  previewEmpty: PreviewEmptyContent,
  documentPreview: DocumentPreviewContent,
};

// Ohne eigenes `theme` fällt dockview-core auf sein eingebautes `themeAbyss`-
// Preset zurück, dessen CSS-Klasse dieselben `--dv-*`-Variablen erneut mit
// fest verdrahteten dunklen Werten belegt - das überschreibt die eigentlich
// theme-fähige `--dv-*`→`--dms-*`-Zuordnung in globals.css innerhalb des
// gesamten dockview-Hosts, unabhängig vom aktuellen `data-theme` (Bug: der
// Arbeitsbereich blieb dadurch immer dunkel). Ein Theme-Objekt ohne eigene,
// irgendwo definierte CSS-Klasse setzt keine eigenen `--dv-*`-Werte, sodass
// die bereits vorhandene `:root`-Zuordnung ungehindert durchgereicht wird.
const DMS_DOCKVIEW_THEME: DockviewTheme = {
  name: "dms",
  className: "dms-dockview-theme",
};

export interface DockableDocumentAreaProps {
  hidden: boolean;
  trail: BreadcrumbEntry[];
  folders: Folder[];
  documents: DocumentSummary[];
  isLoading: boolean;
  error: string | null;
  onOpenFolder: (folder: Folder) => void;
  onNavigateToFolder: (path: Folder[]) => void;
  onBreadcrumbClick: (index: number) => void;
  onCreateFolder: (name: string, objectTypeId?: number) => Promise<boolean>;
  onRenameFolder: (folderId: string, name: string) => Promise<boolean>;
  onMoveFolder: (folderId: string, newParentId: string) => Promise<boolean>;
  onDeleteFolder: (folderId: string) => Promise<"trashed" | "pending_approval" | false>;
  onDeleteDocument: (documentId: string) => Promise<"trashed" | "pending_approval" | false>;
  token: string;
  createdBy: string;
  currentFolderId: string;
  onUploaded: () => void;
  openDocuments: DocumentSummary[];
  onOpenDocument: (doc: DocumentSummary) => void;
  onCloseDocument: (documentId: string) => void;
  onMetadataSaved: (updated: DocumentSummary) => void;
  versionBumps: Record<string, number>;
  onDocumentVersionBump: (documentId: string) => void;
}

export const DockableDocumentArea = forwardRef<DockableDocumentAreaHandle, DockableDocumentAreaProps>(
  function DockableDocumentArea(props, ref) {
    const {
      hidden,
      trail,
      folders,
      documents,
      isLoading,
      error,
      onOpenFolder,
      onNavigateToFolder,
      onBreadcrumbClick,
      onCreateFolder,
      onRenameFolder,
      onMoveFolder,
      onDeleteFolder,
      onDeleteDocument,
      token,
      createdBy,
      currentFolderId,
      onUploaded,
      openDocuments,
      onOpenDocument: propsOnOpenDocument,
      onCloseDocument,
      onMetadataSaved: propsOnMetadataSaved,
      versionBumps,
      onDocumentVersionBump,
    } = props;

    const { t } = useI18n();
    const apiRef = useRef<DockviewApi | null>(null);
    const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);

    const openDocumentsById = useMemo(
      () => new Map(openDocuments.map((doc) => [doc.id, doc])),
      [openDocuments]
    );
    const activeDocument = activeDocumentId ? (openDocumentsById.get(activeDocumentId) ?? null) : null;

    const persistLayout = useCallback((api: DockviewApi) => {
      try {
        window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(api.toJSON()));
      } catch {
        // localStorage kann in seltenen Fällen (Privatmodus, Kontingent)
        // fehlschlagen - das Layout bleibt dann nur für die laufende
        // Sitzung erhalten, kein Retry in diesem Grundgerüst.
      }
    }, []);

    // `title: doc.title` bewusst der rohe Dokumenttitel, nicht die
    // Kennzeichen-formatierte Anzeige aus `formatDocumentTitle`
    // (`@/lib/kennzeichen`, siehe `ExplorerPane`) - deren Objekttyp-/
    // Konfigurationsdaten (`documentTypeById`, `kennzeichenShowByDefault`)
    // müssten sonst aus `ExplorerPane` heraus- und hierher hochgehoben
    // werden. Gleiche Vereinfachung wie `PreviewPane`s Überschrift, die
    // ebenfalls schon immer nur den rohen Titel zeigt - bewusste Grenze
    // dieses Grundgerüsts (P16-S1), keine Regression.
    const docPanelOptions = useCallback(
      (doc: DocumentSummary, position?: { referencePanel: string; direction: "right" | "within" }) => ({
        id: `${DOC_PANEL_PREFIX}${doc.id}`,
        component: "documentPreview",
        title: doc.title,
        params: { documentId: doc.id },
        position,
      }),
      []
    );

    // Werksstandard (Konzept 8): Explorer eigenständig links, rechts oben
    // die Dokumenttabs (leerer Platzhalter, falls keine offen sind) über der
    // Vorschau, darunter Metadaten - genau die neue, bei P16-S0 verifizierte
    // Standardanordnung ("Dokumenttabs künftig über der Vorschau statt über
    // dem Explorer"). Baut bereits offene Dokumente DIREKT mit ein, statt
    // erst einen Platzhalter anzulegen und ihn im Anschluss separat zu
    // entfernen: ein `addPanel()` gefolgt von einem SOFORTIGEN `getPanel()`/
    // `removePanel()` auf denselben, gerade erst erzeugten Platzhalter
    // findet dockview-intern zuverlässig NICHT (vermutlich intern verzögerte
    // Registrierung) - live über einen fehlgeschlagenen "Zurücksetzen"-Test
    // gefunden (doppeltes Vorschau-Panel blieb bestehen). Ein einziger,
    // in sich konsistenter Aufbau-Durchlauf umgeht das vollständig.
    const buildDefaultLayout = useCallback(
      (api: DockviewApi, docs: DocumentSummary[]) => {
        api.clear();
        api.addPanel({
          id: EXPLORER_PANEL_ID,
          component: "explorer",
          title: t("workspace.explorerPanelTitle"),
        });
        const [firstDoc, ...restDocs] = docs;
        const previewAnchorId = firstDoc ? `${DOC_PANEL_PREFIX}${firstDoc.id}` : EMPTY_PANEL_ID;
        if (firstDoc) {
          api.addPanel(
            docPanelOptions(firstDoc, { referencePanel: EXPLORER_PANEL_ID, direction: "right" })
          );
          for (const doc of restDocs) {
            api.addPanel(docPanelOptions(doc, { referencePanel: previewAnchorId, direction: "within" }));
          }
        } else {
          api.addPanel({
            id: EMPTY_PANEL_ID,
            component: "previewEmpty",
            title: t("workspace.previewPanelTitle"),
            position: { referencePanel: EXPLORER_PANEL_ID, direction: "right" },
          });
        }
        api.addPanel({
          id: METADATA_PANEL_ID,
          component: "metadata",
          title: t("workspace.metadataPanelTitle"),
          position: { referencePanel: previewAnchorId, direction: "below" },
        });
      },
      [t, docPanelOptions]
    );

    // Fügt EIN weiteres Dokument in eine BEREITS BESTEHENDE, settled Anordnung
    // ein (Explorer/Vorschau/Metadaten existieren schon aus einem früheren
    // Render/Tick) - anders als `buildDefaultLayout` oben ist hier kein
    // Timing-Risiko bekannt, da nichts unmittelbar zuvor selbst erzeugt
    // wurde.
    const addDocumentPanel = useCallback(
      (api: DockviewApi, doc: DocumentSummary) => {
        const panelId = `${DOC_PANEL_PREFIX}${doc.id}`;
        const existing = api.getPanel(panelId);
        if (existing) {
          existing.api.setActive();
          return;
        }
        const emptyPanel = api.getPanel(EMPTY_PANEL_ID);
        const anyDocPanel = api.panels.find((p) => p.id.startsWith(DOC_PANEL_PREFIX));
        const position = emptyPanel
          ? { referencePanel: EMPTY_PANEL_ID, direction: "within" as const }
          : anyDocPanel
            ? { referencePanel: anyDocPanel.id, direction: "within" as const }
            : api.getPanel(EXPLORER_PANEL_ID)
              ? { referencePanel: EXPLORER_PANEL_ID, direction: "right" as const }
              : undefined;
        api.addPanel(docPanelOptions(doc, position));
        if (emptyPanel) api.removePanel(emptyPanel);
      },
      [docPanelOptions]
    );

    const onReady = useCallback(
      (event: DockviewReadyEvent) => {
        const api = event.api;
        apiRef.current = api;

        let restored = false;
        const saved = window.localStorage.getItem(LAYOUT_STORAGE_KEY);
        if (saved) {
          try {
            api.fromJSON(JSON.parse(saved));
            restored = Boolean(api.getPanel(EXPLORER_PANEL_ID));
          } catch {
            restored = false;
          }
        }
        if (!restored) {
          buildDefaultLayout(api, openDocuments);
        } else {
          // Bereits offene Dokumente, die im gespeicherten Layout fehlen
          // (z. B. weil die Sitzung sie nach dem letzten Speichern noch
          // geöffnet hat), einzeln nachträglich einhängen.
          openDocuments.forEach((doc) => {
            if (!api.getPanel(`${DOC_PANEL_PREFIX}${doc.id}`)) addDocumentPanel(api, doc);
          });
        }

        // Nur EIN Panel ist jemals global "aktiv" (dockview-Konzept über die
        // gesamte Gridview hinweg, nicht je Gruppe) - ein Klick in die
        // Metadaten- oder Explorer-Gruppe macht DIESE zum aktiven Panel und
        // würde `activeDocumentId` sonst fälschlich auf `null` zurücksetzen
        // (die Metadaten-Anzeige würde sich selbst leeren, sobald man
        // hineinklickt, um z. B. ein Feld zu bearbeiten). Deshalb nur bei
        // einem Wechsel AUF ein Dokument-Panel aktualisieren; ein Wechsel
        // AUF Explorer/Metadaten/Vorschau-Platzhalter lässt das zuletzt
        // aktive Dokument unangetastet.
        api.onDidActivePanelChange((changeEvent) => {
          const panel = changeEvent.panel;
          if (panel && panel.id.startsWith(DOC_PANEL_PREFIX)) {
            setActiveDocumentId(panel.id.slice(DOC_PANEL_PREFIX.length));
          }
        });

        api.onDidRemovePanel((panel) => {
          if (!panel.id.startsWith(DOC_PANEL_PREFIX)) return;
          const documentId = panel.id.slice(DOC_PANEL_PREFIX.length);
          onCloseDocument(documentId);
          setActiveDocumentId((current) => (current === documentId ? null : current));
          const anyDocPanelLeft = api.panels.some((p) => p.id.startsWith(DOC_PANEL_PREFIX));
          if (!anyDocPanelLeft && !api.getPanel(EMPTY_PANEL_ID)) {
            const reference = api.getPanel(EXPLORER_PANEL_ID);
            api.addPanel({
              id: EMPTY_PANEL_ID,
              component: "previewEmpty",
              title: t("workspace.previewPanelTitle"),
              position: reference ? { referencePanel: EXPLORER_PANEL_ID, direction: "right" } : undefined,
            });
          }
        });

        api.onDidLayoutChange(() => persistLayout(api));
      },
      // `openDocuments` bewusst nicht in den Deps: `onReady` wird von
      // dockview nur EINMAL beim Erststart aufgerufen (kein Re-Init bei
      // Prop-Änderungen), der Nachhol-Schritt oben braucht ohnehin nur den
      // zum Mount-Zeitpunkt gültigen Bestand. `onCloseDocument` ist über
      // `useCallback` mit funktionalem `setState` in `DocumentWorkspace`
      // stabil, damit der spätere `onDidRemovePanel`-Listener nie eine
      // veraltete Fassung aufruft.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [buildDefaultLayout, addDocumentPanel, persistLayout, t, onCloseDocument]
    );

    const openDocument = useCallback(
      (doc: DocumentSummary) => {
        propsOnOpenDocument(doc);
        const api = apiRef.current;
        if (!api) return;
        addDocumentPanel(api, doc);
      },
      [addDocumentPanel, propsOnOpenDocument]
    );

    const resetLayout = useCallback(() => {
      const api = apiRef.current;
      if (!api) return;
      buildDefaultLayout(api, openDocuments);
      persistLayout(api);
    }, [buildDefaultLayout, persistLayout, openDocuments]);

    useImperativeHandle(ref, () => ({ openDocument, resetLayout }), [openDocument, resetLayout]);

    const handleMetadataSaved = useCallback(
      (updated: DocumentSummary) => {
        const panel = apiRef.current?.getPanel(`${DOC_PANEL_PREFIX}${updated.id}`);
        panel?.api.setTitle(updated.title);
        propsOnMetadataSaved(updated);
      },
      [propsOnMetadataSaved]
    );

    const contextValue: WorkspaceContextValue = {
      trail,
      folders,
      documents,
      isLoading,
      error,
      onOpenFolder,
      onNavigateToFolder,
      onBreadcrumbClick,
      onOpenDocument: openDocument,
      onCreateFolder,
      onRenameFolder,
      onMoveFolder,
      onDeleteFolder,
      onDeleteDocument,
      token,
      createdBy,
      currentFolderId,
      onUploaded,
      openDocumentsById,
      activeDocument,
      onMetadataSaved: handleMetadataSaved,
      versionBumps,
      onDocumentVersionBump,
    };

    return (
      <div className="dockable-document-area" style={{ display: hidden ? "none" : "flex" }}>
        <div className="dockable-document-area-toolbar">
          <button type="button" onClick={resetLayout} title={t("workspace.resetLayoutHint")}>
            {t("workspace.resetLayout")}
          </button>
        </div>
        <WorkspaceContext.Provider value={contextValue}>
          <div className="dockable-document-area-host">
            <DockviewReact
              components={PANEL_COMPONENTS}
              onReady={onReady}
              theme={DMS_DOCKVIEW_THEME}
            />
          </div>
        </WorkspaceContext.Provider>
      </div>
    );
  }
);

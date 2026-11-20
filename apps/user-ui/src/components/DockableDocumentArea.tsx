"use client";

import {
  createContext,
  forwardRef,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
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
import { X } from "lucide-react";
import { useI18n } from "@/i18n";
import { getKennzeichenConfig, listObjectTypes, type DocumentSummary, type Folder, type ObjectType } from "@/lib/api";
import { formatDocumentTitle } from "@/lib/kennzeichen";
import { useNarrow } from "@/lib/use-narrow";
import { ExplorerPane, type BreadcrumbEntry } from "./ExplorerPane";
import { MetadataPanel } from "./MetadataPanel";
import { PreviewPane } from "./PreviewPane";

// Dockable workspace (concept 8, P16-S1, library choice see ADR
// 0057/P16-S0) - replaces the fixed, hand-built splitter layout for
// explorer/metadata/preview with a real VS-Code-style docking system
// (`dockview-react`): panels can be freely moved/stacked/split off,
// multiple documents are visible and arrangeable at the same time. New
// default arrangement: explorer as a standalone panel on the left, on the
// right the document tabs (now real dockview tabs instead of the earlier
// hand-built tab bar in `ExplorerPane`) above the preview, below that the
// metadata of the currently active document.
//
// State architecture: which documents are open (`openDocuments`) stays in
// `DocumentWorkspace` (survives this component being hidden/shown when
// switching IconRail special areas, see there). Everything that concerns
// ONLY the dockview layout itself (which panel is currently active, the
// `DockviewApi` instance) stays local here. Panel content reads frequently
// changing data (folder content, open documents, active document) via a
// React context instead of via dockview's `params` - `params` is only
// suited for values fixed at panel creation time (here: `documentId`),
// updating changing values via `params` would require
// `panel.api.updateParameters()` for every change, while the context
// automatically stays current with every render.
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
  favoritesUser: string;
  currentFolderId: string;
  onUploaded: () => void;
  openDocumentsById: Map<string, DocumentSummary>;
  activeDocument: DocumentSummary | null;
  onMetadataSaved: (updated: DocumentSummary) => void;
  versionBumps: Record<string, number>;
  onDocumentVersionBump: (documentId: string) => void;
  // True while the single-pane mode is shown (P83-S13): the hidden dockview
  // panels then render nothing, so explorer and preview don't load twice.
  dockSuppressed: boolean;
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
      favoritesUser={ctx.favoritesUser}
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
  return <PreviewPane document={doc} versionBump={versionBump} onDocumentCreated={ctx.onUploaded} />;
}

// The dockview copies of the panels render nothing while the single-pane
// mode is active (P83-S13) - dockview itself stays mounted underneath.
function ExplorerDockPanel() {
  return useWorkspaceContext().dockSuppressed ? null : <ExplorerPanelContent />;
}
function MetadataDockPanel() {
  return useWorkspaceContext().dockSuppressed ? null : <MetadataPanelContent />;
}
function PreviewEmptyDockPanel() {
  return useWorkspaceContext().dockSuppressed ? null : <PreviewEmptyContent />;
}
function DocumentPreviewDockPanel(props: IDockviewPanelProps<{ documentId: string }>) {
  return useWorkspaceContext().dockSuppressed ? null : <DocumentPreviewContent {...props} />;
}

const PANEL_COMPONENTS = {
  explorer: ExplorerDockPanel,
  metadata: MetadataDockPanel,
  previewEmpty: PreviewEmptyDockPanel,
  documentPreview: DocumentPreviewDockPanel,
};

type SinglePaneView = "explorer" | "preview" | "metadata";

// Single-pane mode below 768px (P83-S13, ADR 0238 Decision 5): dockview's
// three panes squeezed into 390px left the explorer ~170px wide. Instead one
// pane at a time, chosen with a segmented switcher; opening a document jumps
// to Vorschau, and several open documents get a scrolling strip of chips
// above the preview. The switcher is the way back to Explorer.
function SinglePaneWorkspace({
  view,
  onViewChange,
  activeDocument,
  onSelectDocument,
  onCloseDocument,
  titleOf,
}: {
  view: SinglePaneView;
  onViewChange: (view: SinglePaneView) => void;
  activeDocument: DocumentSummary | null;
  onSelectDocument: (documentId: string) => void;
  onCloseDocument: (documentId: string) => void;
  titleOf: (doc: DocumentSummary) => string;
}) {
  const ctx = useWorkspaceContext();
  const { t } = useI18n();
  const docs = Array.from(ctx.openDocumentsById.values());
  const views: { id: SinglePaneView; label: string }[] = [
    { id: "explorer", label: t("workspace.explorerPanelTitle") },
    {
      id: "preview",
      label: docs.length > 0 ? `${t("workspace.previewPanelTitle")} (${docs.length})` : t("workspace.previewPanelTitle"),
    },
    { id: "metadata", label: t("workspace.metadataPanelTitle") },
  ];
  const versionBump = activeDocument ? (ctx.versionBumps[activeDocument.id] ?? 0) : 0;

  return (
    <div className="single-pane-workspace">
      <div className="view-mode-toggle single-pane-switcher" role="group" aria-label={t("workspace.singlePaneLabel")}>
        {views.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className={view === entry.id ? "view-mode-active" : undefined}
            aria-pressed={view === entry.id}
            onClick={() => onViewChange(entry.id)}
          >
            {entry.label}
          </button>
        ))}
      </div>
      <div className="single-pane-body">
        {view === "explorer" && <ExplorerPanelContent />}
        {view === "preview" && (
          <>
            {docs.length > 1 && (
              <div className="dms-scroll-x dms-tab-strip single-pane-docs" role="group" aria-label={t("workspace.openDocumentsLabel")}>
                {docs.map((doc) => (
                  <span key={doc.id} className={`single-pane-doc${doc.id === activeDocument?.id ? " is-active" : ""}`}>
                    <button
                      type="button"
                      aria-pressed={doc.id === activeDocument?.id}
                      onClick={() => onSelectDocument(doc.id)}
                    >
                      {titleOf(doc)}
                    </button>
                    <button
                      type="button"
                      aria-label={t("workspace.closeDocument", { title: titleOf(doc) })}
                      onClick={() => onCloseDocument(doc.id)}
                    >
                      <X size={14} strokeWidth={1.75} aria-hidden="true" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <PreviewPane document={activeDocument} versionBump={versionBump} onDocumentCreated={ctx.onUploaded} />
          </>
        )}
        {view === "metadata" && <MetadataPanelContent />}
      </div>
    </div>
  );
}

// Without its own `theme`, dockview-core falls back to its built-in
// `themeAbyss` preset, whose CSS class assigns the same `--dv-*` variables
// fixed dark values again - this overrides the actually theme-capable
// `--dv-*`→`--dms-*` mapping in globals.css within the entire dockview
// host, regardless of the current `data-theme` (bug: the workspace
// therefore always stayed dark). A theme object without its own CSS class
// defined anywhere sets no `--dv-*` values of its own, so the
// already-existing `:root` mapping is passed through unhindered.
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
  favoritesUser: string;
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
      favoritesUser,
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

    // Single-pane mode below 768px (P83-S13). While it is shown, dockview
    // stays mounted but hidden and is not touched at all: documents opened or
    // closed meanwhile only change `openDocuments`, and layout changes are
    // not persisted (a hidden dockview would save a collapsed layout). When
    // the window widens again, the saved layout is re-applied and brought in
    // line with the open documents (`restoreLayout`).
    const narrow = useNarrow();
    const narrowRef = useRef(false);
    const [singlePaneView, setSinglePaneView] = useState<SinglePaneView>("explorer");
    // Panels removed by `restoreLayout` itself must not close their document.
    const suppressCloseRef = useRef(false);
    const openDocumentsRef = useRef(openDocuments);
    useEffect(() => {
      openDocumentsRef.current = openDocuments;
    }, [openDocuments]);

    const openDocumentsById = useMemo(
      () => new Map(openDocuments.map((doc) => [doc.id, doc])),
      [openDocuments]
    );
    const activeDocument = activeDocumentId
      ? (openDocumentsById.get(activeDocumentId) ?? null)
      : narrow
        ? (openDocuments[openDocuments.length - 1] ?? null)
        : null;

    // Reference-number display (2.2/8, P5e-S3) for document tab titles -
    // own copy of the same load-once fetch `ExplorerPane` already does for
    // its own list/tree titles (`documentTypeById`/`kennzeichenShowByDefault`
    // aren't lifted out of `ExplorerPane`, so this component needs its own).
    // Closes the tab-title inconsistency noted in P42-S3's research: tabs
    // previously always showed the raw title, unlike the explorer list/tree.
    const [documentTypeById, setDocumentTypeById] = useState<Record<number, ObjectType>>({});
    const [kennzeichenShowByDefault, setKennzeichenShowByDefault] = useState(true);
    useEffect(() => {
      if (!token) return;
      listObjectTypes(token, "document")
        .then((types) => setDocumentTypeById(Object.fromEntries(types.map((ot) => [ot.id, ot]))))
        .catch(() => {});
      getKennzeichenConfig(token)
        .then((config) => setKennzeichenShowByDefault(config.show_before_filename))
        .catch(() => {});
    }, [token]);

    const persistLayout = useCallback((api: DockviewApi) => {
      if (narrowRef.current) return;
      try {
        window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(api.toJSON()));
      } catch {
        // localStorage can fail in rare cases (private mode, quota) -
        // the layout is then only kept for the current
        // session, no retry in this scaffold.
      }
    }, []);

    // Tab title formatted exactly like the explorer list/tree (Post-Roadmap
    // Phase 42 Session 3) - `PreviewPane`'s own heading is a separate,
    // still-unaddressed instance of the same raw-title simplification, out
    // of scope for this fix.
    const docPanelOptions = useCallback(
      (doc: DocumentSummary, position?: { referencePanel: string; direction: "right" | "within" }) => ({
        id: `${DOC_PANEL_PREFIX}${doc.id}`,
        component: "documentPreview",
        title: formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault),
        params: { documentId: doc.id },
        position,
      }),
      [documentTypeById, kennzeichenShowByDefault]
    );

    // Factory default (concept 8): explorer standalone on the left, top
    // right the document tabs (empty placeholder if none are open) above
    // the preview, below that metadata - exactly the new default arrangement
    // verified in P16-S0 ("document tabs going forward above the preview
    // instead of above the explorer"). Includes already-open documents
    // DIRECTLY, instead of first creating a placeholder and then removing it
    // separately afterward: an `addPanel()` followed by an IMMEDIATE
    // `getPanel()`/`removePanel()` on the same, just-created placeholder
    // reliably does NOT work internally in dockview (presumably internally
    // delayed registration) - found live via a failing "reset" test
    // (duplicate preview panel remained). A single, internally consistent
    // build pass avoids this entirely.
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

    // Adds ONE additional document into an ALREADY EXISTING, settled
    // arrangement (explorer/preview/metadata already exist from an earlier
    // render/tick) - unlike `buildDefaultLayout` above, no timing risk is
    // known here, since nothing was just created immediately before.
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

    // Applies the saved layout (or the factory default) and reconciles it
    // with the documents that are actually open: on first mount, and again
    // when the window widens out of the single-pane mode (P83-S13), where
    // documents may have been opened or closed while dockview sat hidden.
    const restoreLayout = useCallback(
      (api: DockviewApi, docs: DocumentSummary[]) => {
        suppressCloseRef.current = true;
        try {
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
            buildDefaultLayout(api, docs);
            return;
          }
          // Panels of documents no longer open go; already-open documents
          // missing from the saved layout (e.g. opened after the last save)
          // are mounted one by one.
          const openIds = new Set(docs.map((doc) => doc.id));
          api.panels
            .filter((p) => p.id.startsWith(DOC_PANEL_PREFIX) && !openIds.has(p.id.slice(DOC_PANEL_PREFIX.length)))
            .forEach((p) => api.removePanel(p));
          docs.forEach((doc) => {
            if (!api.getPanel(`${DOC_PANEL_PREFIX}${doc.id}`)) addDocumentPanel(api, doc);
          });
        } finally {
          suppressCloseRef.current = false;
        }
      },
      [buildDefaultLayout, addDocumentPanel]
    );

    const onReady = useCallback(
      (event: DockviewReadyEvent) => {
        const api = event.api;
        apiRef.current = api;

        restoreLayout(api, openDocuments);

        // Only ONE panel is ever globally "active" (dockview concept across
        // the entire gridview, not per group) - a click into the metadata or
        // explorer group makes THAT the active panel and would otherwise
        // wrongly reset `activeDocumentId` to `null` (the metadata display
        // would clear itself as soon as you click into it, e.g. to edit a
        // field). Therefore only update on a switch TO a document panel; a
        // switch TO the explorer/metadata/preview placeholder leaves the
        // last active document untouched.
        api.onDidActivePanelChange((changeEvent) => {
          const panel = changeEvent.panel;
          if (panel && panel.id.startsWith(DOC_PANEL_PREFIX)) {
            setActiveDocumentId(panel.id.slice(DOC_PANEL_PREFIX.length));
          }
        });

        api.onDidRemovePanel((panel) => {
          if (!panel.id.startsWith(DOC_PANEL_PREFIX)) return;
          const documentId = panel.id.slice(DOC_PANEL_PREFIX.length);
          if (!suppressCloseRef.current) onCloseDocument(documentId);
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
      // `openDocuments` deliberately not in the deps: `onReady` is called by
      // dockview only ONCE on initial startup (no re-init on prop changes),
      // the catch-up step above only needs the set valid at mount time
      // anyway. `onCloseDocument` is kept stable via `useCallback` with
      // functional `setState` in `DocumentWorkspace`, so the later
      // `onDidRemovePanel` listener never calls a stale version.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [restoreLayout, persistLayout, t, onCloseDocument]
    );

    // Entering/leaving the single-pane mode. The ref is set in a layout
    // effect so it is in place before dockview's resize to 0 can try to
    // persist; leaving re-applies the saved layout.
    useLayoutEffect(() => {
      const wasNarrow = narrowRef.current;
      narrowRef.current = narrow;
      const api = apiRef.current;
      if (wasNarrow && !narrow && api) restoreLayout(api, openDocumentsRef.current);
    }, [narrow, restoreLayout]);

    const openDocument = useCallback(
      (doc: DocumentSummary) => {
        propsOnOpenDocument(doc);
        if (narrowRef.current) {
          setActiveDocumentId(doc.id);
          setSinglePaneView("preview");
          return;
        }
        const api = apiRef.current;
        if (!api) return;
        addDocumentPanel(api, doc);
      },
      [addDocumentPanel, propsOnOpenDocument]
    );

    // Closing from the single-pane chips: only the open-documents state
    // changes; dockview catches up when the window widens again.
    const closeDocumentNarrow = useCallback(
      (documentId: string) => {
        onCloseDocument(documentId);
        setActiveDocumentId((current) => (current === documentId ? null : current));
      },
      [onCloseDocument]
    );

    const resetLayout = useCallback(() => {
      const api = apiRef.current;
      if (!api) return;
      buildDefaultLayout(api, openDocuments);
      persistLayout(api);
    }, [buildDefaultLayout, persistLayout, openDocuments]);

    useImperativeHandle(ref, () => ({ openDocument, resetLayout }), [openDocument, resetLayout]);

    // Refreshes already-created panels' titles once the reference-number
    // config (fetched asynchronously above) becomes available - panels built
    // by `onReady`/`buildDefaultLayout` before that point were necessarily
    // created with the not-yet-loaded defaults (empty override map, global
    // default `true`), a one-tick lag `ExplorerPane` doesn't have (its titles
    // are plain JSX, re-rendered every time, not an imperative dockview
    // `setTitle` call).
    useEffect(() => {
      const api = apiRef.current;
      if (!api) return;
      openDocuments.forEach((doc) => {
        const panel = api.getPanel(`${DOC_PANEL_PREFIX}${doc.id}`);
        panel?.api.setTitle(formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault));
      });
    }, [documentTypeById, kennzeichenShowByDefault, openDocuments]);

    const handleMetadataSaved = useCallback(
      (updated: DocumentSummary) => {
        const panel = apiRef.current?.getPanel(`${DOC_PANEL_PREFIX}${updated.id}`);
        panel?.api.setTitle(formatDocumentTitle(updated, documentTypeById, kennzeichenShowByDefault));
        propsOnMetadataSaved(updated);
      },
      [propsOnMetadataSaved, documentTypeById, kennzeichenShowByDefault]
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
      favoritesUser,
      currentFolderId,
      onUploaded,
      openDocumentsById,
      activeDocument,
      onMetadataSaved: handleMetadataSaved,
      versionBumps,
      onDocumentVersionBump,
      dockSuppressed: narrow,
    };

    const titleOf = (doc: DocumentSummary) => formatDocumentTitle(doc, documentTypeById, kennzeichenShowByDefault);

    return (
      <div className="dockable-document-area" style={{ display: hidden ? "none" : "flex" }}>
        {/* "Standardanordnung" has nothing to arrange in the single-pane mode */}
        {!narrow && (
          <div className="dockable-document-area-toolbar">
            <button type="button" className="toggle-btn" onClick={resetLayout} title={t("workspace.resetLayoutHint")}>
              {t("workspace.resetLayout")}
            </button>
          </div>
        )}
        <WorkspaceContext.Provider value={contextValue}>
          {narrow && (
            <SinglePaneWorkspace
              view={singlePaneView}
              onViewChange={setSinglePaneView}
              activeDocument={activeDocument}
              onSelectDocument={setActiveDocumentId}
              onCloseDocument={closeDocumentNarrow}
              titleOf={titleOf}
            />
          )}
          <div className="dockable-document-area-host" style={narrow ? { display: "none" } : undefined}>
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

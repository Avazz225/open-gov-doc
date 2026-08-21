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
  return <PreviewPane document={doc} versionBump={versionBump} onDocumentCreated={ctx.onUploaded} />;
}

const PANEL_COMPONENTS = {
  explorer: ExplorerPanelContent,
  metadata: MetadataPanelContent,
  previewEmpty: PreviewEmptyContent,
  documentPreview: DocumentPreviewContent,
};

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
        // localStorage can fail in rare cases (private mode, quota) -
        // the layout is then only kept for the current
        // session, no retry in this scaffold.
      }
    }, []);

    // `title: doc.title` deliberately the raw document title, not the
    // reference-number-formatted display from `formatDocumentTitle`
    // (`@/lib/kennzeichen`, see `ExplorerPane`) - its object-type/
    // configuration data (`documentTypeById`, `kennzeichenShowByDefault`)
    // would otherwise have to be lifted out of `ExplorerPane` and up to here.
    // Same simplification as `PreviewPane`'s heading, which has also always
    // shown only the raw title - a deliberate limitation of this scaffold
    // (P16-S1), not a regression.
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
          // Retroactively mount, one by one, already-open documents that are
          // missing from the saved layout (e.g. because the session opened
          // them after the last save).
          openDocuments.forEach((doc) => {
            if (!api.getPanel(`${DOC_PANEL_PREFIX}${doc.id}`)) addDocumentPanel(api, doc);
          });
        }

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
      // `openDocuments` deliberately not in the deps: `onReady` is called by
      // dockview only ONCE on initial startup (no re-init on prop changes),
      // the catch-up step above only needs the set valid at mount time
      // anyway. `onCloseDocument` is kept stable via `useCallback` with
      // functional `setState` in `DocumentWorkspace`, so the later
      // `onDidRemovePanel` listener never calls a stale version.
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

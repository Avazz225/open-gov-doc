# 0015 — Tree view: breadcrumb path reconstructed client-side from the already-expanded tree, no new backend endpoint

**Status:** accepted
**Context:** Concept 8 (Explorer tree view), Session P5b-S4

## Decision

The User UI Explorer's new tree view (`FolderTree.tsx`) navigates, on a click on any folder node — however deeply nested — directly to that folder, including the correct breadcrumb path in `DocumentWorkspace`'s `trail` state. Rather than building a new Folder Service endpoint for a folder's full path for this purpose (an open point since P3-S3, see `docs/services/folder-service.md`), `FolderTree` reconstructs the path **client-side from data already loaded**: since a node in the tree is only visible/clickable after all its ancestors have been expanded, the component already knows, at the moment of the click, the complete chain of ancestor `Folder` objects (each of these arrived as an entry from the `listChildFolders()` call of its respective parent node). A new callback `onNavigateToFolder(path: Folder[])` replaces the entire `trail` in `DocumentWorkspace` (instead of merely extending it by one level, as the existing `onOpenFolder` does).

## Rationale

- **No additional backend change needed.** The Folder Service deliberately has no "full path" endpoint (see `docs/services/folder-service.md`, "Open Points") — only `GET /folders/{id}/children` exists. A tree view that expands recursively from the root anyway already has the path information available as a byproduct of its own navigation; querying it again from the backend would be unnecessary duplication.
- **No additional network roundtrip on click.** Since the path is assembled from `Folder` objects already held in memory, navigation is immediately available without waiting for a further request.
- **Only works because the tree view expands from the root** — a hypothetical "jump" to a folder never yet made visible in the tree (e.g. via a free-text folder ID) would not be possible with this approach. That is currently not a limitation, since no such input path exists (no folder-ID address bar, no deep link) — should such a need arise later, it would require the path endpoint left open in `folder-service.md`.
- **`onNavigateToFolder` replaces the trail instead of extending it** — unlike `onOpenFolder` (list view, which always goes exactly one level deeper than the currently shown folder), a tree click can land on any node (sibling, ancestor, deeply nested child); simply appending to the existing trail would be wrong in most cases.

## Consequences

- `ExplorerPane`/`DocumentWorkspace` gain a new prop/function `onNavigateToFolder`, independent of the existing `onOpenFolder` (list view) — both ultimately lead to the same `trail` state, differing only in how the new trail is built (append vs. replace).
- The tree view loads a node's children **lazily, only on expansion** (`listChildFolders`/`listDocumentsInFolder` per node) — a subtree never expanded causes no requests, while the path for a click on an already-visible node is guaranteed to assemble correctly.
- Should a deep link/direct folder-ID addressing become necessary in the future (not part of this session), that would be the moment to actually build the previously deferred "full path" endpoint in the Folder Service — this decision does not stand in the way of that, since it is purely client-side.

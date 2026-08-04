import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FavoritesPane } from "@/components/FavoritesPane";
import { I18nProvider } from "@/i18n";

const listFavoritesMock = vi.fn();
const getDocumentMock = vi.fn();
const getFolderMock = vi.fn();
const removeFavoriteMock = vi.fn();

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listFavorites: (...args: unknown[]) => listFavoritesMock(...args),
    getDocument: (...args: unknown[]) => getDocumentMock(...args),
    getFolder: (...args: unknown[]) => getFolderMock(...args),
    removeFavorite: (...args: unknown[]) => removeFavoriteMock(...args),
  };
});

function renderPane(onOpenDocument = vi.fn(), onOpenFolder = vi.fn()) {
  return render(
    <I18nProvider>
      <FavoritesPane
        token="token-123"
        currentUsername="alice"
        onOpenDocument={onOpenDocument}
        onOpenFolder={onOpenFolder}
      />
    </I18nProvider>
  );
}

describe("FavoritesPane", () => {
  beforeEach(() => {
    listFavoritesMock.mockReset();
    getDocumentMock.mockReset();
    getFolderMock.mockReset();
    removeFavoriteMock.mockReset();
  });

  it("shows an empty-state message when there are no favorites", async () => {
    listFavoritesMock.mockResolvedValue([]);

    renderPane();

    expect(await screen.findByText("Noch keine Favoriten.")).toBeInTheDocument();
    expect(listFavoritesMock).toHaveBeenCalledWith("token-123", "alice");
  });

  it("resolves and lists favorited documents and folders", async () => {
    listFavoritesMock.mockResolvedValue([
      {
        id: "fav-1",
        user_id: "alice",
        object_type: "document",
        object_id: "doc-1",
        created_at: "2026-01-01T00:00:00Z",
      },
      {
        id: "fav-2",
        user_id: "alice",
        object_type: "folder",
        object_id: "folder-1",
        created_at: "2026-01-02T00:00:00Z",
      },
    ]);
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung.pdf" });
    getFolderMock.mockResolvedValue({ id: "folder-1", name: "Verträge" });

    renderPane();

    expect(await screen.findByText(/Rechnung.pdf/)).toBeInTheDocument();
    expect(screen.getByText(/Verträge/)).toBeInTheDocument();
  });

  it("shows a placeholder and still allows removal when a favorite 404s", async () => {
    listFavoritesMock.mockResolvedValue([
      {
        id: "fav-1",
        user_id: "alice",
        object_type: "document",
        object_id: "doc-gone",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    getDocumentMock.mockRejectedValue(new Error("404"));
    removeFavoriteMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPane();

    expect(await screen.findByText(/nicht mehr verfügbar/)).toBeInTheDocument();

    await user.click(screen.getByText("Entfernen"));

    await waitFor(() =>
      expect(removeFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "document",
        object_id: "doc-gone",
      })
    );
  });

  it("opens a favorited document via the callback", async () => {
    const onOpenDocument = vi.fn();
    listFavoritesMock.mockResolvedValue([
      {
        id: "fav-1",
        user_id: "alice",
        object_type: "document",
        object_id: "doc-1",
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    getDocumentMock.mockResolvedValue({ id: "doc-1", title: "Rechnung.pdf" });

    const user = userEvent.setup();
    renderPane(onOpenDocument);

    await screen.findByText(/Rechnung.pdf/);
    await user.click(screen.getByText("Öffnen"));

    expect(onOpenDocument).toHaveBeenCalledWith({ id: "doc-1", title: "Rechnung.pdf" });
  });

  it("removes a favorite and reloads the list", async () => {
    listFavoritesMock
      .mockResolvedValueOnce([
        {
          id: "fav-1",
          user_id: "alice",
          object_type: "folder",
          object_id: "folder-1",
          created_at: "2026-01-01T00:00:00Z",
        },
      ])
      .mockResolvedValueOnce([]);
    getFolderMock.mockResolvedValue({ id: "folder-1", name: "Verträge" });
    removeFavoriteMock.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderPane();

    await screen.findByText(/Verträge/);
    await user.click(screen.getByText("Entfernen"));

    await waitFor(() =>
      expect(removeFavoriteMock).toHaveBeenCalledWith("token-123", {
        user_id: "alice",
        object_type: "folder",
        object_id: "folder-1",
      })
    );
    expect(await screen.findByText("Noch keine Favoriten.")).toBeInTheDocument();
  });
});

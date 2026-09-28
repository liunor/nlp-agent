import { useCallback, useEffect, useRef, useState } from "react";

import { CaptureUpdateAction, Excalidraw, Footer, loadLibraryFromBlob, MainMenu } from "@excalidraw/excalidraw";
import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type {
  AppState,
  BinaryFiles,
  ExcalidrawImperativeAPI,
  ExcalidrawInitialDataState,
  LibraryItems,
} from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";

import { api as httpApi } from "@/platform/http/api";
import type { WhiteboardLibraryItem } from "@/shared/types";
import { WHITEBOARD_LIBRARY_ASSETS, whiteboardLibraryUrl } from "./libraryAssets";
import { withoutEmbeddableElements, type StoredWhiteboardScene } from "./storage";
import { WhiteboardHelpDialog, WhiteboardHelpMenuItem, WhiteboardHelpTrigger } from "./WhiteboardHelp";
import "./whiteboard.css";

export interface ExcalidrawSceneChange {
  elements: readonly ExcalidrawElement[];
  appState: AppState;
  files: BinaryFiles;
}

export interface WhiteboardLibraryLoadError {
  clearFailed: boolean;
  failedAssetNames: readonly string[];
  sharedFailed?: boolean;
}

const UNSUPPORTED_LIBRARY_ELEMENT_TYPES = new Set(["image", "iframe", "embeddable"]);

function isSafeSharedLibraryItem(value: unknown): value is WhiteboardLibraryItem {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<WhiteboardLibraryItem>;
  if (typeof item.id !== "string" || !item.id.trim() || item.status !== "published" || !Number.isFinite(item.created)) return false;
  if (item.name !== undefined && typeof item.name !== "string") return false;
  return Array.isArray(item.elements) && item.elements.length > 0 && item.elements.every((element) => {
    if (!element || typeof element !== "object") return false;
    const candidate = element as { id?: unknown; type?: unknown };
    return typeof candidate.id === "string"
      && Boolean(candidate.id.trim())
      && typeof candidate.type === "string"
      && Boolean(candidate.type.trim())
      && !UNSUPPORTED_LIBRARY_ELEMENT_TYPES.has(candidate.type);
  });
}

type LoadedWhiteboardLibrary = {
  asset: typeof WHITEBOARD_LIBRARY_ASSETS[number];
  libraryItems: Awaited<ReturnType<typeof loadLibraryFromBlob>>;
};

type BundledLibraryLoadResult = {
  libraries: LoadedWhiteboardLibrary[];
  failedAssets: typeof WHITEBOARD_LIBRARY_ASSETS[number][];
};

let bundledLibrariesPromise: Promise<BundledLibraryLoadResult> | null = null;

function loadBundledLibraries() {
  if (!bundledLibrariesPromise) {
    bundledLibrariesPromise = (async () => {
      const failedAssets: typeof WHITEBOARD_LIBRARY_ASSETS[number][] = [];
      const libraries = (await Promise.all(WHITEBOARD_LIBRARY_ASSETS.map(async (asset) => {
        try {
          const response = await fetch(whiteboardLibraryUrl(asset.fileName));
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          return {
            asset,
            libraryItems: await loadLibraryFromBlob(await response.blob(), "published"),
          };
        } catch (error) {
          failedAssets.push(asset);
          console.warn(`[whiteboard] failed to load ${asset.name} library`, error);
          return null;
        }
      }))).filter((library): library is LoadedWhiteboardLibrary => library !== null);

      // Keep successful loads cached, but allow a later board to retry when
      // this attempt was incomplete (for example during a transient outage).
      if (failedAssets.length > 0) bundledLibrariesPromise = null;
      return { libraries, failedAssets };
    })();
  }
  return bundledLibrariesPromise;
}

/** Test-only cache reset; production callers keep successful loads cached. */
export function resetBundledLibrariesCache() {
  bundledLibrariesPromise = null;
}

export function ExcalidrawAdapter({ initialScene, onChange, canManageLibrary = false, onLibraryLoadError, onLibraryPublishError }: {
  initialScene: StoredWhiteboardScene | null;
  onChange: (scene: ExcalidrawSceneChange) => void;
  canManageLibrary?: boolean;
  onLibraryLoadError?: (error: WhiteboardLibraryLoadError) => void;
  onLibraryPublishError?: (message: string | null) => void;
}) {
  const libraryLoadStarted = useRef(false);
  const excalidrawApi = useRef<ExcalidrawImperativeAPI | null>(null);
  const mounted = useRef(true);
  const panelRef = useRef<HTMLElement>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const publishingLibraryIds = useRef(new Set<string>());
  const openHelp = useCallback(() => setHelpOpen(true), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const initialData: ExcalidrawInitialDataState | undefined = initialScene ? {
    elements: withoutEmbeddableElements(initialScene.elements),
    appState: initialScene.appState,
    files: initialScene.files,
  } : undefined;
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      excalidrawApi.current = null;
    };
  }, []);

  useEffect(() => {
    const handleHelpShortcut = (event: KeyboardEvent) => {
      if (event.key !== "?" || event.ctrlKey || event.metaKey || event.altKey) return;
      if (!(event.target instanceof Node) || !panelRef.current?.contains(event.target)) return;
      if (event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable=\"true\"]")) return;
      event.preventDefault();
      event.stopPropagation();
      openHelp();
    };

    window.addEventListener("keydown", handleHelpShortcut, true);
    return () => window.removeEventListener("keydown", handleHelpShortcut, true);
  }, [openHelp]);

  useEffect(() => {
    document.body.classList.toggle("whiteboard-library-manager", canManageLibrary);
    return () => document.body.classList.remove("whiteboard-library-manager");
  }, [canManageLibrary]);

  const handleLibraryChange = useCallback(async (libraryItems: LibraryItems) => {
    if (!canManageLibrary || !excalidrawApi.current) return;
    let publishFailed = false;
    const unpublishedItems = libraryItems.filter(
      (item) => item.status === "unpublished" && !publishingLibraryIds.current.has(item.id),
    );
    for (const item of unpublishedItems) {
      publishingLibraryIds.current.add(item.id);
      try {
        const name = (item.name?.trim() || window.prompt("给这个素材命名", "自定义素材")?.trim() || "");
        if (!name) {
          await excalidrawApi.current.updateLibrary({
            libraryItems: (currentItems) => currentItems.filter((currentItem) => currentItem.id !== item.id),
            merge: false,
          });
          continue;
        }
        const { item: publishedItem } = await httpApi.createWhiteboardLibraryItem(name, [...item.elements]);
        await excalidrawApi.current.updateLibrary({
          libraryItems: (currentItems) => currentItems.map((currentItem) => currentItem.id === item.id ? publishedItem as unknown as LibraryItems[number] : currentItem),
          merge: false,
        });
      } catch (error) {
        publishFailed = true;
        onLibraryPublishError?.("素材发布失败，暂未全局共享；请删除后重新加入素材库重试。");
        console.warn("[whiteboard] failed to publish library item", error);
      } finally {
        publishingLibraryIds.current.delete(item.id);
      }
    }
    if (!publishFailed) onLibraryPublishError?.(null);
  }, [canManageLibrary, onLibraryPublishError]);

  const handleExcalidrawAPI = useCallback((api: ExcalidrawImperativeAPI) => {
    excalidrawApi.current = api;
    if (libraryLoadStarted.current) return;
    libraryLoadStarted.current = true;

    const installBundledLibraries = async () => {
      if (!mounted.current) return;

      // Clear the engine's library before awaiting our local and shared assets
      // so stale browser-local items cannot flash into the catalogue.
      let clearFailed = false;
      try {
        await api.updateLibrary({
          libraryItems: [],
          merge: false,
          defaultStatus: "published",
        });
      } catch (error) {
        clearFailed = true;
        console.warn("[whiteboard] failed to clear the library", error);
      }

      if (!mounted.current) return;
      const { libraries: loadedLibraries, failedAssets } = await loadBundledLibraries();
      if (!mounted.current) return;

      const installationFailures = [...failedAssets];
      let libraryNeedsReplacement = clearFailed;
      for (const { asset, libraryItems } of loadedLibraries) {
        if (!mounted.current) return;
        try {
          await api.updateLibrary({
            libraryItems,
            merge: !libraryNeedsReplacement,
            defaultStatus: "published",
          });
          libraryNeedsReplacement = false;
        } catch (error) {
          installationFailures.push(asset);
          console.warn(`[whiteboard] failed to install ${asset.name} library`, error);
        }
      }
      let sharedFailed = false;
      try {
        const sharedLibrary = await httpApi.getWhiteboardLibrary();
        const safeSharedItems = sharedLibrary.items.filter(isSafeSharedLibraryItem);
        if (safeSharedItems.length > 0 && mounted.current) {
          await api.updateLibrary({
            libraryItems: safeSharedItems as unknown as LibraryItems,
            merge: true,
            defaultStatus: "published",
          });
        }
      } catch (error) {
        sharedFailed = true;
        console.warn("[whiteboard] failed to load shared library", error);
      }
      if (clearFailed || installationFailures.length > 0 || sharedFailed) {
        onLibraryLoadError?.({
          clearFailed,
          failedAssetNames: installationFailures.map((asset) => asset.name),
          sharedFailed,
        });
      }
    };

    void installBundledLibraries();
  }, [onLibraryLoadError]);

  const handleSceneChange = useCallback((elements: readonly ExcalidrawElement[], appState: AppState, files: BinaryFiles) => {
    const safeElements = elements.filter((element) => element.type !== "embeddable");
    if (safeElements.length !== elements.length) {
      excalidrawApi.current?.updateScene({
        elements: safeElements,
        captureUpdate: CaptureUpdateAction.NEVER,
      });
    }
    onChange({ elements: safeElements, appState, files });
  }, [onChange]);

  return <section ref={panelRef} className={`whiteboard-panel${canManageLibrary ? " whiteboard-can-manage-library" : ""}`} aria-label="白板绘图">
    <Excalidraw
      initialData={initialData}
      langCode="zh-CN"
      onChange={handleSceneChange}
      onLibraryChange={handleLibraryChange}
      excalidrawAPI={handleExcalidrawAPI}
      aiEnabled={false}
      validateEmbeddable={() => false}
    >
      <MainMenu>
        <MainMenu.DefaultItems.LoadScene />
        <MainMenu.DefaultItems.SaveToActiveFile />
        <MainMenu.DefaultItems.Export />
        <MainMenu.DefaultItems.SaveAsImage />
        <MainMenu.DefaultItems.SearchMenu />
        <WhiteboardHelpMenuItem onOpen={openHelp} />
        <MainMenu.DefaultItems.ClearCanvas />
        <MainMenu.Separator />
        <MainMenu.DefaultItems.ToggleTheme />
        <MainMenu.DefaultItems.ChangeCanvasBackground />
      </MainMenu>
      <Footer>
        <WhiteboardHelpTrigger onOpen={openHelp} />
      </Footer>
    </Excalidraw>
    <WhiteboardHelpDialog open={helpOpen} onClose={closeHelp} />
  </section>;
}

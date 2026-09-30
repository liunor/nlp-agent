import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Pencil } from "lucide-react";

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
import { TextInputDialog } from "@/shared/ui/TextInputDialog";
import type { WhiteboardLibraryItem } from "@/shared/types";
import { withoutEmbeddableElements, type StoredWhiteboardScene } from "./storage";
import { cloneWhiteboardAssetElements, findNearestWhiteboardAssetOrigin, findPresentedWhiteboardAsset, getPresentableWhiteboardElements, type WhiteboardPresentationElement } from "./whiteboardPresentation";
import { WhiteboardHelpDialog, WhiteboardHelpMenuItem, WhiteboardHelpTrigger } from "./WhiteboardHelp";
import { formatWhiteboardDeleteError } from "./whiteboardLibraryMessages";
import { WHITEBOARD_LIBRARY_ASSETS, whiteboardLibraryUrl } from "./libraryAssets";
import {
  getWhiteboardLibraryDisplayName,
  dedupeWhiteboardLibraryItems,
  getWhiteboardLibraryElementsFingerprint,
  getWhiteboardLibraryItemsRemoved,
  getWhiteboardLibraryItemsToMigrate,
  getSingleWhiteboardLibrarySelection,
  getWhiteboardLibraryTooltipPosition,
  normalizeWhiteboardLibraryItem,
  type WhiteboardLibraryTooltipPosition,
} from "./whiteboardLibraryOverlay";
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
const PRESENTATION_FIT_SETTLE_MS = 320;

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
          return { asset, libraryItems: await loadLibraryFromBlob(await response.blob(), "published") };
        } catch (error) {
          failedAssets.push(asset);
          console.warn(`[whiteboard] failed to load ${asset.name} library`, error);
          return null;
        }
      }))).filter((library): library is LoadedWhiteboardLibrary => library !== null);
      if (failedAssets.length > 0) bundledLibrariesPromise = null;
      return { libraries, failedAssets };
    })();
  }
  return bundledLibrariesPromise;
}

/** Test-only cache reset; successful production loads remain cached. */
export function resetBundledLibrariesCache() {
  bundledLibrariesPromise = null;
}

function getSafeSharedLibraryItem(value: unknown): WhiteboardLibraryItem | null {
  const item = normalizeWhiteboardLibraryItem(value);
  if (!item || item.status !== "published" || item.elements.length === 0) return null;
  if (!item.elements.every((element) => {
    if (!element || typeof element !== "object") return false;
    const candidate = element as { id?: unknown; type?: unknown };
    return typeof candidate.id === "string" && Boolean(candidate.id.trim()) && typeof candidate.type === "string" && Boolean(candidate.type.trim()) && !UNSUPPORTED_LIBRARY_ELEMENT_TYPES.has(candidate.type);
  })) return null;
  // Keep the server payload unchanged when installing it into Excalidraw.
  // Older rows may not have asset_code yet; the display layer supplies the
  // deterministic fallback while preserving object identity for callers.
  return value as WhiteboardLibraryItem;
}

interface WhiteboardLibraryHover extends WhiteboardLibraryTooltipPosition {
  name: string;
}

export function ExcalidrawAdapter({ initialScene, onChange, canManageLibrary = false, onLibraryLoadError, onLibraryLoadReady, onLibraryPublishError, presentRequest }: {
  initialScene: StoredWhiteboardScene | null;
  onChange: (scene: ExcalidrawSceneChange) => void;
  canManageLibrary?: boolean;
  onLibraryLoadError?: (error: WhiteboardLibraryLoadError) => void;
  onLibraryLoadReady?: () => void;
  onLibraryPublishError?: (message: string | null) => void;
  presentRequest?: { requestId: string; assetId: string; elements: unknown[]; name: string } | null;
}) {
  const libraryLoadStarted = useRef(false);
  const excalidrawApi = useRef<ExcalidrawImperativeAPI | null>(null);
  const mounted = useRef(true);
  const panelRef = useRef<HTMLElement>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [libraryHover, setLibraryHover] = useState<WhiteboardLibraryHover | null>(null);
  const [renameTarget, setRenameTarget] = useState<WhiteboardLibraryItem | null>(null);
  const [renameMenuHost, setRenameMenuHost] = useState<HTMLElement | null>(null);
  const [selectedLibraryItemId, setSelectedLibraryItemId] = useState<string | null>(null);
  const [libraryItemsVersion, setLibraryItemsVersion] = useState(0);
  const [apiReady, setApiReady] = useState(false);
  const hoveredLibraryUnit = useRef<HTMLElement | null>(null);
  const publishingLibraryIds = useRef(new Set<string>());
  const sharedLibraryItems = useRef(new Map<string, LibraryItems[number]>());
  const sharedLibraryReady = useRef(false);
  const libraryItemsRef = useRef<LibraryItems>([]);
  const openHelp = useCallback(() => setHelpOpen(true), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  const setLibrarySnapshot = useCallback((items: LibraryItems) => {
    libraryItemsRef.current = items;
    setLibraryItemsVersion((version) => version + 1);
  }, []);
  const syncLibraryDecorations = useCallback(() => {
    const root = panelRef.current;
    if (!root) return;
    const items = [...libraryItemsRef.current].filter((item) => item.elements.length > 0);
    const units = [...root.querySelectorAll<HTMLElement>(".library-unit.library-unit__active")]
      .filter((unit) => !unit.querySelector(".library-unit__adder"));
    const decoratedUnits = units.slice(0, items.length);
    for (const unit of units) {
      delete unit.dataset.whiteboardAssetId;
      delete unit.dataset.whiteboardName;
      unit.removeAttribute("title");
      unit.removeAttribute("aria-label");
    }
    decoratedUnits.forEach((unit, index) => {
      const item = items[index];
      const name = getWhiteboardLibraryDisplayName(item as WhiteboardLibraryItem);
      unit.dataset.whiteboardAssetId = item.id;
      unit.dataset.whiteboardName = name;
      // The overlay below is the single source of truth for hover names.
      // Setting a native title here creates a second browser tooltip on top
      // of it, especially in Chromium.
      unit.setAttribute("aria-label", `白板素材：${name}`);
    });
    // Excalidraw does not expose a library-item selection callback. If its
    // internal list is virtualized or includes a skeleton, DOM order is not a
    // trustworthy ID mapping, so never offer a rename target in that state.
    const selectionMappingReliable = units.length === items.length;
    const selected = selectionMappingReliable
      ? decoratedUnits
        .map((unit, index) => ({ unit, item: items[index] }))
        .filter(({ unit }) => unit.classList.contains("library-unit--selected"))
      : [];
    const selectedItem = getSingleWhiteboardLibrarySelection(selected)?.item ?? null;
    const selectedId = selectedItem?.id ?? null;
    const canRename = Boolean(canManageLibrary && selectedItem && selectedId && sharedLibraryItems.current.has(selectedId));
    const menuHost = canRename
      ? root.querySelector<HTMLElement>(".library-menu-dropdown-container--in-heading .dropdown-menu-container")
      : null;
    setSelectedLibraryItemId((current) => current === selectedId ? current : selectedId);
    setRenameMenuHost((current) => current === menuHost ? current : menuHost);
  }, [canManageLibrary]);

  useEffect(() => {
    const root = panelRef.current;
    if (!root) return undefined;
    let scheduled = false;
    const scheduleSync = () => {
      if (scheduled) return;
      scheduled = true;
      window.setTimeout(() => {
        scheduled = false;
        syncLibraryDecorations();
      }, 0);
    };
    const observer = new MutationObserver(scheduleSync);
    observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
    const updateHoverPosition = (target: HTMLElement | null) => {
      if (!target || !root.contains(target)) {
        hoveredLibraryUnit.current = null;
        setLibraryHover(null);
        return;
      }
      const name = target.dataset.whiteboardName;
      if (!name) {
        setLibraryHover(null);
        return;
      }
      const rect = target.getBoundingClientRect();
      setLibraryHover({ name, ...getWhiteboardLibraryTooltipPosition(rect, window.innerWidth || 1024, window.innerHeight || 768) });
    };
    const handlePointerOver = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-whiteboard-asset-id]") : null;
      if (!target || !root.contains(target)) return;
      hoveredLibraryUnit.current = target;
      updateHoverPosition(target);
    };
    const handlePointerOut = (event: PointerEvent) => {
      const target = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-whiteboard-asset-id]") : null;
      const related = event.relatedTarget instanceof Node ? event.relatedTarget : null;
      if (target && (!related || !target.contains(related))) {
        hoveredLibraryUnit.current = null;
        setLibraryHover(null);
      }
    };
    const handleViewportChange = () => updateHoverPosition(hoveredLibraryUnit.current);
    root.addEventListener("pointerover", handlePointerOver);
    root.addEventListener("pointerout", handlePointerOut);
    root.addEventListener("scroll", handleViewportChange, true);
    window.addEventListener("resize", handleViewportChange);
    syncLibraryDecorations();
    return () => {
      observer.disconnect();
      root.removeEventListener("pointerover", handlePointerOver);
      root.removeEventListener("pointerout", handlePointerOut);
      root.removeEventListener("scroll", handleViewportChange, true);
      window.removeEventListener("resize", handleViewportChange);
      hoveredLibraryUnit.current = null;
      setLibraryHover(null);
      setRenameMenuHost(null);
      setSelectedLibraryItemId(null);
    };
  }, [libraryItemsVersion, syncLibraryDecorations]);
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
    setLibrarySnapshot(libraryItems);
    if (!canManageLibrary || !excalidrawApi.current) return;
    let publishFailed = false;
    if (sharedLibraryReady.current && typeof httpApi.deleteWhiteboardLibraryItem === "function") {
      const removedItems = getWhiteboardLibraryItemsRemoved([...sharedLibraryItems.current.values()], libraryItems);
      for (const removedItem of removedItems) {
        try {
          await httpApi.deleteWhiteboardLibraryItem(removedItem.id);
          sharedLibraryItems.current.delete(removedItem.id);
        } catch (error) {
          if (typeof error === "object" && error !== null && "status" in error && (error as { status?: unknown }).status === 404) {
            sharedLibraryItems.current.delete(removedItem.id);
            continue;
          }
          publishFailed = true;
          const detail = error instanceof Error && error.message ? error.message : "素材仍被教材引用，暂不能删除。";
          const removedAssetCode = typeof (removedItem as { asset_code?: unknown }).asset_code === "string"
            ? (removedItem as unknown as { asset_code: string }).asset_code
            : removedItem.id;
          onLibraryPublishError?.(formatWhiteboardDeleteError(detail, removedAssetCode));
          await excalidrawApi.current.updateLibrary({ libraryItems: [...libraryItems, removedItem], merge: false, defaultStatus: "published" });
        }
      }
    }
    const unpublishedItems = libraryItems.filter((item) => item.status === "unpublished" && !publishingLibraryIds.current.has(item.id));
    for (const item of unpublishedItems) {
      publishingLibraryIds.current.add(item.id);
      try {
        const name = item.name?.trim() || window.prompt("给这个素材命名", "自定义素材")?.trim() || "";
        if (!name) {
          await excalidrawApi.current.updateLibrary({ libraryItems: (currentItems) => currentItems.filter((currentItem) => currentItem.id !== item.id), merge: false });
          continue;
        }
        const { item: publishedItem } = await httpApi.createWhiteboardLibraryItem(name, [...item.elements]);
        sharedLibraryItems.current.set(publishedItem.id, publishedItem as unknown as LibraryItems[number]);
        const nextLibrary = await excalidrawApi.current.updateLibrary({ libraryItems: (currentItems) => currentItems.map((currentItem) => currentItem.id === item.id ? publishedItem as unknown as LibraryItems[number] : currentItem), merge: false });
        setLibrarySnapshot(nextLibrary);
      } catch (error) {
        publishFailed = true;
        onLibraryPublishError?.("素材暂未全局共享；请删除后重新加入素材库重试。");
        console.warn("[whiteboard] failed to publish library item", error);
      } finally {
        publishingLibraryIds.current.delete(item.id);
      }
    }
    if (!publishFailed) onLibraryPublishError?.(null);
  }, [canManageLibrary, onLibraryPublishError, setLibrarySnapshot]);

  const renameSharedLibraryItem = useCallback(async (item: WhiteboardLibraryItem, nextName: string) => {
    const name = nextName.trim();
    if (!name || name === item.name?.trim()) return;
    if (typeof httpApi.renameWhiteboardLibraryItem !== "function") {
      onLibraryPublishError?.("当前版本暂不支持重命名素材。");
      return;
    }
    try {
      const result = await httpApi.renameWhiteboardLibraryItem(item.id, name);
      const renamedItem = normalizeWhiteboardLibraryItem(result.item) ?? { ...item, name };
      sharedLibraryItems.current.set(item.id, renamedItem as unknown as LibraryItems[number]);
      const api = excalidrawApi.current;
      if (api) {
        const nextLibrary = await api.updateLibrary({
          libraryItems: (currentItems) => currentItems.map((currentItem) => currentItem.id === item.id
            ? renamedItem as unknown as LibraryItems[number]
            : currentItem),
          merge: false,
          defaultStatus: "published",
        });
        setLibrarySnapshot(nextLibrary);
      }
      onLibraryPublishError?.(null);
    } catch (error) {
      onLibraryPublishError?.(error instanceof Error && error.message ? error.message : "素材重命名失败，请稍后重试。");
    }
  }, [onLibraryPublishError, setLibrarySnapshot]);

  const handleExcalidrawAPI = useCallback((api: ExcalidrawImperativeAPI) => {
    excalidrawApi.current = api;
    setApiReady(true);
    if (libraryLoadStarted.current) return;
    libraryLoadStarted.current = true;

    const loadSharedLibrary = async () => {
      if (!mounted.current) return;

      // Keep a snapshot until the shared request succeeds. Clearing first made
      // a transient 502 look like a successful empty catalog to the user.
      let previousLibraryItems = [...libraryItemsRef.current];
      let legacyItems: LibraryItems = [];
      if (canManageLibrary) {
        try {
          const existingLibrary = await api.updateLibrary({
            libraryItems: [],
            merge: true,
            defaultStatus: "published",
          });
          previousLibraryItems = [...existingLibrary];
          legacyItems = getWhiteboardLibraryItemsToMigrate(existingLibrary) as LibraryItems;
        } catch (error) {
          console.warn("[whiteboard] failed to inspect legacy library items", error);
        }
      }

      if (typeof httpApi.getWhiteboardLibrary !== "function") {
        onLibraryLoadReady?.();
        return;
      }

      let clearFailed = false;
      let sharedFailed: boolean | undefined;
      const failedAssetNames: string[] = [];
      const migratedLibraryItems: Array<LibraryItems[number]> = [];
      const migratedLegacyIds = new Set<string>();
      try {
        let sharedLibrary = await httpApi.getWhiteboardLibrary();

        // A previous client could have copied the vendored files into the
        // shared table before the deterministic catalog migration landed.
        // Do not migrate those random-id copies again when the server already
        // has the corresponding source-keyed item.
        const bundledElementFingerprints = new Set(
          sharedLibrary.items
            .map(normalizeWhiteboardLibraryItem)
            .filter((item): item is WhiteboardLibraryItem => Boolean(item?.source_key))
            .map((item) => getWhiteboardLibraryElementsFingerprint(item.elements)),
        );
        const legacyItemsToMigrate = getWhiteboardLibraryItemsToMigrate(legacyItems, bundledElementFingerprints);

        // Excalidraw keeps older user-created entries in its local library.
        // Migrate them only after the first shared read succeeds; otherwise a
        // retry after a network failure would create duplicate server rows.
        if (canManageLibrary && typeof httpApi.createWhiteboardLibraryItem === "function") {
          for (const item of legacyItemsToMigrate) {
            if (!mounted.current) return;
            try {
              const name = item.name?.trim() || "未命名图画";
              const { item: publishedItem } = await httpApi.createWhiteboardLibraryItem(name, [...item.elements]);
              migratedLibraryItems.push(publishedItem as unknown as LibraryItems[number]);
              migratedLegacyIds.add(item.id);
            } catch (error) {
              failedAssetNames.push(item.name?.trim() || "未命名图画");
              console.warn("[whiteboard] failed to migrate legacy library item", error);
            }
          }
          if (failedAssetNames.length > 0) throw new Error("legacy whiteboard migration failed");
          if (legacyItemsToMigrate.length > 0) sharedLibrary = await httpApi.getWhiteboardLibrary();
        }

        const safeSharedItems = sharedLibrary.items
          .map(getSafeSharedLibraryItem)
          .filter((item): item is WhiteboardLibraryItem => item !== null);
        const libraryItems = dedupeWhiteboardLibraryItems(safeSharedItems) as unknown as LibraryItems;

        // Only replace the local catalog after the server response is known to
        // be usable. If clearing fails, continue installing the bundled assets
        // so the board remains useful and report the degraded state to the UI.
        try {
          await api.updateLibrary({
            libraryItems: [],
            merge: false,
            defaultStatus: "published",
          });
        } catch (error) {
          clearFailed = true;
          console.warn("[whiteboard] failed to clear the local library", error);
        }
        if (!clearFailed) setLibrarySnapshot([]);

        // The shared catalog is populated from the same bundled files during
        // the server migration.  Loading those files again in the browser
        // creates a second set of entries with fresh Excalidraw IDs.  Keep
        // the local files only when the response does not contain a
        // source-keyed server catalog (older installations may only have
        // user-created rows until the migration has run).
        const hasServerBundledCatalog = libraryItems.some((item) => {
          const sourceKey = (item as unknown as { source_key?: unknown }).source_key;
          return typeof sourceKey === "string" && Boolean(sourceKey.trim());
        });
        const { libraries: bundledLibraries, failedAssets } = hasServerBundledCatalog
          ? { libraries: [], failedAssets: [] }
          : await loadBundledLibraries();
        failedAssetNames.push(...failedAssets.map((asset) => asset.name));

        sharedLibraryItems.current = new Map(libraryItems.map((item) => [item.id, item]));
        let nextLibrary = libraryItemsRef.current;
        for (const { libraryItems: bundledItems } of bundledLibraries) {
          nextLibrary = await api.updateLibrary({ libraryItems: bundledItems as LibraryItems, merge: true, defaultStatus: "published" });
          setLibrarySnapshot(nextLibrary);
        }
        if (libraryItems.length > 0) {
          nextLibrary = await api.updateLibrary({ libraryItems, merge: true, defaultStatus: "published" });
          setLibrarySnapshot(nextLibrary);
        }
        sharedLibraryReady.current = true;
        if (mounted.current && canManageLibrary && excalidrawApi.current) {
          await handleLibraryChange(libraryItemsRef.current);
        }
        onLibraryLoadReady?.();
        if (clearFailed || failedAssetNames.length > 0) {
          onLibraryLoadError?.({ clearFailed, failedAssetNames, sharedFailed: false });
        }
        return;
      } catch (error) {
        sharedFailed = true;
        console.warn("[whiteboard] failed to load shared library", error);
      }

      if (mounted.current) {
        try {
          const restoreItems = migratedLibraryItems.length > 0
            ? [...previousLibraryItems.filter((item) => !migratedLegacyIds.has(item.id)), ...migratedLibraryItems]
            : previousLibraryItems;
          const restoredLibrary = await api.updateLibrary({
            libraryItems: restoreItems,
            merge: false,
            defaultStatus: "published",
          });
          setLibrarySnapshot(restoredLibrary);
        } catch (error) {
          clearFailed = true;
          console.warn("[whiteboard] failed to restore the local library", error);
        }
      }
      if (clearFailed || sharedFailed || failedAssetNames.length > 0) {
        onLibraryLoadError?.({
          clearFailed,
          failedAssetNames,
          sharedFailed,
        });
      }
    };

    void loadSharedLibrary();
  }, [canManageLibrary, handleLibraryChange, onLibraryLoadError, onLibraryLoadReady, setLibrarySnapshot]);

  useEffect(() => {
    const api = excalidrawApi.current;
    if (!api || !presentRequest?.elements.length) return;
    const source = getPresentableWhiteboardElements(withoutEmbeddableElements(presentRequest.elements as ExcalidrawElement[]) as unknown as WhiteboardPresentationElement[]) as unknown as ExcalidrawElement[];
    if (!source.length) return;
    const current = api.getSceneElements();
    const currentPresentation = getPresentableWhiteboardElements(current as unknown as WhiteboardPresentationElement[]);
    const existing = findPresentedWhiteboardAsset(currentPresentation, presentRequest.assetId);
    const scheduleFocus = (elements: WhiteboardPresentationElement[]) => {
      const focus = (animate: boolean) => {
        if (excalidrawApi.current !== api) return;
        api.scrollToContent(elements as unknown as ExcalidrawElement[], { fitToContent: true, animate });
      };
      const frame = window.requestAnimationFrame(() => focus(true));
      const settle = window.setTimeout(() => focus(false), PRESENTATION_FIT_SETTLE_MS);
      return () => {
        window.cancelAnimationFrame(frame);
        window.clearTimeout(settle);
      };
    };
    if (existing.length > 0) {
      api.updateScene({ appState: { selectedElementIds: Object.fromEntries(existing.map((element) => [element.id, true])) } });
      return scheduleFocus(existing);
    }
    const appState = api.getAppState();
    const zoom = Math.max(0.1, appState.zoom.value || 1);
    const viewport = panelRef.current?.getBoundingClientRect();
    const viewportCenter = {
      centerX: -appState.scrollX + (viewport?.width ?? 900) / (2 * zoom),
      centerY: -appState.scrollY + (viewport?.height ?? 600) / (2 * zoom),
    };
    const origin = findNearestWhiteboardAssetOrigin(source as unknown as WhiteboardPresentationElement[], currentPresentation, viewportCenter);
    const presented = cloneWhiteboardAssetElements(source as unknown as WhiteboardPresentationElement[], origin, presentRequest.assetId, presentRequest.name);
    api.updateScene({
      elements: [...current, ...presented as unknown as ExcalidrawElement[]],
      appState: { selectedElementIds: Object.fromEntries(presented.map((element) => [element.id, true])) },
      captureUpdate: CaptureUpdateAction.IMMEDIATELY,
    });
    return scheduleFocus(presented);
  }, [apiReady, presentRequest]);

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
    {libraryHover && <div className="whiteboard-library-hover-tooltip" style={{ position: "fixed", top: libraryHover.top, left: libraryHover.left }} role="tooltip">{libraryHover.name}</div>}
    {renameMenuHost && selectedLibraryItemId && createPortal(
      <button
        type="button"
        className="dropdown-menu-item dropdown-menu-item-base whiteboard-library-rename-item"
        data-testid="whiteboard-library-rename"
        onClick={() => {
          const item = sharedLibraryItems.current.get(selectedLibraryItemId);
          if (!item) return;
          setRenameTarget(item as unknown as WhiteboardLibraryItem);
          setRenameMenuHost(null);
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
        }}
      >
        <span className="dropdown-menu-item__icon"><Pencil size={16} /></span>
        <span className="dropdown-menu-item__text">重命名</span>
      </button>,
      renameMenuHost,
    )}
    {renameTarget && <TextInputDialog
      open
      title="重命名白板素材"
      description="素材名称会显示在白板悬浮提示和知识教材按钮中。"
      label="素材名称"
      initialValue={renameTarget.name || "白板图画"}
      placeholder="例如：Transformer 注意力结构"
      confirmLabel="保存修改"
      onClose={() => setRenameTarget(null)}
      onConfirm={(value) => {
        const target = renameTarget;
        setRenameTarget(null);
        void renameSharedLibraryItem(target, value);
      }}
    />}
    <WhiteboardHelpDialog open={helpOpen} onClose={closeHelp} />
  </section>;
}

import type { ExcalidrawElement } from "@excalidraw/excalidraw/element/types";
import type { AppState, BinaryFiles } from "@excalidraw/excalidraw/types";

export const WHITEBOARD_STORAGE_PREFIX = "nova.whiteboard.v1:";

/** The small app-state subset that is useful when reopening a local board. */
export type PersistedWhiteboardAppState = Partial<Pick<
  AppState,
  | "theme"
  | "viewBackgroundColor"
  | "scrollX"
  | "scrollY"
  | "zoom"
  | "gridModeEnabled"
  | "viewModeEnabled"
  | "currentItemStrokeColor"
  | "currentItemBackgroundColor"
  | "currentItemFillStyle"
  | "currentItemStrokeWidth"
  | "currentItemStrokeStyle"
  | "currentItemRoughness"
  | "currentItemOpacity"
  | "currentItemFontFamily"
  | "currentItemFontSize"
  | "currentItemTextAlign"
>>;

export interface StoredWhiteboardScene {
  schemaVersion: 1;
  elements: readonly ExcalidrawElement[];
  appState: PersistedWhiteboardAppState;
  files: BinaryFiles;
}

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/**
 * Embeds are intentionally disabled in this product surface. Keep the same
 * invariant for restored scenes and for live scene changes so a stale local
 * entry cannot reintroduce an unsupported element or reach page-level agents.
 */
export function withoutEmbeddableElements(elements: readonly ExcalidrawElement[]): ExcalidrawElement[] {
  return elements.filter((element) => element.type !== "embeddable");
}

export function sanitizeWhiteboardScene(scene: StoredWhiteboardScene): StoredWhiteboardScene {
  const elements = withoutEmbeddableElements(scene.elements);
  return elements.length === scene.elements.length ? scene : { ...scene, elements };
}

export function storageKeyForUser(userId: string): string {
  return `${WHITEBOARD_STORAGE_PREFIX}${encodeURIComponent(userId)}`;
}

function resolveStorage(storage?: StorageLike): StorageLike | null {
  if (storage) return storage;
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStoredScene(value: unknown): value is StoredWhiteboardScene {
  return isRecord(value)
    && value.schemaVersion === 1
    && Array.isArray(value.elements)
    && isRecord(value.appState)
    && isRecord(value.files);
}

export function serializeWhiteboardScene(
  elements: readonly ExcalidrawElement[],
  appState: Readonly<AppState>,
  files: BinaryFiles,
): StoredWhiteboardScene {
  const persistedAppState: PersistedWhiteboardAppState = {
    theme: appState.theme,
    viewBackgroundColor: appState.viewBackgroundColor,
    scrollX: appState.scrollX,
    scrollY: appState.scrollY,
    zoom: appState.zoom,
    gridModeEnabled: appState.gridModeEnabled,
    viewModeEnabled: appState.viewModeEnabled,
    currentItemStrokeColor: appState.currentItemStrokeColor,
    currentItemBackgroundColor: appState.currentItemBackgroundColor,
    currentItemFillStyle: appState.currentItemFillStyle,
    currentItemStrokeWidth: appState.currentItemStrokeWidth,
    currentItemStrokeStyle: appState.currentItemStrokeStyle,
    currentItemRoughness: appState.currentItemRoughness,
    currentItemOpacity: appState.currentItemOpacity,
    currentItemFontFamily: appState.currentItemFontFamily,
    currentItemFontSize: appState.currentItemFontSize,
    currentItemTextAlign: appState.currentItemTextAlign,
  };

  return { schemaVersion: 1, elements, appState: persistedAppState, files };
}

export function readWhiteboardScene(userId: string, storage?: StorageLike): StoredWhiteboardScene | null {
  if (!userId) return null;
  const target = resolveStorage(storage);
  if (!target) return null;
  try {
    const raw = target.getItem(storageKeyForUser(userId));
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isStoredScene(parsed) ? sanitizeWhiteboardScene(parsed) : null;
  } catch {
    return null;
  }
}

export function writeWhiteboardScene(userId: string, scene: StoredWhiteboardScene, storage?: StorageLike): boolean {
  if (!userId) return false;
  const target = resolveStorage(storage);
  if (!target) return false;
  try {
    target.setItem(storageKeyForUser(userId), JSON.stringify(scene));
    return true;
  } catch {
    // Local storage is optional and may be unavailable or full. Report the
    // failure so the whiteboard can tell the user that a backup is needed.
    return false;
  }
}

export function clearWhiteboardScene(userId: string, storage?: StorageLike): void {
  if (!userId) return;
  const target = resolveStorage(storage);
  if (!target) return;
  try {
    target.removeItem(storageKeyForUser(userId));
  } catch {
    // Local storage is optional.
  }
}

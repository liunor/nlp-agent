import type { WhiteboardLibraryItem } from "@/shared/types";

export interface WhiteboardLibraryDisplayItem {
  id: string;
  asset_code?: string;
  name?: string;
}

export interface WhiteboardLibraryTooltipRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface WhiteboardLibraryTooltipPosition {
  top: number;
  left: number;
}

export const WHITEBOARD_LIBRARY_TOOLTIP_WIDTH = 220;
export const WHITEBOARD_LIBRARY_TOOLTIP_HEIGHT = 52;
export const WHITEBOARD_LIBRARY_TOOLTIP_GAP = 8;
export const WHITEBOARD_LIBRARY_TOOLTIP_MARGIN = 12;

function fallbackWhiteboardAssetCode(id: string): string {
  return `WB-${id.replace(/-/g, "").slice(0, 8).toUpperCase()}`;
}

/**
 * Normalizes records created before the human-readable code fields were added.
 * The API is shared by SQLite/MySQL installations, so the client must remain
 * tolerant of an older item_json payload while the server data is migrated.
 */
export function normalizeWhiteboardLibraryItem(value: unknown): WhiteboardLibraryItem | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const id = typeof candidate.id === "string" ? candidate.id.trim() : "";
  if (!id || !Array.isArray(candidate.elements)) return null;
  const assetCode = typeof candidate.asset_code === "string" && candidate.asset_code.trim()
    ? candidate.asset_code.trim()
    : fallbackWhiteboardAssetCode(id);
  const name = typeof candidate.name === "string" ? candidate.name : undefined;
  const status = candidate.status === "unpublished" ? "unpublished" : "published";
  const created = typeof candidate.created === "number" && Number.isFinite(candidate.created)
    ? candidate.created
    : 0;
  return {
    ...candidate,
    id,
    asset_code: assetCode,
    status,
    created,
    elements: candidate.elements,
    ...(name === undefined ? {} : { name }),
  } as WhiteboardLibraryItem;
}

export function getWhiteboardLibraryDisplayName(item: WhiteboardLibraryDisplayItem): string {
  const name = item.name?.trim();
  if (name) return name;
  const code = item.asset_code?.trim() || item.id;
  return `未命名图画 · ${code}`;
}

export function getSingleWhiteboardLibrarySelection<T>(items: readonly T[]): T | null {
  return items.length === 1 ? items[0] : null;
}

export function getWhiteboardLibraryItemsRemoved<T extends { id: string }>(
  previousItems: readonly T[],
  nextItems: readonly T[],
): T[] {
  const nextIds = new Set(nextItems.map((item) => item.id));
  return previousItems.filter((item) => !nextIds.has(item.id));
}

/**
 * Finds Excalidraw entries that predate the shared-library bridge.
 *
 * Older entries can already be marked `published` by Excalidraw's restore
 * path, so status is not a reliable migration signal. Shared entries carry
 * our asset_code; entries without it are still local material and should be
 * copied to the server before the local library is replaced.
 */
export function getWhiteboardLibraryItemsToMigrate<T extends {
  elements: readonly unknown[];
  asset_code?: unknown;
}>(items: readonly T[]): T[] {
  return items.filter((item) => {
    if (item.elements.length === 0) return false;
    return !(typeof item.asset_code === "string" && item.asset_code.trim());
  });
}

export function getWhiteboardLibraryTooltipPosition(
  rect: WhiteboardLibraryTooltipRect,
  viewportWidth: number,
  viewportHeight: number,
): WhiteboardLibraryTooltipPosition {
  const left = Math.min(
    Math.max(WHITEBOARD_LIBRARY_TOOLTIP_MARGIN, rect.left),
    Math.max(WHITEBOARD_LIBRARY_TOOLTIP_MARGIN, viewportWidth - WHITEBOARD_LIBRARY_TOOLTIP_WIDTH - WHITEBOARD_LIBRARY_TOOLTIP_MARGIN),
  );
  const below = rect.bottom + WHITEBOARD_LIBRARY_TOOLTIP_GAP;
  const above = rect.top - WHITEBOARD_LIBRARY_TOOLTIP_HEIGHT - WHITEBOARD_LIBRARY_TOOLTIP_GAP;
  const top = below + WHITEBOARD_LIBRARY_TOOLTIP_HEIGHT <= viewportHeight - WHITEBOARD_LIBRARY_TOOLTIP_MARGIN
    ? below
    : Math.max(WHITEBOARD_LIBRARY_TOOLTIP_MARGIN, above);
  return { top, left };
}

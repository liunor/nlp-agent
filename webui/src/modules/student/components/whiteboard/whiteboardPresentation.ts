export interface WhiteboardPresentationElement {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  groupIds?: readonly string[];
  customData?: Record<string, unknown>;
  isDeleted?: boolean;
  [key: string]: unknown;
}

/** Keep only drawable elements with finite geometry. Deleted library entries
 * can retain their old coordinates and would otherwise expand the fit bounds,
 * making the visible drawing appear cropped or impossibly small. */
export function getPresentableWhiteboardElements(elements: readonly WhiteboardPresentationElement[]): WhiteboardPresentationElement[] {
  return elements.filter((element) => element.type !== "embeddable"
    && element.isDeleted !== true
    && Number.isFinite(element.x)
    && Number.isFinite(element.y)
    && Number.isFinite(element.width)
    && Number.isFinite(element.height)
    && element.width >= 0
    && element.height >= 0);
}

export interface WhiteboardViewportCenter {
  centerX: number;
  centerY: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function bounds(elements: readonly WhiteboardPresentationElement[]) {
  return elements.reduce((result, element) => ({
    minX: Math.min(result.minX, element.x),
    minY: Math.min(result.minY, element.y),
    maxX: Math.max(result.maxX, element.x + Math.max(0, element.width)),
    maxY: Math.max(result.maxY, element.y + Math.max(0, element.height)),
  }), { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY, maxY: Number.NEGATIVE_INFINITY });
}

function overlaps(left: { x: number; y: number; width: number; height: number }, right: { x: number; y: number; width: number; height: number }) {
  return left.x < right.x + right.width && left.x + left.width > right.x && left.y < right.y + right.height && left.y + left.height > right.y;
}

/** Search outward from the visible center on a grid until the fragment does not collide. */
export function findNearestWhiteboardAssetOrigin(
  source: readonly WhiteboardPresentationElement[],
  current: readonly WhiteboardPresentationElement[],
  viewport: WhiteboardViewportCenter,
): { x: number; y: number } {
  const sourceBounds = bounds(source);
  const width = Math.max(1, sourceBounds.maxX - sourceBounds.minX);
  const height = Math.max(1, sourceBounds.maxY - sourceBounds.minY);
  const occupied = current.map((element) => ({ x: element.x, y: element.y, width: Math.max(0, element.width), height: Math.max(0, element.height) }));
  const baseX = viewport.centerX - width / 2;
  const baseY = viewport.centerY - height / 2;
  const stepX = width + 48;
  const stepY = height + 48;
  for (let radius = 0; radius <= 8; radius += 1) {
    for (let dx = -radius; dx <= radius; dx += 1) {
      for (let dy = -radius; dy <= radius; dy += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== radius) continue;
        const candidate = { x: baseX + dx * stepX, y: baseY + dy * stepY, width, height };
        if (!occupied.some((rect) => overlaps(candidate, rect))) return { x: candidate.x, y: candidate.y };
      }
    }
  }
  return { x: baseX, y: baseY };
}

export function findPresentedWhiteboardAsset(
  elements: readonly WhiteboardPresentationElement[],
  assetId: string,
): WhiteboardPresentationElement[] {
  return elements.filter((element) => element.isDeleted !== true && element.customData?.whiteboardAssetId === assetId);
}

/** Clone a library fragment while keeping bindings and tagging all elements with its source asset. */
export function cloneWhiteboardAssetElements(
  elements: readonly WhiteboardPresentationElement[],
  origin: { x: number; y: number },
  assetId: string,
  assetName: string,
): WhiteboardPresentationElement[] {
  const sourceBounds = bounds(elements);
  const elementIds = new Map<string, string>();
  const groupIds = new Map<string, string>();
  for (const element of elements) {
    elementIds.set(element.id, crypto.randomUUID());
    for (const groupId of element.groupIds ?? []) {
      if (!groupIds.has(groupId)) groupIds.set(groupId, crypto.randomUUID());
    }
  }
  const remapElementId = (value: string) => elementIds.get(value) ?? value;
  const remapGroupId = (value: string) => groupIds.get(value) ?? value;
  const remapOptionalElementId = (value: string) => elementIds.get(value) ?? null;
  return elements.map((element) => {
    const next = {
      ...element,
      id: remapElementId(element.id),
      x: element.x - sourceBounds.minX + origin.x,
      y: element.y - sourceBounds.minY + origin.y,
      version: 1,
      versionNonce: Math.floor(Math.random() * 2_000_000_000),
      updated: Date.now(),
      customData: { ...(element.customData ?? {}), whiteboardAssetId: assetId, whiteboardAssetName: assetName },
    } as WhiteboardPresentationElement;
    if (Array.isArray(next.groupIds)) next.groupIds = next.groupIds.map((value) => remapGroupId(value));
    // A library payload can contain a deleted/malformed container without its
    // text label. Leaving the old id in place makes Excalidraw treat the text
    // as an orphaned bound element and omit it from the rendered scene.
    for (const bindingKey of ["startBinding", "endBinding"]) {
      const binding = next[bindingKey];
      if (isRecord(binding) && typeof binding.elementId === "string") {
        const elementId = remapOptionalElementId(binding.elementId);
        next[bindingKey] = elementId ? { ...binding, elementId } : null;
      }
    }
    if (typeof next.containerId === "string") next.containerId = remapOptionalElementId(next.containerId);
    if (typeof next.frameId === "string") next.frameId = remapOptionalElementId(next.frameId);
    if (Array.isArray(next.boundElements)) {
      next.boundElements = next.boundElements
        .map((binding) => {
          if (!isRecord(binding) || typeof binding.id !== "string") return binding;
          const elementId = remapOptionalElementId(binding.id);
          return elementId ? { ...binding, id: elementId } : null;
        })
        .filter((binding): binding is Record<string, unknown> => binding !== null);
    }
    return next;
  });
}

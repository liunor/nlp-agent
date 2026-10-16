export interface KnowledgeBookWhiteboardRef {
  asset_id: string;
  asset_code?: string;
  name: string;
}

const WHITEBOARD_REF_PATTERN = /<!--\s*nova-whiteboard\s+asset="([^"]+)"(?:\s+code="([^"]*)")?(?:\s+name="([^"]*)")?\s*-->/g;
const WHITEBOARD_LINK_PATTERN = /^nova-whiteboard:\/\/([^?]+)(?:\?([^#]*))?$/;

export function parseKnowledgeBookWhiteboardRefs(markdown: string): KnowledgeBookWhiteboardRef[] {
  const refs: KnowledgeBookWhiteboardRef[] = [];
  for (const match of markdown.matchAll(WHITEBOARD_REF_PATTERN)) {
    const assetId = match[1]?.trim();
    if (!assetId) continue;
    let assetCode: string | undefined;
    if (match[2]) {
      try {
        assetCode = decodeURIComponent(match[2]);
      } catch {
        assetCode = match[2];
      }
    }
    let name = "白板图画";
    if (match[3]) {
      try {
        name = decodeURIComponent(match[3]);
      } catch {
        name = match[3];
      }
    }
    if (!name.trim()) name = "白板图画";
    refs.push(assetCode ? { asset_id: assetId, asset_code: assetCode, name } : { asset_id: assetId, name });
  }
  return refs;
}

export function addKnowledgeBookWhiteboardRef(markdown: string, ref: KnowledgeBookWhiteboardRef, atOffset?: number): string {
  if (parseKnowledgeBookWhiteboardRefs(markdown).some((candidate) => candidate.asset_id === ref.asset_id)) return markdown;
  const code = ref.asset_code ? ` code="${encodeURIComponent(ref.asset_code)}"` : "";
  const marker = `<!-- nova-whiteboard asset="${ref.asset_id}"${code} name="${encodeURIComponent(ref.name)}" -->`;
  if (atOffset === undefined) return `${marker}${markdown.trim() ? `\n\n${markdown}` : ""}`;
  const offset = Math.max(0, Math.min(markdown.length, atOffset));
  const lineStart = markdown.lastIndexOf("\n", Math.max(0, offset - 1)) + 1;
  return `${markdown.slice(0, lineStart)}${marker}\n\n${markdown.slice(lineStart)}`;
}

export function stripKnowledgeBookWhiteboardRefs(markdown: string): string {
  return markdown.replace(WHITEBOARD_REF_PATTERN, "").replace(/^\s+/, "");
}

export function renderKnowledgeBookWhiteboardRefs(markdown: string): string {
  return markdown.replace(WHITEBOARD_REF_PATTERN, (_marker, assetId: string, assetCode: string | undefined, encodedName: string | undefined) => {
    let name = "白板图画";
    if (encodedName) {
      try {
        name = decodeURIComponent(encodedName) || name;
      } catch {
        // Keep a malformed legacy marker readable instead of crashing the page.
        name = encodedName || name;
      }
    }
    const query = new URLSearchParams();
    if (assetCode) {
      try {
        query.set("code", decodeURIComponent(assetCode));
      } catch {
        query.set("code", assetCode);
      }
    }
    query.set("name", name);
    return `[查看图画](nova-whiteboard://${encodeURIComponent(assetId)}?${query.toString()})`;
  });
}

export function parseKnowledgeBookWhiteboardHref(href: string): KnowledgeBookWhiteboardRef | null {
  const match = WHITEBOARD_LINK_PATTERN.exec(href);
  if (!match) return null;
  const assetId = decodeURIComponent(match[1]);
  const params = new URLSearchParams(match[2] ?? "");
  if (!assetId) return null;
  return {
    asset_id: assetId,
    asset_code: params.get("code") || undefined,
    name: params.get("name") || "白板图画",
  };
}

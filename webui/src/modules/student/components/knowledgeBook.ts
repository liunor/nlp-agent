export interface MarkdownHeading {
  level: number;
  text: string;
  id: string;
}

export interface MarkdownHeadingIndex {
  headings: MarkdownHeading[];
  headingIds: string[];
}

export interface KnowledgeBookUrlState {
  tool: string | null;
  pointId: string | null;
  headingId: string | null;
  demo: boolean;
}

export function readKnowledgeBookUrl(search: string): KnowledgeBookUrlState {
  const params = new URLSearchParams(search);
  return {
    tool: params.get("tool"),
    pointId: params.get("bookPoint"),
    headingId: params.get("bookHeading"),
    demo: params.get("bookDemo") === "1" || params.get("bookDemo") === "true",
  };
}

export function replaceKnowledgeBookUrl({ pointId, headingId }: { pointId?: string | null; headingId?: string | null }): void {
  const url = new URL(window.location.href);
  url.searchParams.set("tool", "knowledge-book");
  if (pointId !== undefined) {
    if (pointId) url.searchParams.set("bookPoint", pointId);
    else url.searchParams.delete("bookPoint");
  }
  if (headingId !== undefined) {
    if (headingId) url.searchParams.set("bookHeading", headingId);
    else url.searchParams.delete("bookHeading");
  }
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
}

function stripHeadingMarkup(value: string): string {
  return value
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/<[^>]+>/g, "")
    .replace(/[`*_~]/g, "")
    .trim();
}

function slugifyHeading(value: string): string {
  const normalized = stripHeadingMarkup(value).normalize("NFKC").toLocaleLowerCase();
  const slug = normalized
    .replace(/[^\p{L}\p{N}\s_-]/gu, "")
    .replace(/[\s_]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return slug || "section";
}

function getFenceMarker(line: string): string | null {
  const match = /^ {0,3}(`{3,}|~{3,})/.exec(line);
  return match?.[1] ?? null;
}

function isFenceClose(line: string, marker: string): boolean {
  return new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(line);
}

/**
 * Extracts the headings that ReactMarkdown renders and creates stable, duplicate-safe IDs.
 * Fenced code is deliberately ignored so examples containing `#` do not become navigation items.
 */
export function indexMarkdownHeadings(markdown: string): MarkdownHeadingIndex {
  const headings: MarkdownHeading[] = [];
  const headingIds: string[] = [];
  const occurrences = new Map<string, number>();
  let fenceMarker: string | null = null;

  for (const line of markdown.split(/\r?\n/)) {
    if (fenceMarker) {
      if (isFenceClose(line, fenceMarker)) fenceMarker = null;
      continue;
    }
    const nextFence = getFenceMarker(line);
    if (nextFence) {
      fenceMarker = nextFence;
      continue;
    }

    const match = /^(?: {0,3})(#{1,4})[ \t]+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const level = match[1].length;
    const text = stripHeadingMarkup(match[2]);
    const baseId = slugifyHeading(text);
    const occurrence = (occurrences.get(baseId) ?? 0) + 1;
    occurrences.set(baseId, occurrence);
    const id = occurrence === 1 ? baseId : `${baseId}-${occurrence}`;
    headingIds.push(id);
    if (level >= 2) headings.push({ level, text, id });
  }

  return { headings, headingIds };
}

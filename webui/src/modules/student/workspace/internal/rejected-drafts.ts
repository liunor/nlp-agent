import type { ChatMessage } from "@/shared/types";

const PREFIX = "nova.rejected-drafts.v1:";

function key(owner: string, session: string) {
  return `${PREFIX}${encodeURIComponent(owner)}:${encodeURIComponent(session)}`;
}

export function readRejectedDrafts(owner: string | null, session: string): ChatMessage[] {
  if (!owner) return [];
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key(owner, session)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is ChatMessage => item && typeof item.id === "string"
      && typeof item.turnId === "string" && item.role === "user" && item.status === "failed"
      && typeof item.content === "string" && typeof item.createdAt === "string");
  } catch { return []; }
}

export function saveRejectedDraft(owner: string | null, session: string, message: ChatMessage) {
  if (!owner) return;
  try {
    const drafts = readRejectedDrafts(owner, session).filter((item) => item.id !== message.id);
    sessionStorage.setItem(key(owner, session), JSON.stringify([...drafts, message]));
  } catch { /* Storage can be disabled or full; the in-memory message remains visible. */ }
}

export function clearRejectedDrafts(owner: string | null) {
  if (!owner) return;
  try {
    const ownerPrefix = `${PREFIX}${encodeURIComponent(owner)}:`;
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const item = sessionStorage.key(i);
      if (item?.startsWith(ownerPrefix)) sessionStorage.removeItem(item);
    }
  } catch { /* Storage may be unavailable. */ }
}

export function removeRejectedDraft(owner: string | null, session: string, id: string) {
  if (!owner) return;
  try {
    const drafts = readRejectedDrafts(owner, session).filter((item) => item.id !== id);
    if (drafts.length) sessionStorage.setItem(key(owner, session), JSON.stringify(drafts));
    else sessionStorage.removeItem(key(owner, session));
  } catch { /* Storage may be unavailable. */ }
}

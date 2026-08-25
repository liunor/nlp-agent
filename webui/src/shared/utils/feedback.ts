import { createUuid } from "./uuid";

const FEEDBACK_STORAGE_KEY = "nlp-agent.feedback.v1";

function storageKey(userId?: string): string {
  return `${FEEDBACK_STORAGE_KEY}.${userId || "anonymous"}`;
}

export type StoredFeedback = {
  id: string;
  content: string;
  createdAt: string;
};

export function saveFeedback(content: string, userId?: string): StoredFeedback {
  const item: StoredFeedback = {
    id: createUuid(),
    content: content.trim(),
    createdAt: new Date().toISOString(),
  };
  const current = loadFeedback(userId);
  localStorage.setItem(storageKey(userId), JSON.stringify([...current, item].slice(-100)));
  return item;
}

export function loadFeedback(userId?: string): StoredFeedback[] {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey(userId)) ?? "[]") as unknown;
    return Array.isArray(value) ? value.filter((item): item is StoredFeedback => (
      typeof item === "object" && item !== null && typeof (item as StoredFeedback).content === "string"
    )) : [];
  } catch {
    return [];
  }
}

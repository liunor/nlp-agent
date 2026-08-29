import { useCallback, useLayoutEffect, useRef } from "react";

const BOTTOM_TOLERANCE = 24;

/** Preserves each conversation's reading position while retaining follow-to-bottom for live chats. */
export function useSessionScrollRestoration(sessionId: string | null, messages: readonly unknown[], loading: boolean) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const positions = useRef(new Map<string, number>());
  const followBottom = useRef(new Map<string, boolean>());
  const pendingRestore = useRef<string | null>(sessionId);
  const followBottomFrame = useRef<number | null>(null);

  useLayoutEffect(() => {
    pendingRestore.current = sessionId;
  }, [sessionId]);

  useLayoutEffect(() => {
    return () => {
      if (followBottomFrame.current !== null) {
        window.cancelAnimationFrame(followBottomFrame.current);
        followBottomFrame.current = null;
      }
    };
  }, [sessionId]);

  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (!scroll || !sessionId || loading || !messages.length) return;

    if (pendingRestore.current === sessionId) {
      const position = positions.current.get(sessionId);
      if (position == null) {
        scroll.scrollTop = scroll.scrollHeight;
        followBottom.current.set(sessionId, true);
      } else {
        scroll.scrollTop = position;
      }
      pendingRestore.current = null;
      return;
    }

    if (!followBottom.current.get(sessionId) || followBottomFrame.current !== null) return;
    followBottomFrame.current = window.requestAnimationFrame(() => {
      followBottomFrame.current = null;
      const currentScroll = scrollRef.current;
      if (currentScroll && followBottom.current.get(sessionId)) {
        currentScroll.scrollTop = currentScroll.scrollHeight;
      }
    });
  }, [loading, messages, sessionId]);

  const onScroll = useCallback(() => {
    const scroll = scrollRef.current;
    if (!scroll || !sessionId) return;
    positions.current.set(sessionId, scroll.scrollTop);
    followBottom.current.set(sessionId, scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight <= BOTTOM_TOLERANCE);
  }, [sessionId]);

  return { scrollRef, onScroll };
}

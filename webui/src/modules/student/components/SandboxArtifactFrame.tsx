import type { CSSProperties } from "react";

export function SandboxArtifactFrame({ url, title = "沙箱产物预览" }: { url: string; title?: string }) {
  return <iframe
    title={title}
    src={url}
    sandbox=""
    referrerPolicy="no-referrer"
    style={{ width: "100%", minHeight: 240, border: "1px solid var(--border-subtle, #d9dee8)", borderRadius: 8 } satisfies CSSProperties}
  />;
}

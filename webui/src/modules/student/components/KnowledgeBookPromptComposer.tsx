import { forwardRef, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

const MIN_HEIGHT = 36;

interface KnowledgeBookPromptComposerProps {
  ariaLabel: string;
  className: string;
  placeholder: string;
  onSubmit: (prompt: string) => void;
  style?: CSSProperties;
}

export const KnowledgeBookPromptComposer = forwardRef<HTMLFormElement, KnowledgeBookPromptComposerProps>(function KnowledgeBookPromptComposer({
  ariaLabel,
  className,
  placeholder,
  onSubmit,
  style,
}, ref) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.max(MIN_HEIGHT, input.scrollHeight)}px`;
    input.style.overflowY = "hidden";
  }, [value]);

  useLayoutEffect(() => {
    inputRef.current?.focus();
  }, []);

  return <form ref={ref} className={className} style={style} aria-label={ariaLabel} onPointerDown={(event) => event.stopPropagation()} onSubmit={(event) => {
    event.preventDefault();
    onSubmit(value.trim() || placeholder);
    setValue("");
  }}>
    <textarea
      ref={inputRef}
      aria-label={ariaLabel}
      rows={1}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      placeholder={placeholder}
    />
    <button type="submit">发送</button>
  </form>;
});

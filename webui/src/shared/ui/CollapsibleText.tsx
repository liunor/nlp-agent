import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface CollapsibleTextProps {
  text: string;
  maxLines?: number;
  maxLength?: number;
  className?: string;
}

export function CollapsibleText({
  text,
  maxLines = 3,
  maxLength = 120,
  className = "",
}: CollapsibleTextProps) {
  const [expanded, setExpanded] = useState(false);

  if (!text) {
    return null;
  }

  const isLong = text.length > maxLength || (text.match(/\n/g) || []).length >= maxLines;

  if (!isLong) {
    return <span className={`collapsible-text-plain ${className}`}>{text}</span>;
  }

  return (
    <div className={`collapsible-text ${expanded ? "is-expanded" : "is-collapsed"} ${className}`}>
      <div
        className="collapsible-text-content"
        style={
          expanded
            ? undefined
            : {
                display: "-webkit-box",
                WebkitLineClamp: maxLines,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
                wordBreak: "break-word",
              }
        }
      >
        {text}
      </div>
      <button
        type="button"
        className="collapsible-text-btn"
        onClick={() => setExpanded((prev) => !prev)}
        aria-expanded={expanded}
      >
        {expanded ? (
          <>
            <span>收起</span>
            <ChevronUp size={12} />
          </>
        ) : (
          <>
            <span>展开全文</span>
            <ChevronDown size={12} />
          </>
        )}
      </button>
    </div>
  );
}

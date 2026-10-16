import type { ReactNode } from "react";

interface ThumbnailElement {
  type?: unknown;
  x?: unknown;
  y?: unknown;
  width?: unknown;
  height?: unknown;
  points?: unknown;
  text?: unknown;
  strokeColor?: unknown;
  backgroundColor?: unknown;
  strokeWidth?: unknown;
  strokeStyle?: unknown;
  opacity?: unknown;
  angle?: unknown;
  startArrowhead?: unknown;
  endArrowhead?: unknown;
  fontSize?: unknown;
  fontFamily?: unknown;
  textAlign?: unknown;
}

function number(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function color(value: unknown, fallback: string): string {
  return typeof value === "string" && (/^#[0-9a-f]{3,8}$/i.test(value) || value === "transparent") ? value : fallback;
}

function transform(element: ThumbnailElement, x: number, y: number, width: number, height: number): string | undefined {
  const angle = number(element.angle);
  if (!angle) return undefined;
  const centerX = x + width / 2;
  const centerY = y + height / 2;
  return `rotate(${angle * 180 / Math.PI} ${centerX} ${centerY})`;
}

function elementBounds(element: ThumbnailElement) {
  const x = number(element.x);
  const y = number(element.y);
  const width = Math.max(0, number(element.width));
  const height = Math.max(0, number(element.height));
  const points = Array.isArray(element.points) ? element.points.filter((point): point is [number, number] => Array.isArray(point) && typeof point[0] === "number" && Number.isFinite(point[0]) && typeof point[1] === "number" && Number.isFinite(point[1])) : [];
  return {
    minX: Math.min(x, ...points.map((point) => x + point[0])),
    minY: Math.min(y, ...points.map((point) => y + point[1])),
    maxX: Math.max(x + width, ...points.map((point) => x + point[0])),
    maxY: Math.max(y + height, ...points.map((point) => y + point[1])),
  };
}

function renderElement(element: ThumbnailElement, index: number): ReactNode {
  const type = typeof element.type === "string" ? element.type : "";
  const x = number(element.x);
  const y = number(element.y);
  const width = Math.max(0, number(element.width));
  const height = Math.max(0, number(element.height));
  const stroke = color(element.strokeColor, "#5b5f72");
  const fill = color(element.backgroundColor, "transparent");
  const strokeWidth = Math.max(1, Math.min(6, number(element.strokeWidth, 2)));
  const strokeDasharray = element.strokeStyle === "dashed" ? "8 6" : element.strokeStyle === "dotted" ? "2 6" : undefined;
  const opacity = Math.max(0, Math.min(1, number(element.opacity, 100) / 100));
  const common = { stroke, strokeWidth, fill, strokeDasharray, opacity, transform: transform(element, x, y, width, height) };
  const key = `${type}-${index}`;
  if (type === "ellipse") return <ellipse key={key} {...common} cx={x + width / 2} cy={y + height / 2} rx={Math.max(1, width / 2)} ry={Math.max(1, height / 2)} />;
  if (type === "diamond") return <polygon key={key} {...common} points={`${x + width / 2},${y} ${x + width},${y + height / 2} ${x + width / 2},${y + height} ${x},${y + height / 2}`} />;
  if (type === "rectangle" || type === "frame") return <rect key={key} {...common} x={x} y={y} width={Math.max(1, width)} height={Math.max(1, height)} rx={3} />;
  if (type === "line" || type === "arrow") {
    const points = Array.isArray(element.points) ? element.points : [];
    const validPoints = points.filter((point): point is [number, number] => Array.isArray(point) && typeof point[0] === "number" && Number.isFinite(point[0]) && typeof point[1] === "number" && Number.isFinite(point[1]));
    const coordinates = validPoints.length > 1
      ? validPoints.map((point) => `${x + point[0]},${y + point[1]}`).join(" ")
      : `${x},${y} ${x + width},${y + height}`;
    return <polyline key={key} {...common} fill="none" points={coordinates} markerEnd={type === "arrow" && element.endArrowhead !== "none" ? "url(#whiteboard-thumbnail-arrow)" : undefined} markerStart={type === "arrow" && element.startArrowhead && element.startArrowhead !== "none" ? "url(#whiteboard-thumbnail-arrow-start)" : undefined} />;
  }
  if (type === "freedraw") {
    const points = Array.isArray(element.points) ? element.points.filter((point): point is [number, number] => Array.isArray(point) && typeof point[0] === "number" && typeof point[1] === "number") : [];
    if (points.length > 1) return <polyline key={key} {...common} fill="none" points={points.map((point) => `${x + point[0]},${y + point[1]}`).join(" ")} />;
  }
  if (type === "text" && typeof element.text === "string") {
    const fontSize = Math.max(10, Math.min(28, number(element.fontSize, height || 14)));
    const lines = element.text.split(/\r?\n/).slice(0, 5);
    const textAnchor = element.textAlign === "center" ? "middle" : element.textAlign === "right" ? "end" : "start";
    const textX = textAnchor === "middle" ? x + width / 2 : textAnchor === "end" ? x + width : x;
    return <text key={`text-${index}`} x={textX} y={y + fontSize} stroke="none" fill={stroke} opacity={opacity} transform={transform(element, x, y, width, height)} fontSize={fontSize} fontFamily={typeof element.fontFamily === "string" ? element.fontFamily : "sans-serif"} textAnchor={textAnchor}>
      {lines.map((line, lineIndex) => <tspan key={`${key}-${lineIndex}`} x={textX} dy={lineIndex === 0 ? 0 : fontSize * 1.2}>{line.slice(0, 80)}</tspan>)}
    </text>;
  }
  return null;
}

export function WhiteboardAssetThumbnail({ elements, label }: { elements: unknown[]; label: string }) {
  const safeElements = elements.filter((element): element is ThumbnailElement => Boolean(element && typeof element === "object")).slice(0, 80);
  const defaultBounds = { minX: 0, minY: 0, maxX: 100, maxY: 80 };
  const bounds = safeElements.length === 0 ? defaultBounds : safeElements.slice(1).reduce((result, element) => {
    const next = elementBounds(element);
    return { minX: Math.min(result.minX, next.minX), minY: Math.min(result.minY, next.minY), maxX: Math.max(result.maxX, next.maxX), maxY: Math.max(result.maxY, next.maxY) };
  }, elementBounds(safeElements[0]));
  const padding = 12;
  const width = Math.max(100, bounds.maxX - bounds.minX + padding * 2);
  const height = Math.max(80, bounds.maxY - bounds.minY + padding * 2);
  return <svg className="teacher-whiteboard-library-thumbnail" role="img" aria-label={`预览：${label}`} viewBox={`${bounds.minX - padding} ${bounds.minY - padding} ${width} ${height}`} preserveAspectRatio="xMidYMid meet">
    <defs>
      <marker id="whiteboard-thumbnail-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 8 4 L 0 8 z" fill="#5b5f72" /></marker>
      <marker id="whiteboard-thumbnail-arrow-start" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto-start-reverse" markerUnits="strokeWidth"><path d="M 0 0 L 8 4 L 0 8 z" fill="#5b5f72" /></marker>
    </defs>
    <rect x={bounds.minX - padding} y={bounds.minY - padding} width={width} height={height} rx={8} fill="#fafaff" />
    {safeElements.map(renderElement)}
  </svg>;
}

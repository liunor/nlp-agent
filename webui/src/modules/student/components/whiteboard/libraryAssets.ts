/**
 * Teaching-oriented libraries vendored from the Excalidraw public library
 * directory. The files are served from our own origin so the board remains
 * usable without reaching out to a third-party service at runtime.
 */
export const WHITEBOARD_LIBRARY_ASSETS = [
  {
    name: "Deep learning",
    fileName: "deep-learning.excalidrawlib",
    repositoryPath: "yuelfei/deep-learning.excalidrawlib",
  },
  {
    name: "Data processing",
    fileName: "data-processing.excalidrawlib",
    repositoryPath: "erlina/data-processing.excalidrawlib",
  },
  {
    name: "Mathematical Symbols",
    fileName: "mathematical-symbols.excalidrawlib",
    repositoryPath: "jjadup/mathematical-symbols.excalidrawlib",
  },
  {
    name: "Flow Chart Symbols",
    fileName: "flow-chart-symbols.excalidrawlib",
    repositoryPath: "finfin/flow-chart-symbols.excalidrawlib",
  },
  {
    name: "Montessori Basic Grammar Symbols",
    fileName: "montessori-basic-grammar-symbols.excalidrawlib",
    repositoryPath: "alluvion/montessori-basic-grammar-symbols.excalidrawlib",
  },
  {
    name: "Bubbles",
    fileName: "bubbles.excalidrawlib",
    repositoryPath: "ocapraro/bubbles.excalidrawlib",
  },
] as const;

export function whiteboardLibraryUrl(fileName: string) {
  return `${import.meta.env.BASE_URL}excalidraw/libraries/${fileName}`;
}

import { render, screen, fireEvent, within } from "@testing-library/react";
import { useState } from "react";
import { expect, it, vi } from "vitest";
import { DocumentTabs } from "./DocumentTabs";

function Tabs() {
  const [documents, setDocuments] = useState([{ id: "a", name: "A.pdf" }, { id: "b", name: "B.docx" }]);
  const [activeId, setActiveId] = useState("a");
  return <DocumentTabs prefix="test" documents={documents} activeId={activeId} onSelect={setActiveId} onClose={id => setDocuments(items => items.filter(item => item.id !== id))}
    onReorder={(source,target) => setDocuments(items => { const next = [...items]; const from = next.findIndex(item => item.id === source), to = next.findIndex(item => item.id === target); next.splice(to,0,next.splice(from,1)[0]); return next; })} />;
}
it("supports keyboard switching, keyboard reordering, and close actions", () => {
  render(<Tabs />);
  fireEvent.keyDown(screen.getByRole("tab", { name: "A.pdf" }), { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "B.docx" })).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(screen.getByRole("tab", { name: "B.docx" }), { key: "ArrowLeft", altKey: true });
  expect(screen.getAllByRole("tab").map(item => item.textContent)).toEqual(["B.docx", "A.pdf"]);
  fireEvent.click(screen.getByRole("button", { name: "关闭 A.pdf" }));
  expect(screen.getAllByRole("tab")).toHaveLength(1);
});
it("reorders files when one tab is dragged onto another", () => {
  render(<Tabs />);
  const transfer = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
  fireEvent.dragStart(screen.getByRole("tab", { name: "A.pdf" }).parentElement!, { dataTransfer: transfer });
  fireEvent.dragOver(screen.getByRole("tab", { name: "B.docx" }).parentElement!, { dataTransfer: transfer });
  fireEvent.drop(screen.getByRole("tab", { name: "B.docx" }).parentElement!, { dataTransfer: transfer });
  expect(within(screen.getByRole("tablist")).getAllByRole("tab").map(item => item.textContent)).toEqual(["B.docx", "A.pdf"]);
});

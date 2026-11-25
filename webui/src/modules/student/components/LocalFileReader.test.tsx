import { expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { zipSync, strToU8 } from "fflate";
import { LocalFileReader, readLocalDocx } from "./LocalFileReader";

it("extracts local DOCX paragraphs and tables without rendering document markup", () => {
  const zip = zipSync({ "word/document.xml": strToU8('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&lt;script&gt;中文</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>') });
  expect(readLocalDocx(zip)).toEqual(["<script>中文", "42"]);
});

it("keeps a local PDF offline until the user explicitly requests upload", () => {
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "about:blank#pdf") });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  const listener = vi.fn();
  window.addEventListener("file-space:attach-local", listener);
  const file = new File(["%PDF-"], "local.pdf");
  const view = render(<LocalFileReader file={file} onClose={vi.fn()} />);
  expect(listener).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "上传并提问" }));
  expect(listener).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("about:blank#pdf");
  window.removeEventListener("file-space:attach-local", listener);
});

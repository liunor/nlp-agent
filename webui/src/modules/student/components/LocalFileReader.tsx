import { useEffect, useRef, useState } from "react";
import { strFromU8, unzipSync } from "fflate";

const WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";

export function readLocalDocx(bytes: Uint8Array): string[] {
  let entries = 0;
  const files = unzipSync(bytes, { filter: file => {
    if (++entries > 2000) throw new Error("文档包含过多项目");
    if (file.name !== "word/document.xml") return false;
    if (file.originalSize > 8 * 1024 * 1024) throw new Error("文档正文过大");
    return true;
  } });
  const xml = files["word/document.xml"];
  if (!xml) throw new Error("不是有效的 DOCX 文件");
  const source = strFromU8(xml);
  if (/<!DOCTYPE|<!ENTITY/i.test(source) || source.includes(String.fromCharCode(0))) throw new Error("不支持该 XML 格式");
  const document = new DOMParser().parseFromString(source, "application/xml");
  if (document.querySelector("parsererror")) throw new Error("文档正文损坏");
  const body = Array.from(document.getElementsByTagName("*")).find(element => element.namespaceURI === WORD_NS && element.localName === "body");
  if (!body) throw new Error("文档缺少正文");
  const text = (element: Element) => Array.from(element.getElementsByTagName("*")).filter(node => node.namespaceURI === WORD_NS && node.localName === "t").map(node => node.textContent ?? "").join("");
  return Array.from(body.children).filter(element => ["p", "tbl"].includes(element.localName)).map(element => {
    if (element.localName === "p") return text(element);
    return Array.from(element.children).filter(row => row.localName === "tr").map(row =>
      Array.from(row.children).filter(cell => cell.localName === "tc").map(text).join("\t")).join("\n");
  });
}

export function LocalFileReader({ file, onClose }: { file: File; onClose: () => void }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const tooLarge = file.size > 25 * 1024 * 1024;
  const [blocks, setBlocks] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const pdf = file.name.toLowerCase().endsWith(".pdf");
  useEffect(() => {
    let active = true;
    if (tooLarge) return;
    if (pdf) {
      const value = URL.createObjectURL(file.slice(0, file.size, "application/pdf"));
      if (frame.current) frame.current.src = value;
      return () => URL.revokeObjectURL(value);
    }
    void file.arrayBuffer().then(buffer => readLocalDocx(new Uint8Array(buffer))).then(value => {
      if (active) setBlocks(value);
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取文档"); });
    return () => { active = false; };
  }, [file, pdf, tooLarge]);
  return <section className="files-panel-preview" aria-label={`本地阅读 ${file.name}`}>
    <header className="files-panel-preview-header"><strong>{file.name}</strong><button type="button" onClick={onClose}>关闭阅读</button></header>
    <p>仅在本机查看，未上传；刷新后需重新导入。</p>
    <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("file-space:attach-local", { detail: { file } }))}>上传并提问</button>
    {(error || tooLarge) && <p role="alert">{error || "本地阅读暂支持 25 MiB 以内的文件"}</p>}
    {pdf && !tooLarge && <iframe ref={frame} title={`本地预览 ${file.name}`} style={{ width: "100%", height: "65vh", border: 0 }} />}
    {!pdf && <div className="files-panel-preview-body"><p>阅读视图；图片、图表和复杂排版未提取。</p>
      {blocks.slice(page * 20, page * 20 + 20).map((text, index) => <pre className="files-panel-plain" key={index}>{text}</pre>)}
      {page > 0 && <button type="button" onClick={() => setPage(value => value - 1)}>上一段</button>}
      {(page + 1) * 20 < blocks.length && <button type="button" onClick={() => setPage(value => value + 1)}>继续读取</button>}
    </div>}
  </section>;
}

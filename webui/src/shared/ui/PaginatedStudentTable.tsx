import { useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export function PaginatedStudentTable({ headers, rows, personLabel = "学生" }: {
  headers: string[];
  personLabel?: "学生" | "用户";
  rows: Array<{ id: string; cells: ReactNode[] }>;
}) {
  const [page, setPage] = useState(0);
  const pageSize = 8;
  const pageCount = Math.max(1, Math.ceil(rows.length / pageSize));
  const activePage = Math.min(page, pageCount - 1);
  const first = rows.length ? activePage * pageSize + 1 : 0;
  const last = Math.min((activePage + 1) * pageSize, rows.length);
  return <>
    <div className="teacher-question-activity-table"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
      <tbody>{rows.slice(activePage * pageSize, (activePage + 1) * pageSize).map((row) => <tr key={row.id}>{row.cells.map((cell, index) => <td key={index}>{cell}</td>)}</tr>)}</tbody>
    </table></div>
    <footer className="teacher-question-pagination"><span>显示 {first}–{last} / 共 {rows.length} 名{personLabel}</span><div>
      <button type="button" aria-label="上一页" disabled={activePage === 0} onClick={() => setPage(activePage - 1)}><ChevronLeft size={15} /></button>
      <strong>{activePage + 1} / {pageCount}</strong>
      <button type="button" aria-label="下一页" disabled={activePage >= pageCount - 1} onClick={() => setPage(activePage + 1)}><ChevronRight size={15} /></button>
    </div></footer>
  </>;
}

export function formatWhiteboardDeleteError(detail: string, assetCode: string): string {
  const message = detail.trim() || "素材仍被教材引用，暂不能删除。";
  return `${message} 请进入教师端“知识教材”，在正文编辑器按 Ctrl+F 搜索素材代号 ${assetCode}，删除对应的 nova-whiteboard 标记，并保存草稿、发布所有引用页面。系统会同时检查草稿和已发布正文；如果仍提示，说明其他教材页面或已发布版本仍保留引用。`;
}

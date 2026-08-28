import { AlertCircle, Bold, BookOpenText, ChevronDown, Code2, Eye, EyeOff, FileUp, FolderPlus, Heading2, Italic, Link2, List, MessageSquareQuote, MoreHorizontal, PanelLeftClose, PanelLeftOpen, Pencil, Plus, RefreshCw, Save, Search, Send, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";

import { api } from "@/platform/http/api";
import type { CourseTopic, TeacherBookArchiveImportPreview, TeacherBookAssetInput, TeacherBookImportPreview, TeacherBookNavigationItem, TeacherBookPage, TeacherCatalog } from "@/shared/types";
import { MarkdownContent } from "@/modules/student/components/MarkdownContent";
import { createUuid } from "@/shared/utils/uuid";
import { indexMarkdownHeadings } from "@/modules/student/components/knowledgeBook";
import { ConfirmDialog } from "@/shared/ui/ConfirmDialog";
import { TextInputDialog } from "@/shared/ui/TextInputDialog";

type Props = { workspaceId: string; catalog?: TeacherCatalog; onCatalogChange?: (catalog: TeacherCatalog) => void };

type MarkdownFormat = "bold" | "italic" | "heading" | "code" | "link" | "list" | "quote";

export function formatMarkdownSelection(value: string, start: number, end: number, format: MarkdownFormat) {
  const selected = value.slice(start, end);
  const fallback = selected || (format === "link" ? "链接文字" : format === "code" ? "代码" : "文本");
  let replacement = fallback;

  if (format === "bold") replacement = `**${fallback}**`;
  if (format === "italic") replacement = `*${fallback}*`;
  if (format === "heading") replacement = fallback.split("\n").map((line) => `## ${line}`).join("\n");
  if (format === "code") replacement = `\`\`\`python\n${fallback}\n\`\`\``;
  if (format === "link") replacement = `[${fallback}](https://)`;
  if (format === "list") replacement = fallback.split("\n").map((line) => `- ${line}`).join("\n");
  if (format === "quote") replacement = fallback.split("\n").map((line) => `> ${line}`).join("\n");

  return {
    value: `${value.slice(0, start)}${replacement}${value.slice(end)}`,
    selectionStart: start,
    selectionEnd: start + replacement.length,
  };
}

async function fileToBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return window.btoa(binary);
}

function assetPathForFile(file: File): string {
  const relativePath = (file.webkitRelativePath ?? "").replaceAll("\\", "/");
  const assetsIndex = relativePath.indexOf("assets/");
  return assetsIndex >= 0 ? relativePath.slice(assetsIndex) : `assets/${file.name}`;
}

function dataUrlForAsset(asset: TeacherBookAssetInput): string {
  return `data:${asset.media_type || "application/octet-stream"};base64,${asset.content_base64}`;
}

function insertImageReferences(value: string, start: number, end: number, references: string) {
  const before = value.slice(0, start);
  const after = value.slice(end);
  const beforeSeparator = before && !before.endsWith("\n") ? "\n\n" : "";
  const afterSeparator = after && !after.startsWith("\n") ? "\n\n" : "";
  const inserted = `${beforeSeparator}${references}${afterSeparator}`;
  return {
    value: `${before}${inserted}${after}`,
    selectionStart: start + beforeSeparator.length,
    selectionEnd: start + beforeSeparator.length + references.length,
  };
}

function replaceLocalAssetReferences(markdown: string, previews: Record<string, string>) {
  const entries = Object.entries(previews);
  if (!entries.length) return markdown;
  return markdown.replace(/(!\[[^\]]*\]\()<?([^\s)>]+)>?(\))/g, (match, prefix: string, source: string, suffix: string) => {
    let normalizedSource = source;
    try {
      normalizedSource = decodeURIComponent(source);
    } catch {
      // Keep a malformed URL untouched; the server-side validator will report it.
    }
    normalizedSource = normalizedSource.split(/[?#]/, 1)[0];
    const preview = entries.find(([assetPath]) => normalizedSource === assetPath || normalizedSource.endsWith(`/${assetPath}`) || normalizedSource.endsWith(assetPath));
    return preview ? `${prefix}${preview[1]}${suffix}` : match;
  });
}

function withTopicUpdate(catalog: TeacherCatalog, topicId: string, update: (topic: CourseTopic) => CourseTopic): TeacherCatalog {
  return { ...catalog, topics: catalog.topics.map((topic) => topic.id === topicId ? update(topic) : topic) };
}

function newKnowledgePoint(name = "未命名知识点", sortOrder = 0) {
  return { id: createUuid(), name, markdown: "", status: "enabled" as const, sort_order: sortOrder };
}

type TeacherBookTreeGroup = { topicId: string; topicName: string; topicStatus: CourseTopic["status"]; items: TeacherBookNavigationItem[] };
type CatalogInputAction = { kind: "new-topic" } | { kind: "edit-topic"; topicId: string } | { kind: "edit-point"; topicId: string; pointId: string };
type CatalogInputState = CatalogInputAction & { topicId?: string; pointId?: string; title: string; description: string; label: string; value: string; placeholder: string; confirmLabel: string };
type CatalogDeleteTarget = { kind: "topic"; topicId: string; name: string } | { kind: "point"; topicId: string; pointId: string; name: string };

function groupNavigation(items: TeacherBookNavigationItem[]): TeacherBookTreeGroup[] {
  return items.reduce<TeacherBookTreeGroup[]>((groups, item) => {
    const group = groups.find((value) => value.topicId === item.topic_id);
    if (group) group.items.push(item);
    else groups.push({ topicId: item.topic_id, topicName: item.topic_name, topicStatus: item.topic_status, items: [item] });
    return groups;
  }, []);
}

export function TeacherBookEditor({ workspaceId, catalog, onCatalogChange }: Props) {
  const [navigation, setNavigation] = useState<TeacherBookNavigationItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [page, setPage] = useState<TeacherBookPage | null>(null);
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState(false);
  const [importPreview, setImportPreview] = useState<TeacherBookImportPreview | null>(null);
  const [importName, setImportName] = useState("");
  const [importAssets, setImportAssets] = useState<TeacherBookAssetInput[]>([]);
  const [editorAssets, setEditorAssets] = useState<TeacherBookAssetInput[]>([]);
  const [editorAssetPreviews, setEditorAssetPreviews] = useState<Record<string, string>>({});
  const [catalogDraft, setCatalogDraft] = useState<TeacherCatalog | null>(catalog ?? null);
  const [directoryCollapsed, setDirectoryCollapsed] = useState(false);
  const [directoryQuery, setDirectoryQuery] = useState("");
  const [collapsedTopicIds, setCollapsedTopicIds] = useState<string[]>([]);
  const [directorySaving, setDirectorySaving] = useState(false);
  const [catalogInput, setCatalogInput] = useState<CatalogInputState | null>(null);
  const [catalogDeleteTarget, setCatalogDeleteTarget] = useState<CatalogDeleteTarget | null>(null);
  const [archivePreview, setArchivePreview] = useState<TeacherBookArchiveImportPreview | null>(null);
  const [archiveName, setArchiveName] = useState("");
  const [archiveBase64, setArchiveBase64] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const pageRequestId = useRef(0);
  const editorRef = useRef<HTMLTextAreaElement>(null);

  const loadNavigation = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.getTeacherBookNavigation(workspaceId);
      setNavigation(result.items);
      setSelectedId((current) => current || result.items[0]?.knowledge_point_id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [workspaceId]);

  const loadPage = useCallback(async () => {
    const requestId = ++pageRequestId.current;
    const requestedId = selectedId;
    if (!requestedId) {
      setPage(null);
      return;
    }
    setError("");
    try {
      const result = await api.getTeacherBookPage(workspaceId, requestedId);
      if (requestId !== pageRequestId.current) return;
      setPage(result.page);
      setContent(result.page.draft_markdown);
      setImportPreview(null);
      setImportAssets([]);
      setEditorAssets([]);
      setEditorAssetPreviews({});
      setArchivePreview(null);
      setMessage("");
    } catch (reason) {
      if (requestId !== pageRequestId.current) return;
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [selectedId, workspaceId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadNavigation(), 0);
    return () => window.clearTimeout(timer);
  }, [loadNavigation]);
  useEffect(() => {
    const timer = window.setTimeout(() => void loadPage(), 0);
    return () => window.clearTimeout(timer);
  }, [loadPage]);

  const groups = useMemo<TeacherBookTreeGroup[]>(() => {
    const navigationGroups = groupNavigation(navigation);
    if (!catalogDraft) return navigationGroups;

    const navigationByTopic = new Map(navigationGroups.map((group) => [group.topicId, group]));
    return catalogDraft.topics.map((topic) => {
      const navigationGroup = navigationByTopic.get(topic.id);
      const navigationByPoint = new Map(navigationGroup?.items.map((item) => [item.knowledge_point_id, item]));
      return {
        topicId: topic.id,
        topicName: topic.name || "未命名主题",
        topicStatus: topic.status,
        items: topic.knowledge_points.map((point) => navigationByPoint.get(point.id) ?? {
          topic_id: topic.id,
          topic_name: topic.name || "未命名主题",
          knowledge_point_id: point.id,
          title: point.name || "未命名知识点",
          sort_order: point.sort_order,
          topic_status: topic.status,
          knowledge_point_status: point.status,
          has_draft: false,
          has_published: false,
          revision: 0,
          published_revision: null,
        }),
      };
    });
  }, [catalogDraft, navigation]);

  const filteredGroups = useMemo(() => {
    const query = directoryQuery.trim().toLocaleLowerCase();
    if (!query) return groups;
    return groups.map((group) => {
      const topicMatches = group.topicName.toLocaleLowerCase().includes(query);
      return topicMatches ? group : { ...group, items: group.items.filter((item) => item.title.toLocaleLowerCase().includes(query)) };
    }).filter((group) => group.topicName.toLocaleLowerCase().includes(query) || group.items.length > 0);
  }, [directoryQuery, groups]);

  const persistCatalog = useCallback(async (nextCatalog: TeacherCatalog, successMessage: string) => {
    setCatalogDraft(nextCatalog);
    onCatalogChange?.(nextCatalog);
    setDirectorySaving(true);
    setMessage("");
    try {
      const result = await api.updateTeacherCatalog(workspaceId, {
        topics: nextCatalog.topics,
        exercise_blueprints: nextCatalog.exercise_blueprints,
        review_blueprints: nextCatalog.review_blueprints,
        guided_blueprints: nextCatalog.guided_blueprints,
      });
      setCatalogDraft(result.catalog);
      onCatalogChange?.(result.catalog);
      setMessage(successMessage);
      await loadNavigation();
    } catch (reason) {
      setMessage(`教材目录保存失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setDirectorySaving(false);
    }
  }, [loadNavigation, onCatalogChange, workspaceId]);

  const addTopic = () => {
    if (!catalogDraft) return;
    setCatalogInput({ kind: "new-topic", title: "新建教材主题", description: "新增主题会直接写入当前教师课程目录。", label: "主题名称", value: "", placeholder: "例如：Transformer 基础", confirmLabel: "新增主题" });
  };

  const addKnowledgePoint = async (topicId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    if (!topic) return;
    const point = newKnowledgePoint("未命名知识点", topic.knowledge_points.length);
    setCollapsedTopicIds((current) => current.filter((id) => id !== topicId));
    setSelectedId(point.id);
    await persistCatalog(withTopicUpdate(catalogDraft, topicId, (current) => ({ ...current, knowledge_points: [...current.knowledge_points, point] })), "知识点已添加并保存。");
  };

  const editTopic = (topicId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    if (!topic) return;
    setCatalogInput({ kind: "edit-topic", topicId, title: "编辑教材主题", description: "修改主题名称后保存，学生端目录会同步更新。", label: "主题名称", value: topic.name, placeholder: "请输入主题名称", confirmLabel: "保存修改" });
  };

  const toggleTopic = async (topicId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    if (!topic) return;
    const nextStatus = topic.status === "enabled" ? "disabled" : "enabled";
    await persistCatalog(withTopicUpdate(catalogDraft, topicId, (current) => ({ ...current, status: nextStatus })), `主题已${nextStatus === "enabled" ? "启用" : "停用"}并保存。`);
  };

  const requestRemoveTopic = (topicId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    if (!topic) return;
    setCatalogDeleteTarget({ kind: "topic", topicId, name: topic.name || "未命名主题" });
  };

  const editKnowledgePoint = (topicId: string, pointId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    const point = topic?.knowledge_points.find((item) => item.id === pointId);
    if (!topic || !point) return;
    setCatalogInput({ kind: "edit-point", topicId, pointId, title: "编辑教材知识点", description: "修改知识点名称后保存，教师目录与学生教材入口会同步更新。", label: "知识点名称", value: point.name, placeholder: "请输入知识点名称", confirmLabel: "保存修改" });
  };

  const submitCatalogInput = async (value: string) => {
    const action = catalogInput;
    setCatalogInput(null);
    const name = value.trim();
    if (!action || !catalogDraft || !name) return;
    if (action.kind === "new-topic") {
      await persistCatalog({ ...catalogDraft, topics: [...catalogDraft.topics, { id: createUuid(), name, description: "", status: "enabled", knowledge_points: [] }] }, "主题已添加并保存。");
      return;
    }
    if (action.kind === "edit-topic") {
      const topic = catalogDraft.topics.find((item) => item.id === action.topicId);
      if (!topic || name === topic.name) return;
      await persistCatalog(withTopicUpdate(catalogDraft, action.topicId, (current) => ({ ...current, name })), "主题已更新并保存。");
      return;
    }
    const topic = catalogDraft.topics.find((item) => item.id === action.topicId);
    const point = topic?.knowledge_points.find((item) => item.id === action.pointId);
    if (!topic || !point || name === point.name) return;
    await persistCatalog(withTopicUpdate(catalogDraft, action.topicId, (current) => ({ ...current, knowledge_points: current.knowledge_points.map((item) => item.id === action.pointId ? { ...item, name } : item) })), "知识点已更新并保存。");
  };

  const requestRemoveKnowledgePoint = (topicId: string, pointId: string) => {
    if (!catalogDraft) return;
    const topic = catalogDraft.topics.find((item) => item.id === topicId);
    const point = topic?.knowledge_points.find((item) => item.id === pointId);
    if (!topic || !point) return;
    setCatalogDeleteTarget({ kind: "point", topicId, pointId, name: point.name || "未命名知识点" });
  };

  const confirmCatalogDelete = async () => {
    const target = catalogDeleteTarget;
    if (!target || !catalogDraft) return;
    setCatalogDeleteTarget(null);
    if (target.kind === "topic") {
      const topic = catalogDraft.topics.find((item) => item.id === target.topicId);
      if (!topic) return;
      if (topic.knowledge_points.some((point) => point.id === selectedId)) {
        setSelectedId("");
        setPage(null);
      }
      await persistCatalog({ ...catalogDraft, topics: catalogDraft.topics.filter((item) => item.id !== target.topicId) }, "主题已删除并保存。");
      return;
    }
    if (selectedId === target.pointId) {
      setSelectedId("");
      setPage(null);
    }
    await persistCatalog(withTopicUpdate(catalogDraft, target.topicId, (current) => ({ ...current, knowledge_points: current.knowledge_points.filter((item) => item.id !== target.pointId) })), "知识点已删除并保存。");
  };

  const toggleKnowledgePoint = async (topicId: string, pointId: string) => {
    if (!catalogDraft) return;
    await persistCatalog(withTopicUpdate(catalogDraft, topicId, (current) => ({ ...current, knowledge_points: current.knowledge_points.map((item) => item.id === pointId ? { ...item, status: item.status === "enabled" ? "disabled" : "enabled" } : item) })), "知识点状态已更新并保存。");
  };

  const closeTreeMenu = (event: MouseEvent<HTMLButtonElement>) => {
    event.currentTarget.closest("details")?.removeAttribute("open");
  };

  useEffect(() => {
    const closeOpenMenus = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      document.querySelectorAll<HTMLDetailsElement>(".teacher-book-tree-menu[open]").forEach((menu) => {
        if (!menu.contains(target)) menu.open = false;
      });
    };
    document.addEventListener("pointerdown", closeOpenMenus);
    return () => document.removeEventListener("pointerdown", closeOpenMenus);
  }, []);

  const headingIndex = useMemo(() => indexMarkdownHeadings(content), [content]);
  const save = async () => {
    if (!page || !selectedId) return;
    setSaving(true);
    setMessage("");
    try {
      const result = await api.updateTeacherBookPage(workspaceId, selectedId, content, page.revision, editorAssets);
      setPage(result.page);
      setContent(result.page.draft_markdown);
      setEditorAssets([]);
      const warningMessage = result.warnings.length > 0 ? `提示：${result.warnings.join("；")}` : "";
      setMessage(`草稿已保存。发布后学生才会看到新内容。${warningMessage ? ` ${warningMessage}` : ""}`);
      await loadNavigation();
    } catch (reason) {
      setMessage(`保存失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setSaving(false);
    }
  };

  const publish = async () => {
    if (!page || !selectedId) return;
    setSaving(true);
    setMessage("");
    try {
      let draft = page;
      let warnings: string[] = [];
      if (content !== page.draft_markdown || editorAssets.length > 0) {
        const saved = await api.updateTeacherBookPage(workspaceId, selectedId, content, page.revision, editorAssets);
        draft = saved.page;
        warnings = saved.warnings;
        setEditorAssets([]);
      }
      const result = await api.publishTeacherBookPage(workspaceId, selectedId, draft.revision);
      setPage(result.page);
      setContent(result.page.draft_markdown);
      const warningMessage = warnings.length > 0 ? `提示：${warnings.join("；")}` : "";
      setMessage(`教材已发布，学生端现在可以读取这一版正文。${warningMessage ? ` ${warningMessage}` : ""}`);
      await loadNavigation();
    } catch (reason) {
      setMessage(`发布失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setSaving(false);
    }
  };

  const applyFormat = (format: MarkdownFormat) => {
    const editor = editorRef.current;
    const result = formatMarkdownSelection(
      content,
      editor?.selectionStart ?? content.length,
      editor?.selectionEnd ?? content.length,
      format,
    );
    setContent(result.value);
    window.requestAnimationFrame(() => {
      editorRef.current?.focus();
      editorRef.current?.setSelectionRange(result.selectionStart, result.selectionEnd);
    });
  };

  const handleEditorKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const command = event.ctrlKey || event.metaKey;
    if (command && event.key.toLowerCase() === "b") {
      event.preventDefault();
      applyFormat("bold");
    } else if (command && event.key.toLowerCase() === "i") {
      event.preventDefault();
      applyFormat("italic");
    } else if (command && event.key.toLowerCase() === "k") {
      event.preventDefault();
      applyFormat("link");
    } else if (command && event.key.toLowerCase() === "s") {
      event.preventDefault();
      void save();
    } else if (event.key === "Tab") {
      event.preventDefault();
      const editor = editorRef.current;
      const start = editor?.selectionStart ?? content.length;
      const end = editor?.selectionEnd ?? content.length;
      const next = `${content.slice(0, start)}  ${content.slice(end)}`;
      setContent(next);
      window.requestAnimationFrame(() => {
        editorRef.current?.focus();
        editorRef.current?.setSelectionRange(start + 2, start + 2);
      });
    }
  };

  const markdownTools: Array<{ format: MarkdownFormat; label: string; icon: typeof Bold; shortcut?: string }> = [
    { format: "bold", label: "加粗", icon: Bold, shortcut: "Ctrl+B" },
    { format: "italic", label: "斜体", icon: Italic, shortcut: "Ctrl+I" },
    { format: "heading", label: "标题", icon: Heading2 },
    { format: "code", label: "代码块", icon: Code2 },
    { format: "link", label: "链接", icon: Link2, shortcut: "Ctrl+K" },
    { format: "list", label: "列表", icon: List },
    { format: "quote", label: "引用", icon: MessageSquareQuote },
  ];

  const handleFile = async (files: File[]) => {
    const markdownFiles = files.filter((file) => file.name.toLowerCase().endsWith(".md"));
    if (markdownFiles.length !== 1) {
      setMessage("请同时选择且只能选择一个 Markdown 文件；图片可一并选择。 ");
      return;
    }
    const file = markdownFiles[0];
    setMessage("");
    try {
      const assets = await Promise.all(files.filter((item) => item !== file).map(async (asset) => ({
        asset_path: assetPathForFile(asset),
        media_type: asset.type,
        content_base64: await fileToBase64(asset),
      })));
      const nextPreview = await api.previewTeacherBookImport(workspaceId, file.name, await file.text());
      setImportName(file.name);
      setImportAssets(assets);
      setImportPreview(nextPreview);
    } catch (reason) {
      setMessage(`导入预览失败：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  };

  const handleEditorAssets = async (files: File[]) => {
    if (files.length === 0) return;
    const editor = editorRef.current;
    const selectionStart = editor?.selectionStart ?? content.length;
    const selectionEnd = editor?.selectionEnd ?? selectionStart;
    setMessage("");
    try {
      const assets = await Promise.all(files.map(async (file) => ({
        asset_path: assetPathForFile(file),
        media_type: file.type,
        content_base64: await fileToBase64(file),
      })));
      const references = assets.map((asset) => `![${asset.asset_path.split("/").pop() ?? "图片"}](${asset.asset_path})`).join("\n\n");
      const insertion = insertImageReferences(content, selectionStart, selectionEnd, references);
      setContent(insertion.value);
      setEditorAssets((current) => {
        const byPath = new Map(current.map((asset) => [asset.asset_path, asset]));
        assets.forEach((asset) => byPath.set(asset.asset_path, asset));
        return Array.from(byPath.values());
      });
      setEditorAssetPreviews((current) => ({ ...current, ...Object.fromEntries(assets.map((asset) => [asset.asset_path, dataUrlForAsset(asset)])) }));
      window.requestAnimationFrame(() => {
        editorRef.current?.focus();
        editorRef.current?.setSelectionRange(insertion.selectionStart, insertion.selectionEnd);
      });
      setMessage(`已附加 ${assets.length} 个图片资源，保存草稿时会自动入库并重写 Markdown 图片地址。`);
    } catch (reason) {
      setMessage(`图片资源读取失败：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  };

  const handleArchiveFile = async (file: File | undefined) => {
    if (!file) return;
    setMessage("");
    try {
      const archive_base64 = await fileToBase64(file);
      const nextPreview = await api.previewTeacherBookArchiveImport(workspaceId, file.name, archive_base64);
      setArchiveName(file.name);
      setArchiveBase64(archive_base64);
      setArchivePreview(nextPreview);
    } catch (reason) {
      setMessage(`教材包预览失败：${reason instanceof Error ? reason.message : String(reason)}`);
    }
  };

  const applyImport = async () => {
    if (!page || !selectedId || !importPreview) return;
    setSaving(true);
    try {
      const result = await api.applyTeacherBookImport(workspaceId, selectedId, importName, importPreview.content_markdown, page.revision, importAssets);
      setPage(result.page);
      setContent(result.page.draft_markdown);
      setImportPreview(null);
      setImportAssets([]);
      setEditorAssets([]);
      setEditorAssetPreviews(Object.fromEntries(importAssets.map((asset) => [asset.asset_path, dataUrlForAsset(asset)])));
      setMessage("Markdown 已导入草稿，请检查预览后保存或发布。");
      await loadNavigation();
    } catch (reason) {
      setMessage(`导入失败：${reason instanceof Error ? reason.message : String(reason)}`);
    } finally {
      setSaving(false);
    }
  };

  const applyArchive = async () => {
    if (!archivePreview || !archiveBase64) return;
    setSaving(true);
    try {
      const expectedRevisions = Object.fromEntries(
        archivePreview.items.map((item) => [item.knowledge_point_id, item.expected_revision]),
      );
      const result = await api.applyTeacherBookArchiveImport(workspaceId, archiveName, archiveBase64, expectedRevisions);
      setArchivePreview(null);
      setArchiveBase64("");
      setMessage(`教材包已应用 ${result.applied_count} 个知识点草稿${result.asset_paths.length ? `，并保存 ${result.asset_paths.length} 个图片资源` : ""}。请逐页检查后发布。`);
      await loadNavigation();
      await loadPage();
    } catch (reason) {
      setMessage(`教材包应用失败：${reason instanceof Error ? `${reason.message}（请重新预览后再试）` : String(reason)}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading && navigation.length === 0) return <div className="teacher-state"><RefreshCw className="spin" />正在加载教材目录…</div>;
  if (error && navigation.length === 0) return <div className="teacher-state error"><AlertCircle /><strong>无法加载教材内容</strong><p>{error}</p></div>;

  return (
    <div className="teacher-book-editor">
      <section className="teacher-page-summary teacher-book-summary">
        <div><span className="teacher-eyebrow">KNOWLEDGE BOOK</span><h2>知识教材正文</h2><p>长篇 Markdown 正文独立于智能体提示词。教师保存草稿后，再明确发布给学生。</p></div>
        <BookOpenText size={46} />
      </section>
      <div className="teacher-book-toolbar">
        <label className="teacher-book-import"><Upload size={15} />导入 Markdown/图片<input type="file" multiple accept=".md,text/markdown,image/png,image/jpeg,image/webp,image/gif" onChange={(event) => { void handleFile(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} /></label>
        <label className="teacher-book-import"><Upload size={15} />附加编辑图片<input type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif" onChange={(event) => { void handleEditorAssets(Array.from(event.target.files ?? [])); event.currentTarget.value = ""; }} /></label>
        <label className="teacher-book-import"><Upload size={15} />导入教材包<input type="file" accept=".zip,application/zip" onChange={(event) => { void handleArchiveFile(event.target.files?.[0]); event.currentTarget.value = ""; }} /></label>
        <button type="button" onClick={() => void loadNavigation()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新目录</button>
        {message && <span role="status">{message}</span>}
      </div>
      <div className="teacher-book-import-states">
        {importPreview && <section className="teacher-book-import-preview" aria-label="Markdown 导入预览"><div><strong>{importName}</strong><span>{importPreview.removed_frameworks.length ? `已过滤：${importPreview.removed_frameworks.join("、")}` : "未发现需要过滤的框架代码"}</span>{importAssets.length > 0 && <small>将一并保存 {importAssets.length} 个图片资源</small>}{importPreview.warnings.map((warning) => <small key={warning}>{warning}</small>)}<details><summary>查看规范化后的 Markdown</summary><pre>{importPreview.content_markdown}</pre></details></div><button type="button" onClick={() => void applyImport()} disabled={saving}><FileUp size={15} />应用到当前草稿</button></section>}
        {archivePreview && <section className="teacher-book-import-preview teacher-book-archive-preview" aria-label="教材包导入预览">
        <div className="teacher-book-archive-summary"><strong>{archivePreview.title}</strong><span>{archiveName} · {archivePreview.items.length} 个待检查知识点 · {archivePreview.asset_paths.length} 个图片资源</span>{archivePreview.warnings.map((warning) => <small key={warning}>{warning}</small>)}</div>
        <div className="teacher-book-archive-items">{archivePreview.items.map((item) => <div className={`teacher-book-archive-item ${item.action}`} key={item.knowledge_point_id}><span className="teacher-book-archive-action">{item.action === "create" ? "新增草稿" : item.action === "update" ? "覆盖草稿" : "内容未变"}</span><strong>{item.title}</strong><small>{item.file_name} · 版本 {item.expected_revision}</small>{item.removed_frameworks.length > 0 && <small>已过滤：{item.removed_frameworks.join("、")}</small>}{item.warnings.map((warning) => <small key={warning}>{warning}</small>)}{item.action === "update" && <details><summary>查看前后内容</summary><div className="teacher-book-diff"><pre>{item.current_markdown}</pre><pre>{item.content_markdown}</pre></div></details>}</div>)}</div>
        {archivePreview.omitted_knowledge_points.length > 0 && <small>未包含的目录知识点不会被删除：{archivePreview.omitted_knowledge_points.length} 个</small>}
        <div className="teacher-book-archive-actions"><button type="button" onClick={() => setArchivePreview(null)} disabled={saving}>取消</button><button type="button" onClick={() => void applyArchive()} disabled={saving || (archivePreview.items.every((item) => item.action === "unchanged") && archivePreview.asset_paths.length === 0)}><FileUp size={15} />确认应用到草稿</button></div>
        </section>}
      </div>
      <div className={['teacher-book-layout', directoryCollapsed && 'directory-collapsed'].filter(Boolean).join(' ')}>
        <aside className={['teacher-book-tree', directoryCollapsed && 'collapsed'].filter(Boolean).join(' ')} aria-label="教材目录">
          <button type="button" className="teacher-book-tree-collapsed-toggle" aria-label="展开教材目录" onClick={() => setDirectoryCollapsed(false)}><PanelLeftOpen size={16} /></button>
          <div className="teacher-book-tree-heading"><div><strong>教材目录</strong><small>{groups.reduce((total, group) => total + group.items.length, 0)} 个知识点</small></div><div className="teacher-book-tree-actions"><details className="teacher-book-tree-menu"><summary aria-label="教材目录选项"><MoreHorizontal size={16} /></summary><div><button type="button" onClick={(event) => { closeTreeMenu(event); void addTopic(); }} disabled={!catalogDraft || directorySaving}><FolderPlus size={14} />新增主题</button></div></details><button type="button" aria-label="收起教材目录" onClick={() => setDirectoryCollapsed(true)}><PanelLeftClose size={15} /></button></div></div>
          <label className="teacher-book-tree-search"><Search size={15} /><input type="search" aria-label="搜索教材目录" value={directoryQuery} onChange={(event) => setDirectoryQuery(event.target.value)} placeholder="搜索主题或知识点" /></label>
          <div className="teacher-book-tree-groups">{filteredGroups.map((group) => {
            const topicExpanded = directoryQuery.trim().length > 0 || !collapsedTopicIds.includes(group.topicId);
            return <section className="teacher-book-tree-topic" key={group.topicId}>
              <div className="teacher-book-tree-topic-heading">
                <button type="button" className="teacher-book-topic-toggle" aria-label={`${topicExpanded ? "折叠" : "展开"}主题 ${group.topicName}`} aria-expanded={topicExpanded} onClick={() => setCollapsedTopicIds((current) => topicExpanded ? [...current, group.topicId] : current.filter((id) => id !== group.topicId))}><ChevronDown size={14} /><span>{group.topicName}</span><small>{group.items.length}</small></button>
                <details className="teacher-book-tree-menu"><summary aria-label={`${group.topicName}目录选项`}><MoreHorizontal size={16} /></summary><div>
                  <button type="button" onClick={(event) => { closeTreeMenu(event); void addKnowledgePoint(group.topicId); }} disabled={!catalogDraft || directorySaving}><Plus size={14} />新增知识点</button>
                  <button type="button" onClick={(event) => { closeTreeMenu(event); void editTopic(group.topicId); }} disabled={!catalogDraft || directorySaving}><Pencil size={14} />编辑主题</button>
                  <button type="button" onClick={(event) => { closeTreeMenu(event); void toggleTopic(group.topicId); }} disabled={!catalogDraft || directorySaving}>{group.topicStatus === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{group.topicStatus === "enabled" ? "停用主题" : "启用主题"}</button>
                  <button type="button" className="danger" onClick={(event) => { closeTreeMenu(event); requestRemoveTopic(group.topicId); }} disabled={!catalogDraft || directorySaving}><Trash2 size={14} />删除主题</button>
                </div></details>
              </div>
              {topicExpanded && <div className="teacher-book-tree-topic-items">{group.items.map((item) => <div className={`teacher-book-tree-point ${selectedId === item.knowledge_point_id ? "active" : ""}`} key={item.knowledge_point_id}>
                <button className="teacher-book-tree-point-main" aria-label={item.title} type="button" onClick={() => setSelectedId(item.knowledge_point_id)}><span>{item.title}</span>{item.knowledge_point_status === "disabled" ? <small>已停用</small> : item.has_published && <small>已发布</small>}</button>
                <details className="teacher-book-tree-menu"><summary aria-label={`${item.title}选项`}><MoreHorizontal size={15} /></summary><div>
                  <button type="button" onClick={(event) => { closeTreeMenu(event); void editKnowledgePoint(group.topicId, item.knowledge_point_id); }} disabled={!catalogDraft || directorySaving}><Pencil size={14} />编辑知识点</button>
                  <button type="button" onClick={(event) => { closeTreeMenu(event); void toggleKnowledgePoint(group.topicId, item.knowledge_point_id); }} disabled={!catalogDraft || directorySaving}>{item.knowledge_point_status === "enabled" ? <EyeOff size={14} /> : <Eye size={14} />}{item.knowledge_point_status === "enabled" ? "停用知识点" : "启用知识点"}</button>
                  <button type="button" className="danger" onClick={(event) => { closeTreeMenu(event); requestRemoveKnowledgePoint(group.topicId, item.knowledge_point_id); }} disabled={!catalogDraft || directorySaving}><Trash2 size={14} />删除知识点</button>
                </div></details>
              </div>)}</div>}
            </section>;
          })}</div>
          {!filteredGroups.length && <p className="teacher-empty-state">未找到匹配的主题或知识点。</p>}
          {!groups.length && <p className="teacher-empty-state">请通过目录选项新建主题，或在主题选项中添加知识点。</p>}
        </aside>
        <main className="teacher-book-workspace">
          {page ? <>
            <header className="teacher-book-page-heading"><div className="teacher-book-page-heading-info"><div className="teacher-book-page-breadcrumb"><span className="teacher-book-page-topic">{page.topic_name}</span><span className="teacher-book-page-chevron" aria-hidden="true">›</span><h3>{page.title}</h3></div><span className="teacher-book-version"><strong>草稿 v{page.revision}</strong><span aria-hidden="true">·</span><span>{page.published_revision != null ? `已发布 v${page.published_revision}` : "尚未发布"}</span></span></div><div className="teacher-book-page-actions"><button type="button" className={preview ? "active" : ""} onClick={() => setPreview((current) => !current)}><Eye size={15} />{preview ? "返回编辑" : "预览正文"}</button><button type="button" onClick={() => void save()} disabled={saving}><Save size={15} />保存草稿</button><button type="button" className="teacher-book-publish" onClick={() => void publish()} disabled={saving || !content.trim()}><Send size={15} />发布给学生</button></div></header>
            {editorAssets.length > 0 && <small className="teacher-book-editor-assets">已附加 {editorAssets.length} 个图片资源，保存时会写入当前知识点的教材资源。</small>}
            <nav className="teacher-book-heading-outline" aria-label="本页小标题"><strong>本页小标题</strong>{headingIndex.headings.length ? <div>{headingIndex.headings.map((heading) => <a className={`level-${heading.level}`} key={heading.id} href={`#${heading.id}`} onClick={() => setPreview(true)}>{heading.text}</a>)}</div> : <small>使用 Markdown 的 ## / ### 标题，学生页面右侧目录会自动同步。</small>}</nav>
            {preview ? <div className="teacher-book-preview"><MarkdownContent allowDataImages headingIds={headingIndex.headingIds}>{replaceLocalAssetReferences(content || "暂无内容", editorAssetPreviews)}</MarkdownContent></div> : <div className="teacher-book-source"><div className="teacher-book-markdown-toolbar" aria-label="Markdown 快捷工具栏"><span>Markdown 源码</span><div>{markdownTools.map(({ format, label, icon: Icon, shortcut }) => <button key={format} type="button" title={shortcut ? `${label}（${shortcut}）` : label} aria-label={label} onMouseDown={(event) => event.preventDefault()} onClick={() => applyFormat(format)}><Icon size={14} />{label}</button>)}</div><small>支持 Ctrl/Cmd+B、I、K、S</small></div><textarea ref={editorRef} className="teacher-book-textarea" aria-label="教材正文 Markdown" value={content} onChange={(event) => setContent(event.target.value)} onKeyDown={handleEditorKeyDown} placeholder="# 知识点标题\n\n在这里编写面向学生的长篇教材正文。代码块建议使用 ```python，并只保留 PyTorch 示例。" /></div>}
          </> : <div className="teacher-state"><BookOpenText /><p>选择一个知识点开始编写教材。</p></div>}
        </main>
      </div>
      {catalogInput && <TextInputDialog key={`${catalogInput.kind}-${catalogInput.topicId ?? ""}-${catalogInput.pointId ?? ""}`} open title={catalogInput.title} description={catalogInput.description} label={catalogInput.label} initialValue={catalogInput.value} placeholder={catalogInput.placeholder} confirmLabel={catalogInput.confirmLabel} onClose={() => setCatalogInput(null)} onConfirm={(value) => { void submitCatalogInput(value); }} />}
      {catalogDeleteTarget && <ConfirmDialog open title={`删除${catalogDeleteTarget.kind === "topic" ? "主题" : "知识点"}“${catalogDeleteTarget.name}”？`} description={catalogDeleteTarget.kind === "topic" ? "该主题及其知识点会从当前教材目录移除；已有学习记录不会受影响。" : "该知识点会从当前教材目录移除；已有教材版本和学习记录不会受影响。"} onClose={() => setCatalogDeleteTarget(null)} onConfirm={() => { void confirmCatalogDelete(); }} />}
    </div>
  );
}

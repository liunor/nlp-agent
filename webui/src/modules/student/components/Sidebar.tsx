import { Archive, BookOpen, FolderPlus,  Menu, MoreHorizontal, Pencil, Pin, Plus, Search, Settings, Trash2, UserRound, X } from "lucide-react";
import { useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import novaMarkUrl from "../../../../logo/nova-remove.png";

import { CategoryDialog } from "@/modules/student/components/CategoryDialog";
import { deriveTitle } from "@/platform/storage/learning-preferences";
import type { LearningPreferences, SessionLearningMeta, SessionSummary } from "@/shared/types";

export function Sidebar({ sessions, preferences, activeId, open, collapsed, connected, onClose, onCollapse, onExpand, onSelect, onCreate, onMeta, onAddCategory, onRenameCategory, onDeleteCategory, onDelete, onAccount, onSettings }: {
  sessions: SessionSummary[];
  preferences: LearningPreferences;
  activeId: string | null;
  open: boolean;
  collapsed: boolean;
  connected: boolean;
  onClose: () => void;
  onCollapse: () => void;
  onExpand: () => void;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onMeta: (id: string, patch: Partial<SessionLearningMeta>) => void;
  onAddCategory: (name: string) => string;
  onRenameCategory: (id: string, name: string) => void;
  onDeleteCategory: (id: string, name: string) => void;
  onDelete: (id: string, title: string) => void;
  onAccount: () => void;
  onSettings: () => void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [categoryDialogOpen, setCategoryDialogOpen] = useState(false);
  const visible = useMemo(() => sessions.filter((session) => {
    const meta = preferences.sessions[session.session_id];
    if (!!meta?.archived !== showArchived) return false;
    const title = meta?.title ?? deriveTitle(session.session_id);
    return title.toLowerCase().includes(query.toLowerCase());
  }), [preferences.sessions, query, sessions, showArchived]);
  const grouped = useMemo(() => {
    const pinned = visible
      .filter((session) => preferences.sessions[session.session_id]?.pinnedAt)
      .sort((first, second) => (
        (preferences.sessions[second.session_id]?.pinnedAt ?? 0)
        - (preferences.sessions[first.session_id]?.pinnedAt ?? 0)
      ));
    const itemsByCategory = new Map<string | undefined, SessionSummary[]>();
    const firstUnpinnedIndexByCategory = new Map<string | undefined, number>();
    for (const [index, session] of visible.entries()) {
      const meta = preferences.sessions[session.session_id];
      if (meta?.pinnedAt) continue;
      const categoryId = meta?.categoryId;
      if (!firstUnpinnedIndexByCategory.has(categoryId)) firstUnpinnedIndexByCategory.set(categoryId, index);
      const items = itemsByCategory.get(categoryId) ?? [];
      items.push(session);
      itemsByCategory.set(categoryId, items);
    }
    const regularGroups = [
      { key: "uncategorized", id: undefined, name: t("uncategorized"), items: itemsByCategory.get(undefined) ?? [], pinned: false },
      ...preferences.categories.map((category) => ({ key: category.id, id: category.id, name: category.name, items: itemsByCategory.get(category.id) ?? [], pinned: false })),
    ].filter((group) => group.items.length > 0 || !query && !showArchived);
    regularGroups.sort((first, second) => (
      (firstUnpinnedIndexByCategory.get(first.id) ?? Number.MAX_SAFE_INTEGER)
      - (firstUnpinnedIndexByCategory.get(second.id) ?? Number.MAX_SAFE_INTEGER)
    ));
    return [
      ...(pinned.length > 0 ? [{ key: "pinned", id: undefined, name: "置顶", items: pinned, pinned: true }] : []),
      ...regularGroups,
    ];
  }, [preferences.categories, preferences.sessions, query, showArchived, t, visible]);
  useEffect(() => {
    const closeOpenMenus = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;

      document
        .querySelectorAll<HTMLDetailsElement>(
          ".category-menu[open], .session-menu[open]",
        )
        .forEach((menu) => {
          if (!menu.contains(target)) menu.open = false;
        });
    };

    document.addEventListener("pointerdown", closeOpenMenus);
    return () => document.removeEventListener("pointerdown", closeOpenMenus);
  }, []);
  const createCategory = () => setCategoryDialogOpen(true);
  const expandFromCollapsedRail = (event: MouseEvent<HTMLElement>) => {
    if (!collapsed || event.target !== event.currentTarget) return;
    event.preventDefault();
    event.stopPropagation();
    onExpand();
  };

  return <>
    {open && <button className="sidebar-backdrop" type="button" aria-label="关闭侧栏" onClick={onClose} />}
    <aside className={`sidebar ${open ? "open" : ""} ${collapsed ? "collapsed" : ""}`} onClickCapture={expandFromCollapsedRail}>
      <div className="sidebar-brand">
        <button className="brand-mark" type="button" aria-label={collapsed ? "展开侧栏" : "Nova NLP 学习助手"} onClick={collapsed ? onExpand : undefined}><img src={novaMarkUrl} alt="" /></button>
        {!collapsed && <><span className="sidebar-brand-copy"><strong>Nova</strong><small>LSNU NLP Learning Agent</small></span><button className="icon-button collapse-button" type="button" aria-label="折叠侧栏" onClick={onCollapse}><Menu size={16} /></button><button className="mobile-only icon-button" type="button" onClick={onClose}><X size={18} /></button></>}
      </div>
      <nav className="sidebar-actions">
        <SideAction collapsed={collapsed} label={t("newChat")} icon={<Plus size={18} />} onClick={onCreate} />
        <SideAction collapsed={collapsed} label={t("newCategory")} icon={<FolderPlus size={18} />} onClick={() => { if (collapsed) onExpand(); createCategory(); }} />
        <SideAction collapsed={collapsed} label={t("search")} icon={<Search size={18} />} onClick={() => { if (collapsed) onExpand(); setSearchOpen((value) => !value); }} />
        {!!sessions.some((session) => preferences.sessions[session.session_id]?.archived) && <SideAction collapsed={collapsed} label={showArchived ? "返回最近对话" : "归档对话"} icon={<Archive size={18} />} onClick={() => { if (collapsed) onExpand(); setShowArchived((value) => !value); }} />}
      </nav>
      {!collapsed && searchOpen && <div className="search-box"><Search size={15} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索历史问题" /></div>}
      <div className="session-scroll">
        {!collapsed && grouped.map((group) => <section className="session-group" key={group.key}>
          <h3>
            <span>{group.pinned ? <Pin size={13} /> : <BookOpen size={13} />}{group.name}</span>
            {group.id && <details className="category-menu">
              <summary aria-label={`${group.name} 分类菜单`}><MoreHorizontal size={14} /></summary>
              <div>
                <button type="button" onClick={() => {
                  const name = prompt("重命名分类", group.name);
                  if (name?.trim()) onRenameCategory(group.id!, name.trim());
                }}><Pencil size={13} />重命名</button>
                <button className="danger" type="button" onClick={() => onDeleteCategory(group.id!, group.name)}><Trash2 size={13} />删除分类</button>
              </div>
            </details>}
          </h3>
          {group.items.map((session) => {
            const meta = preferences.sessions[session.session_id] ?? {};
            return <div className={`session-item ${activeId === session.session_id ? "active" : ""}`} key={session.session_id}>
              <button
  className="session-main"
  type="button"
  onClick={() => {
    onSelect(session.session_id);
    onClose();
  }}
>
  <span>{meta.title ?? "新的学习对话"}</span>
</button>
              <button
  type="button"
  className={`session-pin ${meta.pinnedAt ? "active" : ""}`}
  aria-label={meta.pinnedAt ? "取消置顶" : "置顶对话"}
  aria-pressed={Boolean(meta.pinnedAt)}
  title={meta.pinnedAt ? "取消置顶" : "置顶对话"}
  onClick={() =>
    onMeta(session.session_id, {
      pinnedAt: meta.pinnedAt ? undefined : Date.now(),
    })
  }
>
  <Pin size={14} />
</button>
              <details className="session-menu"><summary aria-label="会话菜单"><MoreHorizontal size={16} /></summary><div>
                <button type="button" onClick={() => { const title = prompt("重命名学习对话", meta.title ?? ""); if (title?.trim()) onMeta(session.session_id, { title: title.trim() }); }}><Pencil size={14} />重命名</button>
                <button type="button" onClick={() => onMeta(session.session_id, { pinnedAt: meta.pinnedAt ? undefined : Date.now() })}><Pin size={14} />{meta.pinnedAt ? "取消置顶" : "置顶"}</button>
                <button type="button" onClick={() => onMeta(session.session_id, { archived: !meta.archived })}><Archive size={14} />{meta.archived ? "移出归档" : "归档"}</button>
                <div className="session-category-actions"><span>移动到分类</span><button type="button" onClick={() => onMeta(session.session_id, { categoryId: undefined })}>未分类</button>{preferences.categories.map((category) => <button key={category.id} type="button" onClick={() => onMeta(session.session_id, { categoryId: category.id })}>{category.name}</button>)}</div>
                <button className="danger" type="button" onClick={() => onDelete(session.session_id, meta.title ?? "新的学习对话")}><Trash2 size={14} />删除</button>
              </div></details>
            </div>;
          })}
        </section>)}
        {!collapsed && !visible.length && <p className="sidebar-empty">{showArchived ? "暂无归档对话" : "还没有学习记录"}</p>}
      </div>
      <div className="sidebar-footer"><SideAction collapsed={collapsed} label={t("settings")} icon={<Settings size={18} />} onClick={onSettings} /><SideAction collapsed={collapsed} label="账户管理" icon={<UserRound size={18} />} onClick={onAccount} /><i className={`connection-dot ${connected ? "online" : ""}`} title={connected ? "已连接" : "连接中"} /></div>
    </aside>
    <CategoryDialog open={categoryDialogOpen} onClose={() => setCategoryDialogOpen(false)} onConfirm={onAddCategory} />
  </>;
}

function SideAction({ collapsed, label, icon, onClick }: { collapsed: boolean; label: string; icon: ReactNode; onClick: () => void }) {
  return <button type="button" className="side-action" title={collapsed ? label : undefined} aria-label={label} onClick={onClick}><span>{icon}</span>{!collapsed && <b>{label}</b>}</button>;
}

export function SidebarToggle({ onClick }: { onClick: () => void }) {
  return <button className="icon-button sidebar-toggle" type="button" onClick={onClick} aria-label="打开侧栏"><Menu size={18} /></button>;
}

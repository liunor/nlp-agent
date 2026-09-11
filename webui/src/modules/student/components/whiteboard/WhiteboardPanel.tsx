import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ExcalidrawAdapter, type ExcalidrawSceneChange, type WhiteboardLibraryLoadError } from "./ExcalidrawAdapter";
import {
  readWhiteboardScene,
  serializeWhiteboardScene,
  sanitizeWhiteboardScene,
  writeWhiteboardScene,
  type StoredWhiteboardScene,
} from "./storage";

const LOCAL_SAVE_DEBOUNCE_MS = 250;

export interface WhiteboardPanelProps {
  userId: string | null;
  canManageLibrary?: boolean;
  /** Exposes the structured scene to page-level business actions. */
  onSceneChange?: (scene: StoredWhiteboardScene) => void;
}

/**
 * The whiteboard deliberately owns no business state. It only adapts the
 * embedded Excalidraw scene to per-user browser storage.
 */
export function WhiteboardPanel({ userId, canManageLibrary = false, onSceneChange }: WhiteboardPanelProps) {
  if (!userId) {
    return <div className="whiteboard-shell whiteboard-auth-required" role="status">请登录后使用白板。</div>;
  }

  return <AuthenticatedWhiteboardPanel key={userId} userId={userId} canManageLibrary={canManageLibrary} onSceneChange={onSceneChange} />;
}

function AuthenticatedWhiteboardPanel({ userId, canManageLibrary = false, onSceneChange }: WhiteboardPanelProps & { userId: string }) {
  const initialScene = useMemo<StoredWhiteboardScene | null>(() => {
    const scene = userId ? readWhiteboardScene(userId) : null;
    return scene ? sanitizeWhiteboardScene(scene) : null;
  }, [userId]);
  const latestScene = useRef<StoredWhiteboardScene | null>(initialScene);
  const saveTimer = useRef<number | null>(null);
  const [saveErrorUserId, setSaveErrorUserId] = useState<string | null>(null);
  const [libraryLoadError, setLibraryLoadError] = useState<WhiteboardLibraryLoadError | null>(null);
  const [libraryPublishError, setLibraryPublishError] = useState<string | null>(null);

  useEffect(() => {
    latestScene.current = initialScene;
  }, [initialScene]);

  useEffect(() => {
    if (initialScene) onSceneChange?.(initialScene);
  }, [initialScene, onSceneChange]);

  const persistLatestScene = useCallback((notify: boolean) => {
    if (!userId || !latestScene.current) return;
    const persisted = writeWhiteboardScene(userId, latestScene.current);
    if (notify) setSaveErrorUserId(persisted ? null : userId);
  }, [userId]);

  useEffect(() => {
    const flushLatestScene = () => {
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      persistLatestScene(true);
    };

    window.addEventListener("pagehide", flushLatestScene);
    return () => {
      window.removeEventListener("pagehide", flushLatestScene);
      if (saveTimer.current !== null) {
        window.clearTimeout(saveTimer.current);
        saveTimer.current = null;
      }
      persistLatestScene(false);
    };
  }, [persistLatestScene]);

  const handleChange = useCallback((
    scene: ExcalidrawSceneChange,
  ) => {
    const serializedScene = serializeWhiteboardScene(scene.elements, scene.appState, scene.files);
    latestScene.current = serializedScene;
    onSceneChange?.(serializedScene);
    if (!userId) return;
    if (saveTimer.current !== null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      saveTimer.current = null;
      persistLatestScene(true);
    }, LOCAL_SAVE_DEBOUNCE_MS);
  }, [onSceneChange, persistLatestScene, userId]);

  return <div className="whiteboard-shell">
    <ExcalidrawAdapter
      key={userId}
      initialScene={initialScene}
      onChange={handleChange}
      canManageLibrary={canManageLibrary}
      onLibraryLoadError={setLibraryLoadError}
      onLibraryPublishError={setLibraryPublishError}
    />
    {libraryLoadError && <div className="whiteboard-library-warning" role="status">
      {libraryLoadError.clearFailed
        ? "白板素材区初始化失败，已尽力恢复，请刷新白板后重试。"
        : libraryLoadError.sharedFailed
          ? "共享素材加载失败，请刷新白板后重试。"
          : "部分教学素材加载失败，请刷新白板后重试。"}
    </div>}
    {libraryPublishError && <div className="whiteboard-library-warning" role="alert">{libraryPublishError}</div>}
    {userId !== null && saveErrorUserId === userId && <div className="whiteboard-save-warning" role="alert">本地保存失败，请导出白板文件备份。</div>}
  </div>;
}

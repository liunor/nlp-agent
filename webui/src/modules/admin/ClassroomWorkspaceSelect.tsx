import { useEffect, useState } from "react";
import { api } from "@/platform/http/api";

type WorkspaceOption = { id: string; name: string };

export function ClassroomWorkspaceSelect({ workspaceId, value, onChange }: {
  workspaceId?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [options, setOptions] = useState<WorkspaceOption[]>([]);
  useEffect(() => {
    let cancelled = false;
    if (typeof api.listWorkspaces !== "function") return;
    void api.listWorkspaces().then((result) => {
      if (!cancelled) setOptions(result.workspaces);
    }).catch(() => { /* The selected workspace remains usable if names are unavailable. */ });
    return () => { cancelled = true; };
  }, []);

  return <label className="text-sm text-gray-600">
    工作区
    <select aria-label="工作区" value={value} onChange={(event) => onChange(event.target.value)} disabled={Boolean(workspaceId)} className="mt-1 block w-full rounded border border-gray-300 px-3 py-2 disabled:bg-gray-100">
      {!value && <option value="">选择工作区</option>}
      {options.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
      {value && !options.some((option) => option.id === value) && <option value={value}>当前工作区</option>}
    </select>
  </label>;
}

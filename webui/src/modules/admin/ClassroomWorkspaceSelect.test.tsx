import { render, screen } from "@testing-library/react";
import { ClassroomWorkspaceSelect } from "./ClassroomWorkspaceSelect";
const { listWorkspaces } = vi.hoisted(() => ({ listWorkspaces: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: { listWorkspaces } }));
it("shows the current workspace name without exposing its internal identifier", async () => {
  listWorkspaces.mockResolvedValue({ workspaces: [{ id: "workspace-uuid", name: "自然语言处理" }] });
  render(<ClassroomWorkspaceSelect workspaceId="workspace-uuid" value="workspace-uuid" onChange={vi.fn()} />);
  expect(await screen.findByRole("option", { name: "自然语言处理" })).toBeVisible();
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.queryByText("workspace-uuid")).not.toBeInTheDocument();
});

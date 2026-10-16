import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ClassroomManagementPage } from "./ClassroomManagementPage";

const mocks = vi.hoisted(() => ({
  listClassrooms: vi.fn(),
  listJoinRequests: vi.fn(),
  getClassroomJoinCode: vi.fn(),
  approveJoinRequest: vi.fn(),
  rejectJoinRequest: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: {
    listClassrooms: mocks.listClassrooms,
    listJoinRequests: mocks.listJoinRequests,
    getClassroomJoinCode: mocks.getClassroomJoinCode,
    approveJoinRequest: mocks.approveJoinRequest,
    rejectJoinRequest: mocks.rejectJoinRequest,
  },
}));

describe("ClassroomManagementPage", () => {
  beforeEach(() => {
    vi.stubGlobal("queueMicrotask", (callback: () => void) => callback());
    mocks.listClassrooms.mockReset().mockResolvedValue({ items: [{ id: "class-1", workspace_id: "w1", name: "高一语文", status: "active" }] });
    const pending = { id: "request-1", class_id: "class-1", class_name: "高一语文", user_id: "student-1", user_name: "student", display_name: "Student", student_number: "001", status: "pending", requested_at: "2026-10-15T00:00:00Z", reviewed_at: null, reviewed_by: null };
    let requestListCalls = 0;
    mocks.listJoinRequests.mockReset().mockImplementation(async () => {
      requestListCalls += 1;
      return requestListCalls === 1 ? { items: [pending], total: 1 } : { items: [], total: 0 };
    });
    mocks.getClassroomJoinCode.mockReset().mockResolvedValue({ classroom_id: "class-1", classroom_name: "高一语文", join_code: "ABCD2345" });
    mocks.approveJoinRequest.mockReset().mockResolvedValue({});
    mocks.rejectJoinRequest.mockReset().mockResolvedValue({});
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows and copies a class code, then refreshes after approval", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<ClassroomManagementPage />);

    await waitFor(() => expect(mocks.listClassrooms).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /高一语文/ }));
    expect(await screen.findByText("ABCD2345")).toBeVisible();
    expect(await screen.findByRole("button", { name: "通过" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "复制班级码" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("ABCD2345"));
    expect(screen.getByRole("status")).toHaveTextContent("班级码已复制");

    fireEvent.click(screen.getByRole("button", { name: "通过" }));
    await waitFor(() => expect(mocks.approveJoinRequest).toHaveBeenCalledWith("class-1", "request-1"));
    expect(await screen.findByText("申请已通过")).toBeVisible();
    expect(mocks.listJoinRequests).toHaveBeenCalledTimes(2);
  });

  it("does not let a slower classroom response overwrite the selected classroom", async () => {
    const requestsByClassroom = new Map<string, { resolve: (value: { items: never[]; total: number }) => void }>();
    mocks.listClassrooms.mockResolvedValue({ items: [
      { id: "class-a", workspace_id: "w1", name: "班级 A", status: "active" },
      { id: "class-b", workspace_id: "w1", name: "班级 B", status: "active" },
    ] });
    mocks.listJoinRequests.mockImplementation((classroomId: string) => new Promise((resolve) => { requestsByClassroom.set(classroomId, { resolve }); }));
    mocks.getClassroomJoinCode.mockImplementation(async (classroomId: string) => ({ classroom_id: classroomId, classroom_name: classroomId, join_code: classroomId === "class-a" ? "AAAA1111" : "BBBB2222" }));
    render(<ClassroomManagementPage workspaceId="w1" />);

    fireEvent.click(await screen.findByRole("button", { name: /班级 A/ }));
    fireEvent.click(await screen.findByRole("button", { name: /班级 B/ }));
    requestsByClassroom.get("class-b")?.resolve({ items: [], total: 0 });
    await waitFor(() => expect(screen.getByText("BBBB2222")).toBeVisible());
    requestsByClassroom.get("class-a")?.resolve({ items: [], total: 0 });
    await waitFor(() => expect(screen.queryByText("AAAA1111")).not.toBeInTheDocument());
    expect(screen.getByText("BBBB2222")).toBeVisible();
  });

  it("rejects an application, refreshes the list, and shows a success message", async () => {
    render(<ClassroomManagementPage />);

    await waitFor(() => expect(mocks.listClassrooms).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole("button", { name: /高一语文/ }));
    expect(await screen.findByRole("button", { name: "拒绝" })).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "拒绝" }));
    await waitFor(() => expect(mocks.rejectJoinRequest).toHaveBeenCalledWith("class-1", "request-1"));
    expect(await screen.findByText("申请已拒绝")).toBeVisible();
    expect(mocks.listJoinRequests).toHaveBeenCalledTimes(2);
  });
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ClassroomSettingsSection } from "./ClassroomSettingsSection";

const { listMyJoinRequestsMock, submitClassroomJoinRequestMock } = vi.hoisted(() => ({
  listMyJoinRequestsMock: vi.fn(),
  submitClassroomJoinRequestMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  ApiError: class MockApiError extends Error {
    status = 0;
    code?: string;
  },
  api: {
    listMyJoinRequests: listMyJoinRequestsMock,
    submitClassroomJoinRequest: submitClassroomJoinRequestMock,
  },
}));

describe("ClassroomSettingsSection", () => {
  beforeEach(() => {
    listMyJoinRequestsMock.mockReset().mockResolvedValue({ items: [], total: 0 });
    submitClassroomJoinRequestMock.mockReset().mockResolvedValue({
      id: "request-1",
      class_id: "class-1",
      class_name: "高一语文",
      user_id: "student-1",
      user_name: "student",
      display_name: "Student",
      student_number: null,
      status: "pending",
      requested_at: "2026-10-15T00:00:00Z",
      reviewed_at: null,
      reviewed_by: null,
    });
  });

  it("loads requests and submits a normalized short code", async () => {
    render(<ClassroomSettingsSection canSubmit />);

    expect(await screen.findByText("暂无入班申请。")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText("例如 ABCD2345"), { target: { value: " abcd2345 " } });
    fireEvent.click(screen.getByRole("button", { name: "申请入班" }));

    await waitFor(() => expect(submitClassroomJoinRequestMock).toHaveBeenCalledWith({ join_code: "ABCD2345" }));
    expect(await screen.findByText("申请已提交，请等待教师审核")).toBeVisible();
  });

  it("maps duplicate pending applications to an actionable message", async () => {
    submitClassroomJoinRequestMock.mockRejectedValueOnce({ code: "class_join_request_pending" });
    render(<ClassroomSettingsSection canSubmit />);

    expect(await screen.findByText("暂无入班申请。")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText("例如 ABCD2345"), { target: { value: "ABCD2345" } });
    fireEvent.click(screen.getByRole("button", { name: "申请入班" }));

    await waitFor(() => expect(submitClassroomJoinRequestMock).toHaveBeenCalled());
    expect(await screen.findByRole("alert")).toHaveTextContent("已申请该班级，请等待审核");
  });

  it("explains when the current user is already a classroom member", async () => {
    submitClassroomJoinRequestMock.mockRejectedValueOnce({ code: "class_join_member_exists" });
    render(<ClassroomSettingsSection canSubmit />);

    expect(await screen.findByText("暂无入班申请。")).toBeVisible();
    fireEvent.change(screen.getByPlaceholderText("例如 ABCD2345"), { target: { value: "ABCD2345" } });
    fireEvent.click(screen.getByRole("button", { name: "申请入班" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("你已经加入该班级，无需重复申请");
  });
});

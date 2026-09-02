import { render, screen } from "@testing-library/react";

import { ClassroomQuotaPage } from "./TeacherWorkspace";

const { listClassroomsMock, getTeacherClassroomUsageMock } = vi.hoisted(() => ({
  listClassroomsMock: vi.fn(),
  getTeacherClassroomUsageMock: vi.fn(),
}));

vi.mock("@/platform/http/api", () => ({
  api: { listClassrooms: listClassroomsMock, getTeacherClassroomUsage: getTeacherClassroomUsageMock },
}));

describe("ClassroomQuotaPage", () => {
  it("loads a classroom aggregate and identifies pending usage", async () => {
    listClassroomsMock.mockResolvedValue({ items: [{ id: "class-a", workspace_id: "default", name: "高一 A 班", status: "active" }] });
    getTeacherClassroomUsageMock.mockResolvedValue({
      classroom_id: "class-a",
      from: "2026-08-01T00:00:00+00:00",
      to: "2026-08-30T00:00:00+00:00",
      students: 2,
      active_student_ids: ["student-a", "student-b"],
      events: 4,
      priced_events: 3,
      priced_credits_micro: 1250000,
      pending_events: 1,
      unavailable_events: 0,
      tokens: { total_tokens: 900 },
      by_user: [{ user_id: "student-a", events: 3, priced_credits_micro: 1000000, pending_events: 1, unavailable_events: 0, total_tokens: 600 }],
    });

    render(<ClassroomQuotaPage workspaceId="default" />);

    expect(await screen.findByRole("heading", { name: "班级额度用量" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "选择班级" })).toHaveValue("class-a");
    expect(await screen.findByText("1.25 credits")).toBeVisible();
    expect(screen.getByText("有 1 条用量待对账")).toBeVisible();
    expect(getTeacherClassroomUsageMock).toHaveBeenCalledWith("class-a", "default", 30);
  });
});

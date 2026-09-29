import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ReleaseNotes } from "./DeveloperWorkspace";

const { listMock, createMock, updateMock, deleteMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  createMock: vi.fn(),
  updateMock: vi.fn(),
  deleteMock: vi.fn(),
}));
vi.mock("@/platform/http/api", () => ({
  api: {
    listReleaseNotes: listMock,
    createReleaseNote: createMock,
    updateReleaseNote: updateMock,
    deleteReleaseNote: deleteMock,
  },
}));

const published = { id: "n1", version: "1.0.0", released_at: "2026-08-01T00:00:00", notes: ["新增发布说明功能", "优化设置页"], status: "published" as const };
const draft = { id: "n2", version: "1.1.0", released_at: "2026-08-13T00:00:00", notes: ["草稿条目"], status: "draft" as const };

describe("ReleaseNotes", () => {
  beforeEach(() => {
    listMock.mockReset();
    createMock.mockReset();
    updateMock.mockReset();
    deleteMock.mockReset();
    listMock.mockResolvedValue({ items: [published, draft] });
    createMock.mockResolvedValue(published);
    updateMock.mockResolvedValue(draft);
    deleteMock.mockResolvedValue(undefined);
    vi.stubGlobal("confirm", vi.fn(() => true));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists published and draft release notes", async () => {
    render(<ReleaseNotes />);

    expect(await screen.findByText("v1.0.0")).toBeVisible();
    expect(screen.getByText("v1.1.0")).toBeVisible();
    expect(screen.getAllByText("已发布").length).toBeGreaterThan(0);
    expect(screen.getAllByText("草稿").length).toBeGreaterThan(0);
    expect(listMock).toHaveBeenCalledOnce();
  });

  it("collapses release history into accessible expandable entries", async () => {
    render(<ReleaseNotes />);

    await screen.findByText("v1.0.0");
    const entries = document.querySelectorAll("details.developer-release-card");

    expect(entries).toHaveLength(2);
    expect((entries[0] as HTMLDetailsElement).open).toBe(true);
    expect((entries[1] as HTMLDetailsElement).open).toBe(false);

    fireEvent.click(entries[0].querySelector("summary") as HTMLElement);
    expect((entries[0] as HTMLDetailsElement).open).toBe(false);
  });

  it("starts a new release note with today's date", async () => {
    render(<ReleaseNotes />);

    const today = new Date();
    const expected = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    expect(await screen.findByLabelText("发布日期")).toHaveValue(expected);
  });

  it("creates a note from the form, trimming blank lines", async () => {
    render(<ReleaseNotes />);
    await screen.findByText("v1.0.0");

    fireEvent.change(screen.getByPlaceholderText("版本，例如 1.0.0"), { target: { value: "2.0.0" } });
    fireEvent.change(screen.getByLabelText("发布日期"), { target: { value: "2026-08-13" } });
    fireEvent.change(screen.getByPlaceholderText("每行一条更新与修复说明"), { target: { value: " 第一条 \n\n第二条 " } });
    fireEvent.click(screen.getByRole("button", { name: "新建发布说明" }));

    await waitFor(() => expect(createMock).toHaveBeenCalledWith({
      version: "2.0.0",
      released_at: "2026-08-13",
      notes: ["第一条", "第二条"],
      status: "published",
    }));
    expect(listMock).toHaveBeenCalledTimes(2);
  });

  it("starts editing an existing note and saves through update", async () => {
    render(<ReleaseNotes />);
    await screen.findByText("v1.0.0");

    fireEvent.click(screen.getAllByRole("button", { name: "编辑" })[0]);
    fireEvent.change(screen.getByPlaceholderText("每行一条更新与修复说明"), { target: { value: "改过的说明" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledWith("n1", {
      version: "1.0.0",
      released_at: "2026-08-01",
      notes: ["改过的说明"],
      status: "published",
    }));
  });

  it("deletes a note after confirmation", async () => {
    render(<ReleaseNotes />);
    await screen.findByText("v1.0.0");

    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);

    await waitFor(() => expect(deleteMock).toHaveBeenCalledWith("n1"));
    expect(confirm).toHaveBeenCalled();
  });

  it("shows an empty state when there are no notes", async () => {
    listMock.mockResolvedValue({ items: [] });
    render(<ReleaseNotes />);

    expect(await screen.findByText("暂无发布说明")).toBeVisible();
  });

});

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QuestionActivityPanel } from "./QuestionActivityPanel";
const { read } = vi.hoisted(() => ({ read: vi.fn() }));
vi.mock("@/platform/http/api", () => ({ api: { getTeacherQuestionActivity: read } }));
beforeEach(() => {
  read.mockReset().mockResolvedValue({ summary:{question_count:3,user_count:2}, start_date:"2026-11-01",end_date:"2026-11-19",timezone:"Asia/Shanghai", total:3, items:[{id:"q1",user_id:"u1",username:"student",display_name:"张三",question:"什么是注意力？",created_at:"2026-11-19T12:00:00+08:00"}],frequent_total:1,frequent:[{group_id:"abc",question:"什么是注意力？",user_count:2,question_count:3}] });
});
it("queries the applied date range and current classroom, and filters original questions", async () => {
  render(<QuestionActivityPanel workspaceId="w1" classroomId="c1" />);
  const details=screen.getByRole("region",{name:"提问明细"});
  expect(await within(details).findByText("什么是注意力？")).toBeVisible();
  fireEvent.change(screen.getByLabelText("提问开始日期"),{target:{value:"2026-11-01"}});
  fireEvent.change(screen.getByLabelText("提问结束日期"),{target:{value:"2026-11-19"}});
  fireEvent.click(screen.getByRole("button",{name:"查询"}));
  await waitFor(() => expect(read.mock.lastCall?.[0].get("start_date")).toBe("2026-11-01"));
  expect(read.mock.lastCall?.[0].get("classroom_id")).toBe("c1");
  expect(await within(details).findByText("张三")).toBeVisible();
  fireEvent.click(screen.getByRole("button",{name:"查看原始提问"}));
  await waitFor(() => expect(read.mock.lastCall?.[0].get("group_id")).toBe("abc"));
  expect(read.mock.lastCall?.[0].get("start_date")).toBe("2026-11-01");
});
it("rejects a reversed date range without sending a new request", async () => {
  render(<QuestionActivityPanel workspaceId="w1" />);
  expect(await screen.findByText("3 次提问",{exact:false})).toBeVisible();
  const count=read.mock.calls.length;
  fireEvent.change(screen.getByLabelText("提问开始日期"),{target:{value:"2026-11-20"}});
  fireEvent.change(screen.getByLabelText("提问结束日期"),{target:{value:"2026-11-01"}});
  fireEvent.click(screen.getByRole("button",{name:"查询"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("有效时间段");
  expect(read).toHaveBeenCalledTimes(count);
  expect(within(screen.getByRole("region",{name:"提问明细"})).getByText("什么是注意力？")).toBeVisible();
});

it("returns to a valid page when the result set shrinks", async () => {
  const original = await read();
  let shrunk=false;
  read.mockImplementation(async (params: URLSearchParams) => {
    if(params.get("page")==="3") shrunk=true;
    return { ...original,total:shrunk ? 1 : 21 };
  });
  render(<QuestionActivityPanel workspaceId="w1" />);
  const details=screen.getByRole("region",{name:"提问明细"});
  expect(await within(details).findByText("1 / 3")).toBeVisible();
  fireEvent.click(within(details).getByRole("button",{name:"下一页"}));
  expect(await within(details).findByText("2 / 3")).toBeVisible();
  await waitFor(() => expect(within(details).getByRole("button",{name:"下一页"})).not.toBeDisabled());
  fireEvent.click(within(details).getByRole("button",{name:"下一页"}));
  expect(await within(details).findByText("1 / 1")).toBeVisible();
  expect(read.mock.lastCall?.[0].get("page")).toBe("1");
});

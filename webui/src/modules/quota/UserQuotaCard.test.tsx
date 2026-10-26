import { act, render, screen, waitFor } from "@testing-library/react";
import { UserQuotaCard } from "./UserQuotaCard";

const getUserQuota = vi.hoisted(() => vi.fn());
vi.mock("@/platform/http/api", () => ({ api: { getUserQuota } }));

it("ignores the previous user's delayed balance response", async () => {
  let resolveA!: (value: unknown) => void;
  const empty = { quota: { user_id: "b", workspace_id: null, buckets: [] }, policy: null };
  getUserQuota.mockImplementation((id) => id === "a" ? new Promise((resolve) => { resolveA = resolve; }) : Promise.resolve(empty));
  const view = render(<UserQuotaCard userId="a" />);
  view.rerender(<UserQuotaCard userId="b" />);
  await waitFor(() => expect(getUserQuota).toHaveBeenCalledWith("b"));
  await act(async () => resolveA({ ...empty, policy: { base: { code: "A-private-policy", version: "1" } } }));
  expect(screen.queryByText(/A-private-policy/)).not.toBeInTheDocument();
  expect(await screen.findByText("尚无本周期用量记录")).toBeInTheDocument();
});

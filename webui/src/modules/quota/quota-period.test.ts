import { quotaPeriodStart } from "./QuotaManagementPageRouted";

it("uses UTC days and Monday weeks regardless of the browser timezone", () => {
  const date = new Date("2026-10-27T02:37:00+08:00");
  expect(new Date(quotaPeriodStart("daily", date)).toISOString()).toBe("2026-10-26T00:00:00.000Z");
  expect(new Date(quotaPeriodStart("weekly", new Date("2026-11-01T23:00:00Z"))).toISOString()).toBe("2026-10-26T00:00:00.000Z");
});

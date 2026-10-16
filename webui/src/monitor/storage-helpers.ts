export function formatStorageBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 1024) return `${Math.round(value)} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let amount = value;
  let unit = "B";
  for (const next of units) {
    amount /= 1024;
    unit = next;
    if (amount < 1024 || next === units.at(-1)) break;
  }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${unit}`;
}

export function storagePercent(value: number | null | undefined): number {
  return value == null || !Number.isFinite(value) ? 0 : Math.min(100, Math.max(0, value * 100));
}

export function storageStateClass(state: string | null | undefined): string {
  return state === "full" || state === "critical" ? "critical" : state === "warning" ? "warning" : "normal";
}

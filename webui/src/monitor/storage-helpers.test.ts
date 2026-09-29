import { formatStorageBytes, storagePercent, storageStateClass } from "./storage-helpers";

describe("monitor storage helpers", () => {
  it("formats small and GiB values for the capacity card", () => {
    expect(formatStorageBytes(512)).toBe("512 B");
    expect(formatStorageBytes(3 * 1024 ** 3)).toBe("3.0 GiB");
  });

  it("clamps progress and maps Windows-like capacity states", () => {
    expect(storagePercent(1.4)).toBe(100);
    expect(storagePercent(-0.2)).toBe(0);
    expect(storageStateClass("warning")).toBe("warning");
    expect(storageStateClass("full")).toBe("critical");
    expect(storageStateClass("normal")).toBe("normal");
  });
});

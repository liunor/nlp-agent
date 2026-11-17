import { cleanSandboxOutput, formatSandboxOutput } from "./sandbox-output";

it("removes terminal colors and links while preserving Unicode and ordinary brackets", () => {
  expect(cleanSandboxOutput("\u001b[31m中文 😀\u001b[0m\n[31m\ttext")).toBe("中文 😀\n[31m\ttext");
  expect(cleanSandboxOutput("\u001b]8;;https://example.test\u0007链接\u001b]8;;\u001b\\")).toBe("链接");
  expect(cleanSandboxOutput("错误\u001b[31")).toBe("错误");
});

it("keeps both streams, adding only a needed separator", () => {
  expect(formatSandboxOutput("输出", "错误")).toBe("输出\n错误");
  expect(formatSandboxOutput("输出\n", "错误")).toBe("输出\n错误");
  expect(formatSandboxOutput(undefined, "错误")).toBe("错误");
  expect(formatSandboxOutput("输出")).toBe("输出");
  expect(formatSandboxOutput("\u001b[0m")).toBe("运行完成。");
});

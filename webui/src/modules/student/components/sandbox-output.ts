// Output is plain text, not a terminal. Also handle older runtime images.
export function cleanSandboxOutput(text: string): string {
  // These control characters identify ANSI sequences; ordinary bracketed text stays intact.
  // eslint-disable-next-line no-control-regex
  return text.replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\|$)|(?:\u001b\[|\u009b)[0-?]*[ -/]*(?:[@-~]|$)|\u001b[@-_]/g, "");
}

export function formatSandboxOutput(stdout?: string, stderr?: string): string {
  const out = cleanSandboxOutput(stdout ?? "");
  const error = cleanSandboxOutput(stderr ?? "");
  return (out && error ? out + (out.endsWith("\n") ? "" : "\n") + error : out + error) || "运行完成。";
}

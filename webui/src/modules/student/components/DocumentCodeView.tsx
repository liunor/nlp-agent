import { lazy, Suspense } from "react";

const LazySyntaxHighlighter = lazy(async () => {
  const [{ default: SyntaxHighlighter }, { default: oneDark }, { default: oneLight }] = await Promise.all([
    import("react-syntax-highlighter/dist/esm/prism-async-light"),
    import("react-syntax-highlighter/dist/esm/styles/prism/one-dark"),
    import("react-syntax-highlighter/dist/esm/styles/prism/one-light"),
  ]);
  return {
    default({ language, code, dark }: { language: string; code: string; dark: boolean }) {
      return <SyntaxHighlighter language={language} style={dark ? oneDark : oneLight} customStyle={{ margin: 0, borderRadius: 0, fontSize: 13, background: "transparent" }}>{code}</SyntaxHighlighter>;
    },
  };
});

export function DocumentCodeView({ language, code }: { language: string; code: string }) {
  const dark = document.documentElement.classList.contains("dark");
  return (
    <div className="document-code-view">
      <Suspense fallback={<pre><code>{code}</code></pre>}>
        <LazySyntaxHighlighter language={language} code={code} dark={dark} />
      </Suspense>
    </div>
  );
}

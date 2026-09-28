import { useEffect } from "react";
import type { MouseEvent } from "react";
import { LEGAL_DOCUMENTS, type LegalDocumentKind } from "./legalDocuments";

interface ConsentNoticeProps {
  id: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
  onOpen: (document: LegalDocumentKind) => void;
}

export function ConsentNotice({ id, checked, disabled = false, onChange, onOpen }: ConsentNoticeProps) {
  const open = (event: MouseEvent<HTMLButtonElement>, document: LegalDocumentKind) => {
    event.preventDefault();
    event.stopPropagation();
    onOpen(document);
  };

  return (
    <div className="legal-consent-notice flex items-start gap-2 text-xs leading-5 text-gray-500">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        aria-label="我已阅读并同意用户协议和隐私政策"
        onChange={(event) => onChange(event.target.checked)}
        className="legal-consent-checkbox mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
      />
      <span>
        我已阅读并同意
        <button type="button" className="mx-0.5 text-blue-600 hover:underline" onClick={(event) => open(event, "terms")}>
          《用户协议》
        </button>
        和
        <button type="button" className="mx-0.5 text-blue-600 hover:underline" onClick={(event) => open(event, "privacy")}>
          《隐私政策》
        </button>
      </span>
    </div>
  );
}

interface LegalDocumentModalProps {
  document: LegalDocumentKind | null;
  onClose: () => void;
}

export function LegalDocumentModal({ document, onClose }: LegalDocumentModalProps) {
  useEffect(() => {
    if (!document) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [document, onClose]);

  if (!document) return null;
  const content = LEGAL_DOCUMENTS[document];

  return (
    <div
      className="legal-document-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="legal-document-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="legal-document-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="legal-document-header">
          <div>
            <h2 id="legal-document-title">{content.title}</h2>
            <p>{content.version} · 生效日期：{content.effectiveDate}</p>
          </div>
          <button type="button" className="legal-document-close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>
        <div className="legal-document-body">
          <p className="legal-document-intro">{content.intro}</p>
          {content.sections.map((section) => (
            <section key={section.heading} className="legal-document-section">
              <h3>{section.heading}</h3>
              {section.paragraphs?.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
              {section.items && (
                <ul>
                  {section.items.map((item) => <li key={item}>{item}</li>)}
                </ul>
              )}
            </section>
          ))}
        </div>
        <footer className="legal-document-footer">
          <button type="button" className="legal-document-confirm" onClick={onClose}>我已阅读</button>
        </footer>
      </section>
    </div>
  );
}

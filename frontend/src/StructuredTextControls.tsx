import { ReactNode, useEffect, useMemo, useState } from "react";

export interface FoldableLine {
  indent: number;
  value: string;
  foldEnd?: number;
}

export function CopyTextButton({ text }: { text: string }) {
  const [status, setStatus] = useState<"idle" | "copied" | "error">("idle");

  useEffect(() => {
    setStatus("idle");
  }, [text]);

  useEffect(() => {
    if (status === "idle") return;
    const timer = window.setTimeout(() => setStatus("idle"), 2_000);
    return () => window.clearTimeout(timer);
  }, [status]);

  async function copy() {
    try {
      await copyText(text);
      setStatus("copied");
    } catch {
      setStatus("error");
    }
  }

  const label = status === "copied" ? "Скопировано" : status === "error" ? "Не удалось" : "Копировать";
  return (
    <button
      className={`copy-text-button ${status}`}
      type="button"
      onClick={copy}
      aria-label={`${label}: весь текст файла`}
      title="Копировать весь исходный текст"
    >
      <span className="copy-text-icon" aria-hidden="true">{status === "copied" ? "✓" : "⧉"}</span>
      <span className="copy-text-label">{label}</span>
    </button>
  );
}

export function FoldableCode({
  lines,
  className = "",
  highlight,
}: {
  lines: FoldableLine[];
  className?: string;
  highlight: (value: string) => ReactNode;
}) {
  const [collapsed, setCollapsed] = useState<Set<number>>(() => new Set());

  useEffect(() => {
    setCollapsed(new Set());
  }, [lines]);

  const visibleLines = useMemo(
    () => lines
      .map((line, index) => ({ line, index }))
      .filter(({ index }) => !isHidden(index, lines, collapsed)),
    [collapsed, lines],
  );

  function toggle(index: number) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  return (
    <ol className={`xml-code ${className}`.trim()}>
      {visibleLines.map(({ line, index }) => {
        const foldable = line.foldEnd !== undefined && line.foldEnd > index + 1;
        const isCollapsed = collapsed.has(index);
        const hiddenLines = foldable ? line.foldEnd! - index - 1 : 0;
        return (
          <li
            key={index}
            value={index + 1}
            style={{ paddingInlineStart: `${6 + line.indent * 20}px` }}
          >
            {foldable
              ? (
                <button
                  className="fold-toggle"
                  type="button"
                  onClick={() => toggle(index)}
                  aria-expanded={!isCollapsed}
                  aria-label={isCollapsed ? "Развернуть узел" : "Свернуть узел"}
                  title={isCollapsed ? "Развернуть узел" : "Свернуть узел"}
                >
                  {isCollapsed ? "▸" : "▾"}
                </button>
              )
              : <span className="fold-spacer" aria-hidden="true" />}
            <code>{highlight(line.value)}</code>
            {isCollapsed && <span className="fold-summary">… скрыто строк: {hiddenLines}</span>}
          </li>
        );
      })}
    </ol>
  );
}

function isHidden(index: number, lines: FoldableLine[], collapsed: Set<number>): boolean {
  for (const openingLine of collapsed) {
    const closingLine = lines[openingLine]?.foldEnd;
    if (closingLine !== undefined && index > openingLine && index < closingLine) return true;
  }
  return false;
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // Browsers can deny the Clipboard API even in a secure context.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.position = "fixed";
  textarea.style.inset = "0 auto auto -9999px";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("Clipboard is unavailable");
}

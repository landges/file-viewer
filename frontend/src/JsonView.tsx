import { ReactNode, useEffect, useMemo, useState } from "react";

const MAX_FORMATTED_CHARACTERS = 1_000_000;
const MAX_FORMATTED_LINES = 50_000;

interface JsonLine {
  indent: number;
  value: string;
}

type JsonTokenKind = "string" | "number" | "boolean" | "null" | "punctuation" | "whitespace" | "other";

interface JsonToken {
  kind: JsonTokenKind;
  value: string;
}

export function JsonView({ url }: { url: string }) {
  const [content, setContent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [raw, setRaw] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setContent("");
    setError(null);
    setLoading(true);
    fetch(url, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((value) => {
        setContent(value);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught.message : "Неизвестная ошибка");
        setLoading(false);
      });
    return () => controller.abort();
  }, [url]);

  const formatted = useMemo(() => {
    if (content.length > MAX_FORMATTED_CHARACTERS) return null;
    const lines = formatJson(content);
    return lines.length <= MAX_FORMATTED_LINES ? lines : null;
  }, [content]);

  if (error) {
    return (
      <section className="center-panel error-panel">
        <div className="file-glyph">!</div>
        <h2>Не удалось открыть JSON</h2>
        <p>{error}</p>
      </section>
    );
  }
  if (loading) {
    return <section className="center-panel"><div className="spinner" /><h2>Читаем JSON…</h2></section>;
  }

  const showRaw = raw || !formatted;
  return (
    <section className="xml-view json-view">
      <div className="xml-toolbar">
        <div>
          <strong>JSON</strong>
          <span>{formatted ? `${formatted.length} строк` : "большой файл"}</span>
        </div>
        {formatted && (
          <div className="xml-modes" aria-label="Режим отображения">
            <button className={!raw ? "active" : ""} onClick={() => setRaw(false)}>Форматированный</button>
            <button className={raw ? "active" : ""} onClick={() => setRaw(true)}>Исходный</button>
          </div>
        )}
      </div>
      {!formatted && (
        <div className="notice xml-notice">
          Большой JSON показан без форматирования и подсветки, чтобы страница оставалась отзывчивой.
        </div>
      )}
      <div className="xml-scroll">
        {showRaw
          ? <pre className="xml-raw" dir="ltr">{content}</pre>
          : (
            <ol className="xml-code json-code">
              {formatted!.map((line, index) => (
                <li key={index} style={{ paddingInlineStart: `${24 + line.indent * 20}px` }}>
                  <code>{highlightJson(line.value)}</code>
                </li>
              ))}
            </ol>
          )}
      </div>
    </section>
  );
}

function formatJson(source: string): JsonLine[] {
  const tokens = lexJson(source).filter((token) => token.kind !== "whitespace");
  const lines: JsonLine[] = [];
  let indent = 0;
  let lineIndent = 0;
  let current = "";

  const append = (value: string, requestedIndent = indent) => {
    if (!current) lineIndent = requestedIndent;
    current += value;
  };
  const push = () => {
    if (!current) return;
    lines.push({ indent: lineIndent, value: current });
    current = "";
  };

  tokens.forEach((token, index) => {
    const previous = tokens[index - 1];
    const next = tokens[index + 1];
    if (token.value === "{" || token.value === "[") {
      append(token.value);
      const matchingClose = token.value === "{" ? "}" : "]";
      if (next?.value !== matchingClose) {
        push();
        indent += 1;
      }
      return;
    }
    if (token.value === "}" || token.value === "]") {
      const matchingOpen = token.value === "}" ? "{" : "[";
      if (previous?.value === matchingOpen && current) {
        append(token.value);
      } else {
        push();
        indent = Math.max(0, indent - 1);
        append(token.value, indent);
      }
      return;
    }
    if (token.value === ",") {
      append(token.value);
      push();
      return;
    }
    if (token.value === ":") {
      append(": ");
      return;
    }
    append(token.value);
  });
  push();
  return lines;
}

function lexJson(source: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  let offset = 0;
  while (offset < source.length) {
    const character = source[offset];
    if (/\s/.test(character)) {
      const start = offset;
      while (offset < source.length && /\s/.test(source[offset])) offset += 1;
      tokens.push({ kind: "whitespace", value: source.slice(start, offset) });
      continue;
    }
    if (character === "\"") {
      const start = offset;
      offset += 1;
      while (offset < source.length) {
        if (source[offset] === "\\") {
          offset = Math.min(source.length, offset + 2);
        } else if (source[offset] === "\"") {
          offset += 1;
          break;
        } else {
          offset += 1;
        }
      }
      tokens.push({ kind: "string", value: source.slice(start, offset) });
      continue;
    }
    if ("{}[],:".includes(character)) {
      tokens.push({ kind: "punctuation", value: character });
      offset += 1;
      continue;
    }

    const start = offset;
    while (offset < source.length && !/[\s{}[\],:\"]/.test(source[offset])) offset += 1;
    const value = source.slice(start, offset);
    let kind: JsonTokenKind = "other";
    if (/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) kind = "number";
    else if (value === "true" || value === "false") kind = "boolean";
    else if (value === "null") kind = "null";
    tokens.push({ kind, value });
  }
  return tokens;
}

function highlightJson(value: string): ReactNode[] {
  const tokens = lexJson(value);
  return tokens.map((token, index) => {
    if (token.kind === "whitespace") return token.value;
    if (token.kind === "string") {
      const next = tokens.slice(index + 1).find((candidate) => candidate.kind !== "whitespace");
      const className = next?.value === ":" ? "json-key" : "json-string";
      return <bdi className={className} dir="auto" key={index}>{token.value}</bdi>;
    }
    return <span className={`json-${token.kind}`} key={index}>{token.value}</span>;
  });
}

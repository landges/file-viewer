import { Fragment, ReactNode, useEffect, useMemo, useState } from "react";

const MAX_FORMATTED_CHARACTERS = 1_000_000;
const MAX_FORMATTED_LINES = 50_000;

interface XmlLine {
  indent: number;
  value: string;
}

interface XmlFrame {
  hasChild: boolean;
  hasText: boolean;
}

type XmlToken =
  | { kind: "markup"; value: string }
  | { kind: "text"; value: string };

export function XmlView({ url }: { url: string }) {
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
    const lines = formatXml(content);
    return lines.length <= MAX_FORMATTED_LINES ? lines : null;
  }, [content]);

  if (error) {
    return (
      <section className="center-panel error-panel">
        <div className="file-glyph">!</div>
        <h2>Не удалось открыть XML</h2>
        <p>{error}</p>
      </section>
    );
  }
  if (loading) {
    return <section className="center-panel"><div className="spinner" /><h2>Читаем XML…</h2></section>;
  }

  const showRaw = raw || !formatted;
  return (
    <section className="xml-view">
      <div className="xml-toolbar">
        <div>
          <strong>XML</strong>
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
          Большой XML показан без форматирования и подсветки, чтобы страница оставалась отзывчивой.
        </div>
      )}
      <div className="xml-scroll">
        {showRaw
          ? <pre className="xml-raw" dir="ltr">{content}</pre>
          : (
            <ol className="xml-code">
              {formatted!.map((line, index) => (
                <li key={index} style={{ paddingInlineStart: `${24 + line.indent * 20}px` }}>
                  <code>{highlightXml(line.value)}</code>
                </li>
              ))}
            </ol>
          )}
      </div>
    </section>
  );
}

function formatXml(source: string): XmlLine[] {
  const lines: XmlLine[] = [];
  const stack: XmlFrame[] = [];
  let depth = 0;
  let lastWasText = false;

  const addLine = (indent: number, value: string): number => {
    lines.push({ indent, value });
    return lines.length - 1;
  };
  const appendToLine = (lineIndex: number, value: string) => {
    if (lineIndex < 0 || !lines[lineIndex]) addLine(depth, value);
    else lines[lineIndex].value += value;
  };

  for (const token of lexXml(source)) {
    if (token.kind === "text") {
      if (!token.value.trim()) continue;
      const value = token.value.trim().replace(/\s*\r?\n\s*/g, " ");
      const lineIndex = lines.length ? lines.length - 1 : addLine(depth, "");
      appendToLine(lineIndex, value);
      const frame = stack.at(-1);
      if (frame) frame.hasText = true;
      lastWasText = true;
      continue;
    }

    const value = token.value.trim().replace(/\s*\r?\n\s*/g, " ");
    if (!value) continue;
    const closing = /^<\//.test(value);
    const processing = /^<\?/.test(value);
    const declaration = /^<!/.test(value);
    const selfClosing = /\/\s*>$/.test(value);

    if (closing) {
      depth = Math.max(0, depth - 1);
      const frame = stack.pop();
      if (frame && (lastWasText || (!frame.hasChild && !frame.hasText))) {
        appendToLine(lines.length - 1, value);
      } else {
        addLine(depth, value);
      }
      lastWasText = false;
      continue;
    }

    if (processing || declaration || selfClosing) {
      if (lastWasText) appendToLine(lines.length - 1, value);
      else addLine(depth, value);
      const parent = stack.at(-1);
      if (parent) parent.hasChild = true;
      lastWasText = false;
      continue;
    }

    const parent = stack.at(-1);
    if (parent) parent.hasChild = true;
    if (lastWasText) appendToLine(lines.length - 1, value);
    else addLine(depth, value);
    stack.push({ hasChild: false, hasText: false });
    depth += 1;
    lastWasText = false;
  }
  return lines;
}

function lexXml(source: string): XmlToken[] {
  const tokens: XmlToken[] = [];
  let offset = 0;
  while (offset < source.length) {
    const markupStart = source.indexOf("<", offset);
    if (markupStart < 0) {
      tokens.push({ kind: "text", value: source.slice(offset) });
      break;
    }
    if (markupStart > offset) {
      tokens.push({ kind: "text", value: source.slice(offset, markupStart) });
    }
    const markupEnd = findMarkupEnd(source, markupStart);
    tokens.push({ kind: "markup", value: source.slice(markupStart, markupEnd) });
    offset = markupEnd;
  }
  return tokens;
}

function findMarkupEnd(source: string, start: number): number {
  const terminated = (marker: string, markerLength: number) => {
    const end = source.indexOf(marker, start + markerLength);
    return end < 0 ? source.length : end + marker.length;
  };
  if (source.startsWith("<!--", start)) return terminated("-->", 4);
  if (source.startsWith("<![CDATA[", start)) return terminated("]]>", 9);
  if (source.startsWith("<?", start)) return terminated("?>", 2);

  let quote = "";
  let subsetDepth = 0;
  for (let index = start + 1; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === quote) quote = "";
      continue;
    }
    if (character === "\"" || character === "'") quote = character;
    else if (character === "[") subsetDepth += 1;
    else if (character === "]") subsetDepth = Math.max(0, subsetDepth - 1);
    else if (character === ">" && subsetDepth === 0) return index + 1;
  }
  return source.length;
}

function highlightXml(value: string): ReactNode[] {
  return lexXml(value).map((token, index) => {
    if (token.kind === "text") {
      return <bdi className="xml-text" dir="auto" key={index}>{token.value}</bdi>;
    }
    if (token.value.startsWith("<!--")) {
      return <span className="xml-comment" key={index}>{token.value}</span>;
    }
    if (token.value.startsWith("<![CDATA[")) {
      return <span className="xml-cdata" key={index}>{token.value}</span>;
    }
    if (token.value.startsWith("<!")) {
      return <span className="xml-declaration" key={index}>{token.value}</span>;
    }
    return <Fragment key={index}>{highlightTag(token.value, index)}</Fragment>;
  });
}

function highlightTag(value: string, keyPrefix: number): ReactNode[] {
  const match = value.match(/^(<\/?|<\?)([^\s/>?]+)([\s\S]*?)(\?>|\/?>)$/);
  if (!match) return [<span className="xml-punctuation" key={`${keyPrefix}-raw`}>{value}</span>];
  const [, opening, name, attributes, closing] = match;
  const result: ReactNode[] = [
    <span className="xml-punctuation" key={`${keyPrefix}-open`}>{opening}</span>,
    <span className="xml-tag" key={`${keyPrefix}-name`}>{name}</span>,
  ];
  const attributePattern = /([:\w.-]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)/g;
  let offset = 0;
  let attributeMatch: RegExpExecArray | null;
  let part = 0;
  while ((attributeMatch = attributePattern.exec(attributes))) {
    if (attributeMatch.index > offset) result.push(attributes.slice(offset, attributeMatch.index));
    result.push(
      <span className="xml-attribute" key={`${keyPrefix}-attr-${part}`}>{attributeMatch[1]}</span>,
      <span className="xml-punctuation" key={`${keyPrefix}-equals-${part}`}>{attributeMatch[2]}</span>,
      <span className="xml-value" key={`${keyPrefix}-value-${part}`}>{attributeMatch[3]}</span>,
    );
    offset = attributeMatch.index + attributeMatch[0].length;
    part += 1;
  }
  if (offset < attributes.length) result.push(attributes.slice(offset));
  result.push(<span className="xml-punctuation" key={`${keyPrefix}-close`}>{closing}</span>);
  return result;
}

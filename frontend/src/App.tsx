import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  createPreview,
  getData,
  getPreview,
  openEntry,
  Preview
} from "./api";

interface WorkbookData {
  sheets: Array<{ name: string; rows: unknown[][] }>;
  truncated: boolean;
}

interface ArchiveEntry {
  id: string;
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
  compressed_size: number;
  encrypted: boolean;
}

interface ArchiveData {
  entries: ArchiveEntry[];
  total_size: number;
  format: string;
}

interface ArchiveNode {
  key: string;
  name: string;
  path: string;
  entry?: ArchiveEntry;
  children: ArchiveNode[];
}

interface EmailAttachment {
  id: string;
  name: string;
  size: number;
  content_type: string;
}

interface EmailData {
  from: string;
  to: string;
  cc: string;
  subject: string;
  date: string;
  text: string;
  html: string;
  attachments: EmailAttachment[];
}

function App() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceUrl = params.get("url");
  const initialId = params.get("id");

  useEffect(() => {
    let cancelled = false;
    async function initialize() {
      if (!sourceUrl && !initialId) return;
      try {
        const result = initialId
          ? await getPreview(initialId)
          : await createPreview(sourceUrl!, params.get("name") || undefined);
        if (!cancelled) setPreview(result);
      } catch (caught) {
        if (!cancelled) setError(errorMessage(caught));
      }
    }
    initialize();
    return () => {
      cancelled = true;
    };
  }, [initialId, params, sourceUrl]);

  useEffect(() => {
    if (!preview || !["queued", "processing"].includes(preview.status)) return;
    const timer = window.setTimeout(async () => {
      try {
        setPreview(await getPreview(preview.id));
      } catch (caught) {
        setError(errorMessage(caught));
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [preview]);

  if (!sourceUrl && !initialId) return <Landing />;
  return (
    <div className="app-shell">
      <Header preview={preview} />
      <main className="viewer-stage">
        {error && <ErrorPanel message={error} />}
        {!error && !preview && <LoadingPanel label="Подключаемся к источнику…" />}
        {!error && preview?.status === "queued" && <LoadingPanel label="Файл поставлен в очередь…" />}
        {!error && preview?.status === "processing" && <LoadingPanel label="Готовим предпросмотр…" />}
        {!error && preview?.status === "failed" && <ErrorPanel message={preview.error || "Не удалось обработать файл"} />}
        {!error && preview?.status === "ready" && <PreviewBody preview={preview} />}
      </main>
    </div>
  );
}

function Landing() {
  const [url, setUrl] = useState("");
  function submit(event: FormEvent) {
    event.preventDefault();
    const next = new URL(window.location.href);
    next.pathname = "/view";
    next.search = "";
    next.searchParams.set("url", url);
    window.location.assign(next);
  }
  return (
    <main className="landing">
      <div className="landing-card">
        <div className="brand-mark">FV</div>
        <p className="eyebrow">Внутренний сервис</p>
        <h1>Просмотр файлов</h1>
        <p className="landing-copy">
          Откройте публичную HTTP-ссылку на документ, изображение, аудио, архив или письмо.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="source-url">Адрес файла</label>
          <div className="url-row">
            <input
              id="source-url"
              type="url"
              required
              value={url}
              placeholder="http://storage.internal/path/file.docx"
              onChange={(event) => setUrl(event.target.value)}
            />
            <button type="submit">Открыть</button>
          </div>
        </form>
      </div>
    </main>
  );
}

function Header({ preview }: { preview: Preview | null }) {
  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="File Viewer">
        <span className="brand-mark small">FV</span>
      </a>
      <div className="file-title">
        <strong>{preview?.display_name || "Открываем файл"}</strong>
        {preview?.status === "ready" && (
          <span>{preview.detected_type}{preview.depth ? ` · вложение ${preview.depth}-го уровня` : ""}</span>
        )}
      </div>
      <div className="topbar-actions">
        {preview?.download_url && (
          <a className="button secondary" href={preview.download_url}>Скачать</a>
        )}
      </div>
    </header>
  );
}

function PreviewBody({ preview }: { preview: Preview }) {
  if (preview.renderer === "pdf" && preview.content_url) {
    return (
      <object className="document-frame" data={preview.content_url} type="application/pdf">
        <FallbackDownload preview={preview} />
      </object>
    );
  }
  if (preview.renderer === "image" && preview.content_url) {
    return <div className="image-canvas"><img src={preview.content_url} alt={preview.display_name} /></div>;
  }
  if (preview.renderer === "audio" && preview.content_url) {
    return (
      <section className="audio-view">
        <div className="audio-glyph" aria-hidden="true">♪</div>
        <h2>{preview.display_name}</h2>
        <audio controls preload="metadata" src={preview.content_url}>
          Браузер не поддерживает воспроизведение этого аудиофайла.
        </audio>
      </section>
    );
  }
  if (preview.renderer === "html" && preview.content_url) {
    return (
      <iframe
        className="html-frame"
        sandbox=""
        src={preview.content_url}
        title={preview.display_name}
      />
    );
  }
  if (preview.renderer === "text" && preview.content_url) return <TextView url={preview.content_url} />;
  if (preview.renderer === "spreadsheet" && preview.data_url) return <SpreadsheetView url={preview.data_url} />;
  if (preview.renderer === "archive" && preview.data_url) return <ArchiveView preview={preview} />;
  if (preview.renderer === "email" && preview.data_url) return <EmailView preview={preview} />;
  return (
    <section className="center-panel">
      <div className="file-glyph">?</div>
      <h2>Предпросмотр этого формата пока недоступен</h2>
      <p>Файл распознан как «{preview.detected_type}».</p>
      <FallbackDownload preview={preview} />
    </section>
  );
}

function TextView({ url }: { url: string }) {
  const [content, setContent] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(url).then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.text();
    }).then(setContent).catch((caught) => setError(errorMessage(caught)));
  }, [url]);
  if (error) return <ErrorPanel message={error} />;
  return <pre className="text-view">{content}</pre>;
}

function SpreadsheetView({ url }: { url: string }) {
  const [data, setData] = useState<WorkbookData | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getData<WorkbookData>(url).then(setData).catch((caught) => setError(errorMessage(caught)));
  }, [url]);
  if (error) return <ErrorPanel message={error} />;
  if (!data) return <LoadingPanel label="Читаем таблицу…" />;
  const sheet = data.sheets[active];
  return (
    <section className="sheet-view">
      {data.truncated && <div className="notice">Показана только часть большой таблицы.</div>}
      <div className="sheet-scroll">
        <table>
          <tbody>
            {(sheet?.rows || []).map((row, rowIndex) => (
              <tr key={rowIndex}>
                <th className="row-number">{rowIndex + 1}</th>
                {row.map((cell, cellIndex) => <td key={cellIndex}>{renderCell(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav className="sheet-tabs" aria-label="Листы книги">
        {data.sheets.map((item, index) => (
          <button className={index === active ? "active" : ""} key={item.name} onClick={() => setActive(index)}>
            {item.name}
          </button>
        ))}
      </nav>
    </section>
  );
}

function ArchiveView({ preview }: { preview: Preview }) {
  const [data, setData] = useState<ArchiveData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  useEffect(() => {
    getData<ArchiveData>(preview.data_url!).then(setData).catch((caught) => setError(errorMessage(caught)));
  }, [preview.data_url]);

  async function open(item: ArchiveEntry) {
    if (item.is_dir || item.encrypted) return;
    const tab = window.open("about:blank", "_blank");
    setOpening(item.id);
    try {
      const child = await openEntry(preview.id, item.id);
      if (tab) tab.location.href = `/view?id=${encodeURIComponent(child.id)}`;
      else window.location.href = `/view?id=${encodeURIComponent(child.id)}`;
    } catch (caught) {
      tab?.close();
      setError(errorMessage(caught));
    } finally {
      setOpening(null);
    }
  }

  if (error) return <ErrorPanel message={error} />;
  if (!data) return <LoadingPanel label="Читаем структуру архива…" />;
  const tree = buildArchiveTree(data.entries);
  return (
    <section className="archive-view">
      <div className="archive-summary">
        <span>{data.entries.length} элементов</span>
        <span>{formatBytes(data.total_size)} после распаковки</span>
      </div>
      <div className="archive-list">
        {tree.map((node) => (
          <ArchiveNodeRow node={node} depth={0} key={node.key} opening={opening} onOpen={open} />
        ))}
      </div>
    </section>
  );
}

function ArchiveNodeRow({
  node,
  depth,
  opening,
  onOpen
}: {
  node: ArchiveNode;
  depth: number;
  opening: string | null;
  onOpen: (entry: ArchiveEntry) => void;
}) {
  const isDirectory = node.children.length > 0 || node.entry?.is_dir === true;
  const [expanded, setExpanded] = useState(depth < 1);
  const encrypted = node.entry?.encrypted === true;
  return (
    <>
      <button
        className="archive-row"
        disabled={!isDirectory && (encrypted || opening === node.entry?.id)}
        onClick={() => isDirectory ? setExpanded((value) => !value) : node.entry && onOpen(node.entry)}
        title={encrypted ? "Файл защищён паролем" : node.path}
        aria-expanded={isDirectory ? expanded : undefined}
      >
        <span className="entry-icon" style={{ marginLeft: `${depth * 18}px` }}>
          {isDirectory ? (expanded ? "▾" : "▸") : encrypted ? "⌾" : "□"}
        </span>
        <span className="entry-path">{node.name}</span>
        <span className="entry-size">{isDirectory ? "" : formatBytes(node.entry?.size || 0)}</span>
      </button>
      {isDirectory && expanded && node.children.map((child) => (
        <ArchiveNodeRow
          node={child}
          depth={depth + 1}
          key={child.key}
          opening={opening}
          onOpen={onOpen}
        />
      ))}
    </>
  );
}

function EmailView({ preview }: { preview: Preview }) {
  const [data, setData] = useState<EmailData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getData<EmailData>(preview.data_url!).then(setData).catch((caught) => setError(errorMessage(caught)));
  }, [preview.data_url]);
  async function openAttachment(item: EmailAttachment) {
    const tab = window.open("about:blank", "_blank");
    try {
      const child = await openEntry(preview.id, item.id);
      if (tab) tab.location.href = `/view?id=${encodeURIComponent(child.id)}`;
      else window.location.href = `/view?id=${encodeURIComponent(child.id)}`;
    } catch (caught) {
      tab?.close();
      setError(errorMessage(caught));
    }
  }
  if (error) return <ErrorPanel message={error} />;
  if (!data) return <LoadingPanel label="Разбираем письмо…" />;
  return (
    <section className="email-view">
      <div className="email-envelope">
        <h2>{data.subject || "Без темы"}</h2>
        <dl>
          <dt>От</dt><dd>{data.from || "—"}</dd>
          <dt>Кому</dt><dd>{data.to || "—"}</dd>
          {data.cc && <><dt>Копия</dt><dd>{data.cc}</dd></>}
          <dt>Дата</dt><dd>{data.date || "—"}</dd>
        </dl>
      </div>
      {data.attachments.length > 0 && (
        <div className="attachments">
          {data.attachments.map((item) => (
            <button key={item.id} onClick={() => openAttachment(item)}>
              <span>□</span><strong>{item.name}</strong><small>{formatBytes(item.size)}</small>
            </button>
          ))}
        </div>
      )}
      {data.html
        ? <iframe className="email-body" sandbox="" srcDoc={data.html} title="Содержимое письма" />
        : <pre className="email-text">{data.text || "Письмо не содержит текстовой части."}</pre>}
    </section>
  );
}

function LoadingPanel({ label }: { label: string }) {
  return <section className="center-panel"><div className="spinner" /><h2>{label}</h2></section>;
}

function ErrorPanel({ message }: { message: string }) {
  return <section className="center-panel error-panel"><div className="file-glyph">!</div><h2>Не удалось открыть файл</h2><p>{message}</p></section>;
}

function FallbackDownload({ preview }: { preview: Preview }) {
  return preview.download_url ? <a className="button" href={preview.download_url}>Скачать файл</a> : null;
}

function formatBytes(value: number): string {
  if (!value) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function buildArchiveTree(entries: ArchiveEntry[]): ArchiveNode[] {
  const roots: ArchiveNode[] = [];
  for (const entry of entries) {
    const parts = entry.path.replace(/\\/g, "/").split("/").filter(Boolean);
    let level = roots;
    let currentPath = "";
    parts.forEach((part, index) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      let node = level.find((candidate) => candidate.name === part);
      if (!node) {
        node = { key: currentPath, name: part, path: currentPath, children: [] };
        level.push(node);
      }
      if (index === parts.length - 1) node.entry = entry;
      level = node.children;
    });
  }
  const sort = (nodes: ArchiveNode[]) => {
    nodes.sort((left, right) => {
      const leftDirectory = left.children.length > 0 || left.entry?.is_dir;
      const rightDirectory = right.children.length > 0 || right.entry?.is_dir;
      if (leftDirectory !== rightDirectory) return leftDirectory ? -1 : 1;
      return left.name.localeCompare(right.name, "ru", { sensitivity: "base" });
    });
    nodes.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}

function errorMessage(value: unknown): string {
  return value instanceof Error ? value.message : "Неизвестная ошибка";
}

export default App;

export type PreviewStatus = "queued" | "processing" | "ready" | "failed";
export type Renderer =
  | "proxy"
  | "pdf"
  | "image"
  | "audio"
  | "text"
  | "xml"
  | "json"
  | "html"
  | "spreadsheet"
  | "archive"
  | "email"
  | "unsupported";

export interface Preview {
  id: string;
  display_name: string;
  status: PreviewStatus;
  renderer: Renderer;
  detected_type: string;
  mime_type: string;
  error: string | null;
  content_url: string | null;
  data_url: string | null;
  download_url: string | null;
  depth: number;
}

async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // The HTTP status is enough when the body is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function createPreview(url: string, filename?: string): Promise<Preview> {
  return request<Preview>("/api/previews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, filename: filename || null })
  });
}

export function uploadPreview(file: File): Promise<Preview> {
  const body = new FormData();
  body.append("file", file);
  return request<Preview>("/api/uploads", { method: "POST", body });
}

export function getPreview(id: string): Promise<Preview> {
  return request<Preview>(`/api/previews/${encodeURIComponent(id)}`);
}

export function openEntry(id: string, entryId: string): Promise<Preview> {
  return request<Preview>(`/api/previews/${encodeURIComponent(id)}/entries`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entry_id: entryId })
  });
}

export async function getData<T>(url: string): Promise<T> {
  return request<T>(url);
}

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_CATENA_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8765";

export interface HealthOut {
  ok: boolean;
  data_dir: string;
  gateway_ready: boolean;
}

export interface PaperOut {
  id: number;
  title: string;
  source_path: string;
  stored_pdf_path?: string | null;
  doi?: string | null;
  url?: string | null;
  authors_json?: string[] | null;
  year?: number | null;
  venue?: string | null;
  publication_date?: string | null;
  citation_count?: number | null;
  abstract?: string | null;
  metadata_json?: Record<string, unknown> | null;
  content_hash?: string | null;
  parse_status: string;
  index_status: string;
  parse_error?: string | null;
  docling_json_path?: string | null;
  markdown_path?: string | null;
  tags: string[];
}

export interface TableOut {
  id: number;
  name: string;
  description?: string | null;
  source_filter_json?: PaperFilter | Record<string, unknown> | null;
}

export interface TagOut {
  id: number;
  name: string;
  normalized_name: string;
  color?: string | null;
  description?: string | null;
}

export interface ColumnOut {
  id: number;
  table_id: number;
  name: string;
  prompt: string;
  output_type: string;
  retrieval_query?: string | null;
  top_k?: number | null;
  model?: string | null;
  output_schema_json?: Record<string, unknown> | null;
}

export interface CellOut {
  id: number;
  table_id: number;
  paper_id: number;
  column_id: number;
  status: string;
  answer_text?: string | null;
  value_json?: Record<string, unknown> | null;
  evidence_json?: Array<Record<string, unknown>> | null;
  confidence?: string | null;
  raw_json?: Record<string, unknown> | null;
  error?: string | null;
}

export interface MatrixRowOut {
  paper: PaperOut;
  cells: Record<string, CellOut>;
}

export interface MatrixOut {
  table: TableOut;
  columns: ColumnOut[];
  rows: MatrixRowOut[];
}

export interface SimilarityOut {
  id: number;
  paper_id_a: number;
  paper_id_b: number;
  score: number;
  cosine_similarity: number;
  algorithm: string;
  embedding_model?: string | null;
  embedding_hash?: string | null;
  details_json?: Record<string, unknown> | null;
}

export interface SimilarPaperOut {
  paper: Omit<PaperOut, "tags">;
  similarity: SimilarityOut;
}

export interface PaperFilter {
  tags_all?: string[];
  tags_any?: string[];
  tags_none?: string[];
  untagged?: boolean;
  year_min?: number | null;
  year_max?: number | null;
  citations_min?: number | null;
  citations_max?: number | null;
  title_contains?: string | null;
  venue_contains?: string | null;
  has_doi?: boolean;
  missing_doi?: boolean;
  has_pdf?: boolean;
  parsed_only?: boolean;
  indexed_only?: boolean;
  limit?: number | null;
  sort_by?: "created" | "title" | "year" | "citations";
  descending?: boolean;
}

export interface AskOut {
  question: string;
  paper_ids: number[];
  answer: string;
  evidence: Array<Record<string, unknown>>;
  confidence?: string | null;
  rationale?: string | null;
  raw: Record<string, unknown>;
  retrieved_chunk_ids: number[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: init?.body instanceof FormData
      ? init.headers
      : {
          "Content-Type": "application/json",
          ...init?.headers,
        },
  });

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) {
        message = body.detail;
      }
    } catch {
      // Keep the status text.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | string[] | undefined | null>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, item));
    } else {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  health: () => request<HealthOut>("/health"),
  tables: () => request<TableOut[]>("/tables"),
  createTable: (payload: { name: string; description?: string | null }) =>
    request<TableOut>("/tables", { method: "POST", body: JSON.stringify(payload) }),
  createFilteredTable: (payload: {
    name: string;
    description?: string | null;
    paper_filter: PaperFilter;
  }) => request<MatrixOut>("/tables/from-filter", { method: "POST", body: JSON.stringify(payload) }),
  refreshTable: (tableId: number, prune = false) =>
    request<MatrixOut>(`/tables/${tableId}/refresh`, {
      method: "POST",
      body: JSON.stringify({ prune }),
    }),
  attachPaperToTable: (tableId: number, paperId: number) =>
    request<MatrixOut>(`/tables/${tableId}/papers`, {
      method: "POST",
      body: JSON.stringify({ paper_id: paperId }),
    }),
  matrix: (tableId: number) => request<MatrixOut>(`/tables/${tableId}/matrix`),
  papers: (params: Record<string, string | number | boolean | string[] | undefined | null> = {}) =>
    request<PaperOut[]>(`/papers${query(params)}`),
  uploadPaper: (formData: FormData) =>
    request<PaperOut>("/papers/upload", { method: "POST", body: formData }),
  uploadPapers: (formData: FormData) =>
    request<PaperOut[]>("/papers/upload-batch", { method: "POST", body: formData }),
  setMetadata: (
    paperId: number,
    payload: {
      year?: number | null;
      venue?: string | null;
      citation_count?: number | null;
      doi?: string | null;
      abstract?: string | null;
    },
  ) => request<PaperOut>(`/papers/${paperId}/metadata`, { method: "PATCH", body: JSON.stringify(payload) }),
  enrichPaper: (paperId: number) =>
    request<PaperOut>(`/papers/${paperId}/enrich`, { method: "POST", body: JSON.stringify({}) }),
  addTags: (paperId: number, tags: string[]) =>
    request<PaperOut>(`/papers/${paperId}/tags`, { method: "POST", body: JSON.stringify({ tags }) }),
  removeTag: (paperId: number, tagName: string) =>
    request<PaperOut>(`/papers/${paperId}/tags/${encodeURIComponent(tagName)}`, {
      method: "DELETE",
    }),
  tags: () => request<TagOut[]>("/tags"),
  createTag: (payload: { name: string; color?: string | null; description?: string | null }) =>
    request<TagOut>("/tags", { method: "POST", body: JSON.stringify(payload) }),
  columns: (tableId?: number) => request<ColumnOut[]>(`/columns${query({ table_id: tableId })}`),
  createColumn: (payload: {
    table_id: number;
    name: string;
    prompt: string;
    retrieval_query?: string | null;
    top_k?: number | null;
    run?: boolean;
  }) => request<ColumnOut>("/columns", { method: "POST", body: JSON.stringify(payload) }),
  run: (payload: {
    table_id?: number | null;
    column_id?: number | null;
    paper_id?: number | null;
    limit?: number | null;
    retry_failed?: boolean;
  }) => request<CellOut[]>("/run", { method: "POST", body: JSON.stringify(payload) }),
  ask: (payload: {
    question: string;
    paper_ids?: number[] | null;
    table_id?: number | null;
    top_k?: number | null;
  }) => request<AskOut>("/ask", { method: "POST", body: JSON.stringify(payload) }),
  computeSimilarity: (payload: { paper_ids?: number[] | null; table_id?: number | null }) =>
    request<SimilarityOut[]>("/similarity/compute", { method: "POST", body: JSON.stringify(payload) }),
  similarPapers: (paperId: number, limit = 10) =>
    request<SimilarPaperOut[]>(`/papers/${paperId}/similar${query({ limit })}`),
};

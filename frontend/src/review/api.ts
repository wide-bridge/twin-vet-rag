// Client for the MVP1 review API (/api/v1/review, docs/04 §7). Reviewer cookie is HttpOnly, path-scoped.

export type Me = { id: string; role: "vet" | "operator"; display_name: string; credential_status: string; production: boolean };
export type ReviewStatus = "draft" | "vet_reviewed" | "independently_verified" | "disputed";
export type ItemSummary = {
  id: string;
  origin: "dataset" | "mock" | "consultation";
  is_mock: boolean;
  question: string;
  review_status: ReviewStatus;
  dispute_type: string | null;
  my_label: "draft" | "submitted" | null;
  created_at: string;
  needs_recheck?: boolean;
  is_test?: boolean;
};
// M03 operator views (numbers only / consented + answered questions only)
export type M03Stats = {
  days: number | null;
  links: Record<string, number>;
  questions: Record<string, number>;
  reply_hours: { count: number; median: number | null; max: number | null };
  consent: { yes: number; total: number; rate: number | null };
  import: { pending: number; imported: number; needs_recheck: number };
};
// 서비스 이용통계 (operator): Flow per KST day + Stock levels, counts only
export type UsageFlow = Record<string, number | null> & { topics?: Record<string, number> };
export type UsageReport = {
  days: number | null; today: string; retention_days: number;
  flow: { totals: UsageFlow; by_day: (UsageFlow & { day: string })[] };
  stock: { now: Record<string, number>; by_day: (Record<string, number> & { day: string })[] };
};
export type M03Pending = { id: string; replied_at: string | null; revision_id: string; corrected: boolean; earlier_imports: number; question_preview: string };
export type M03PendingDetail = {
  id: string; revision_id: string; corrected: boolean; question: string; ai_answer: string; note_to_vet: string | null;
  vet_reply: string; dog: Record<string, string | number>; sources: string[]; ai_response_type: string | null;
};
export type ConceptTag = { code: string; assertion: string };
export type Label = {
  id: string;
  author_kind: "vet" | "operator" | "ai";
  reviewer_id: string | null;
  state: "draft" | "submitted";
  ai_suggestion_shown: boolean | null;
  accuracy: string | null;
  evidence_support: string | null;
  sufficiency: string | null;
  guidance: string | null;
  safety_issue: boolean;
  revised_answer: string | null;
  revision_reason: string | null;
  revision_evidence: string[];
  reuse_scope: string | null;
  note: string | null;
  concepts: ConceptTag[];
  submitted_at: string | null;
  active_seconds: number;
};
export type AutoCheck = {
  retrieval: "relevant" | "partial" | "irrelevant";
  claims: { claim: string; support: "supported" | "partial" | "unsupported"; sources: number[] }[];
  citation_mismatch: number[];
  missing_conditions: string[];
  forbidden_content: string[];
  reference_agreement: "agree" | "partial" | "conflict" | "none";
  reference_answer?: string | null;
  needs_medical_judgment: boolean;
  summary: string;
  issues: string[];
  top1_score: number | null;
  retrieved: { rank: number; title: string; department: string | null; score: number; cited: boolean }[];
};

export type ItemDetail = {
  item: ItemSummary & {
    history: { role: string; content: string }[];
    dog: Record<string, unknown>;
    ai_answer: string;
    response_type: string | null;
    citations: { n?: number; title: string; department?: string | null; excerpt?: string }[];
    versions: Record<string, string>;
    show_ai_suggestion: boolean;
    vet_reference?: string | null;
  };
  my_label: Label | null;
  ai_suggestion: Label | null;
  other_labels: Label[];
  other_label_count: number;
  auto_check: AutoCheck | null;
};
export type GuideField = { label: string; help: string; options?: Record<string, [string, string]>; assertions?: Record<string, string> };
export type Guide = { version: string; fields: Record<string, GuideField>; dispute_types: Record<string, string> };
export type Concept = { code: string; scheme: string; label: string; synonyms: string[]; status: string };
export type Candidate = {
  id: string;
  item_id: string | null;
  kind: "case" | "general";
  title: string;
  content: string;
  conditions: Record<string, unknown>;
  evidence: string[];
  is_mock: boolean;
  publish_status: "unapproved" | "approved" | "active" | "rejected" | "retired";
  approved: boolean;
  approved_at: string | null;
  source_withdrawn_at?: string | null;
  eval_result: Record<string, unknown> | null;
  eval_passed: boolean | null;
  document_id: string | null;
};
export type LabelBody = Omit<Label, "id" | "author_kind" | "reviewer_id" | "state" | "ai_suggestion_shown" | "submitted_at" | "active_seconds"> & {
  active_seconds: number;
};
export type CandidateBody = { item_id: string | null; kind: "case" | "general"; title: string; content: string; conditions: Record<string, unknown>; evidence: string[] };

export class ReviewApiError extends Error {
  constructor(public status: number, public code: string, message: string, public fields: Record<string, string> = {}) {
    super(message);
  }
}

const BASE = "/api/v1/review";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-TwinVet-Client": "web" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ReviewApiError(0, "network", "서버에 연결할 수 없습니다.");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON */
  }
  if (!res.ok) {
    const e = (data as { error?: { code?: string; message?: string; fields?: unknown } } | null)?.error;
    const fields = e?.fields && !Array.isArray(e.fields) ? (e.fields as Record<string, string>) : {};
    throw new ReviewApiError(res.status, e?.code ?? "http_" + res.status, e?.message ?? "오류가 발생했습니다.", fields);
  }
  return data as T;
}

export const reviewApi = {
  login: (access_key: string) => call<Me>("POST", "/login", { access_key }),
  logout: () => call<void>("POST", "/logout"),
  me: () => call<Me>("GET", "/me"),
  guide: () => call<Guide>("GET", "/guide"),
  concepts: () => call<{ items: Concept[]; schemes: string[] }>("GET", "/concepts"),
  items: (status?: string) => call<{ items: ItemSummary[] }>("GET", "/items" + (status ? `?status=${encodeURIComponent(status)}` : "")),
  item: (id: string) => call<ItemDetail>("GET", `/items/${id}`),
  save: (id: string, body: LabelBody) => call<ItemDetail>("PUT", `/items/${id}/label`, body),
  submit: (id: string, body: LabelBody) => call<ItemDetail>("POST", `/items/${id}/label/submit`, body),
  adjudicate: (id: string, dispute_type: string, note: string) => call<ItemDetail>("POST", `/items/${id}/adjudicate`, { dispute_type, note }),
  candidates: () => call<{ items: Candidate[] }>("GET", "/candidates"),
  createCandidate: (b: CandidateBody) => call<Candidate>("POST", "/candidates", b),
  updateCandidate: (id: string, b: CandidateBody) => call<Candidate>("PUT", `/candidates/${id}`, b),
  approve: (id: string) => call<Candidate>("POST", `/candidates/${id}/approve`),
  usage: (days?: number) => call<UsageReport>("GET", "/usage" + (days ? `?days=${days}` : "")),
  m03Stats: (days?: number) => call<M03Stats>("GET", "/m03/stats" + (days ? `?days=${days}` : "")),
  m03Pending: () => call<{ items: M03Pending[] }>("GET", "/m03/pending"),
  m03PendingDetail: (id: string) => call<M03PendingDetail>("GET", `/m03/pending/${id}`),
  m03Import: (id: string, b: { revision_id: string; question: string; ai_answer: string; note_to_vet: string | null; vet_reply: string }) =>
    call<{ item_id: string; revision_id: string }>("POST", `/m03/pending/${id}/import`, b),
  m03Availability: () => fetch("/api/v1/m03/availability").then((r) => r.json() as Promise<{ enabled: boolean }>).catch(() => ({ enabled: false })),
};

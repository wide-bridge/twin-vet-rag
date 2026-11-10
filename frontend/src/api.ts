// Typed client for /api/v1 (docs/04 §2). State-changing calls send the client header the server's
// CSRF guard requires; the session cookie is HttpOnly and handled by the browser.

export type Neutered = "yes" | "no" | "unknown";
export type AgeBasis = "day" | "month" | "year" | "estimate";
// Sent when creating a consultation: a birth date as far as known (year -> month -> day) OR an estimated age.
export type DogIn = {
  breed: string;
  birth_year: number | null;
  birth_month: number | null;
  birth_day: number | null;
  age_months: number | null;
  weight_kg: number | null;
  neutered: Neutered | null;
  health_notes: string | null;
};
// Returned by the server: age_months is computed at creation, age_basis says how the age is known.
export type Dog = Omit<DogIn, "age_months"> & { age_months: number; age_basis: AgeBasis };
export type ResponseType = "grounded" | "clarification" | "insufficient_evidence" | "out_of_scope";
export type RunStatus = "queued" | "running" | "completed" | "failed";
export type CitationRef = { id: string; title: string; document_type: "qa_case" | "reference" | null; department: string | null };
export type Run = {
  id: string;
  question_message_id: string;
  client_request_id: string;
  status: RunStatus;
  response_type: ResponseType | null;
  answer: { message_id: string; text: string } | null;
  citations: CitationRef[];
  error: { code: string; message: string; retryable: boolean } | null;
};
export type Feedback = { helpful: boolean; comment: string | null };
export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  run_id?: string | null;
  response_type?: ResponseType | null;
  citations?: CitationRef[];
  feedback?: Feedback | null;
};
export type Thread = { consultation_id: string; dog: Dog; created_at: string; messages: Message[]; runs: Run[] };
export type CitationDetail = {
  id: string;
  title: string;
  availability: "available" | "unavailable";
  document_type: "qa_case" | "reference" | null;
  department: string | null;
  source_name: string | null;
  source_uri: string | null;
  excerpt: string | null;
  excerpt_allowed: boolean;
};
export type UiConfig = { question_max_chars: number; session_ttl_days: number; run_timeout_seconds: number };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const BASE = "/api/v1";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
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
    throw new ApiError(0, "network", "서버에 연결할 수 없어요. 인터넷 연결을 확인해 주세요.");
  }
  if (res.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON error page */
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_" + res.status, err?.message ?? "일시적인 오류가 발생했어요.");
  }
  return data as T;
}

export const api = {
  config: () => request<UiConfig>("GET", "/config"),
  startSession: () => request<{ expires_at: string }>("POST", "/sessions"),
  session: () => request<{ expires_at: string }>("GET", "/session"),
  createConsultation: (dog: DogIn) => request<{ consultation_id: string; dog: Dog }>("POST", "/consultations", { dog }),
  thread: (cid: string) => request<Thread>("GET", `/consultations/${cid}/messages`),
  ask: (cid: string, text: string, clientRequestId: string) =>
    request<{ run_id: string; status: RunStatus }>("POST", `/consultations/${cid}/questions`, { text, client_request_id: clientRequestId }),
  run: (id: string) => request<Run>("GET", `/runs/${id}`),
  // Serverless hosting: this request runs the queued answer and returns when it is done. Elsewhere a no-op.
  execute: (id: string) => request<Run>("POST", `/runs/${id}/execute`),
  citation: (id: string) => request<CitationDetail>("GET", `/citations/${id}`),
  feedback: (messageId: string, helpful: boolean, comment: string | null) =>
    request<{ feedback_id: string }>("PUT", `/messages/${messageId}/feedback`, { helpful, comment }),
  deleteConsultation: (cid: string) => request<void>("DELETE", `/consultations/${cid}`),
};

export function newRequestId(): string {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

// Typed client for /api/v1 (docs/04 §2). State-changing calls send the client header the server's
// CSRF guard requires; the session cookie is HttpOnly and handled by the browser.

export type Neutered = "yes" | "no" | "unknown";
export type AgeBasis = "day" | "month" | "year" | "estimate";
// Sent when creating a consultation: a birth date as far as known (year -> month -> day) OR an estimated age.
export type DogIn = {
  name: string | null;
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
export type UiConfig = { question_max_chars: number; session_ttl_days: number; run_timeout_seconds: number; profile_ttl_days?: number };

// Settings (P08): the saved dog and 주치동물병원
export type Clinic = {
  id: string;
  source: "public" | "manual";
  name: string;
  road_address: string | null;
  public_phone: string | null;
  business_status: "open" | "paused" | "closed" | "other" | null;
  kakao_place_url: string | null;
  is_partner: boolean;
};
export type Profile = { dog: Dog; clinic: Clinic | null; clinic_phone: string | null; expires_at: string | null };
export type ClinicChoice =
  | { clinic_id: string; kakao_place_id?: string | null; kakao_place_url?: string | null }
  | { manual_name: string; kakao_place_id?: string | null; kakao_place_url?: string | null };
export type ProfileIn = { dog: DogIn; clinic?: ClinicChoice | null; clinic_phone?: string | null };
// Kakao Local result, shown live only (never stored)
export type Place = {
  place_id: string; name: string; road_address: string; address: string; phone: string; distance_m: number | null; place_url: string;
};
// Care (P09)
export type Topic = "ear" | "eye" | "skin" | "oral" | "digestive" | "urinary" | "respiratory" | "other";
export type CareTag = "vet_advised" | "in_progress" | "clarifying" | "insufficient" | "out_of_scope";
export type CareCard = {
  id: string; created_at: string; dog: Dog; first_question: string; message_count: number;
  topic: Topic | null; topics: Topic[]; summary: string | null; vet_advised: boolean | null; summary_fresh: boolean; tags: CareTag[];
};

// M03 guardian–vet link (docs/04 §7-1)
export type VetInfo = { id: string; display_name: string; clinic_name: string | null; intro: string | null; is_test: boolean };
export type VetLinkStatus = "requested" | "accepted" | "expired" | "declined" | "cancelled" | "ended";
export type VetLink = { id: string; status: VetLinkStatus; vet: VetInfo | null; expires_at: string | null; requested_at: string; decided_at: string | null; ended_at: string | null; ended_by: string | null };
export type QuestionStage = "held" | "delivered" | "seen" | "drafting" | "answered" | "cancelled";
export type ShareField = "name" | "breed" | "age" | "weight" | "neutered" | "health_notes";
export type VetReply = { body: string; sent_at: string; corrected: boolean; corrected_at: string | null; history: { kind: "original" | "correction"; body: string; created_at: string }[] };
export type VetQuestion = {
  id: string; link_id: string; status: "sent" | "answered" | "cancelled"; stage?: QuestionStage; link_status?: VetLinkStatus | null; cancelled_reason: "guardian" | "link_ended" | "link_declined" | null;
  vet: VetInfo | null; answer_message_id: string | null; shared_fields: ShareField[]; quality_use_consent: boolean; created_at: string;
  snapshot: { question: string; ai_answer: string; note_to_vet: string | null; dog: Partial<Record<ShareField, string | number>>; shared_at: string; sources: string[] };
  reply: VetReply | null;
  is_test?: boolean;
};
export type M03Availability = { enabled: boolean; vets: boolean; mode: "off" | "pilot" | "on"; link_days?: number };
export type VetQuestionIn = { link_id: string; answer_message_id: string; note_to_vet: string | null; shared_fields: ShareField[]; quality_use_consent: boolean };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const BASE = "/api/v1";

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
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
  // settings: null when nothing is saved for this browser
  profile: () => request<Profile>("GET", "/profile").catch((e: ApiError) => {
    if (e.status === 404) return null;
    throw e;
  }),
  saveProfile: (body: ProfileIn) => request<Profile>("PUT", "/profile", body),
  deleteProfile: () => request<void>("DELETE", "/profile"),
  searchClinics: (p: { q?: string; x?: number; y?: number; emergency?: boolean }) => {
    const qs = new URLSearchParams();
    if (p.q) qs.set("q", p.q);
    if (p.x !== undefined && p.y !== undefined) { qs.set("x", p.x.toFixed(6)); qs.set("y", p.y.toFixed(6)); }
    if (p.emergency) qs.set("emergency", "true");
    return request<{ items: Place[] }>("GET", "/clinics/search?" + qs.toString());
  },
  matchClinic: (p: Place) => request<{ clinic: Clinic | null }>("POST", "/clinics/match",
    { name: p.name, road_address: p.road_address || null, address: p.address || null }),
  care: () => request<{ items: CareCard[] }>("GET", "/care/consultations"),
  summarize: (cid: string) => request<CareCard>("POST", `/consultations/${cid}/summary`),
  // M03
  m03Availability: () => request<M03Availability>("GET", "/m03/availability"),
  joinPilot: (code: string) => request<{ enabled: boolean }>("POST", "/m03/pilot", { code }),
  vets: () => request<{ items: VetInfo[] }>("GET", "/vets"),
  vetLinks: () => request<{ items: VetLink[] }>("GET", "/vet-links"),
  requestVetLink: (vetId: string) => request<VetLink>("POST", "/vet-links", { vet_id: vetId }),
  closeVetLink: (id: string) => request<VetLink>("DELETE", `/vet-links/${id}`),
  vetQuestions: () => request<{ items: VetQuestion[] }>("GET", "/vet-questions"),
  sendVetQuestion: (body: VetQuestionIn) => request<VetQuestion & { access_token: string }>("POST", "/vet-questions", body),
  cancelVetQuestion: (id: string) => request<VetQuestion>("POST", `/vet-questions/${id}/cancel`),
  setVetConsent: (id: string, consent: boolean) => request<VetQuestion>("PATCH", `/vet-questions/${id}`, { quality_use_consent: consent }),
  reissueVetLink: (id: string) => request<{ access_token: string }>("POST", `/vet-questions/${id}/link`),
  deleteVetQuestion: (id: string) => request<void>("DELETE", `/vet-questions/${id}`),
  vetQuestionByToken: (token: string) => request<VetQuestion>("GET", `/vet-questions/by-token/${encodeURIComponent(token)}`),
};

export function newRequestId(): string {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

// MVP1 vet work screens (docs/05 §6): S10 login, S11 list, S12 bundle + labeling, S13 compare/adjudicate,
// S14 knowledge candidates. Separate from the guardian app; reached at /review. Routes live in the hash.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  reviewApi,
  ReviewApiError,
  type Candidate,
  type CandidateBody,
  type Concept,
  type ConceptTag,
  type Guide,
  type ItemDetail,
  type ItemSummary,
  type AutoCheck,
  type Label,
  type LabelBody,
  type Me,
} from "./api";
import "./review.css";

const STATUS: Record<string, string> = {
  draft: "초안",
  vet_reviewed: "수의사 검토 완료",
  independently_verified: "독립 재검증 완료",
  disputed: "이견",
};
const PUBLISH: Record<string, string> = { unapproved: "미승인", approved: "발행 승인", active: "활성화", rejected: "반려", retired: "종료" };
const AUTHOR: Record<string, string> = { vet: "수의사", operator: "운영자(시험)", ai: "AI 제안" };
const ORIGIN: Record<string, string> = { mock: "모의", dataset: "데이터셋", consultation: "실제 상담" };
// all compared fields decide independent re-verification (same list as backend AGREEMENT_FIELDS)
const COMPARE_FIELDS = ["accuracy", "evidence_support", "sufficiency", "guidance", "safety_issue", "reuse_scope"] as const;
const SUMMARY_FIELDS = ["accuracy", "evidence_support", "sufficiency", "guidance", "reuse_scope"] as const;
const AGREEMENT: readonly string[] = COMPARE_FIELDS;

type Route = { name: "list" } | { name: "item"; id: string } | { name: "candidates"; itemId?: string };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "items" && parts[1]) return { name: "item", id: parts[1] };
  if (parts[0] === "candidates") return { name: "candidates", itemId: new URLSearchParams(query ?? "").get("item") ?? undefined };
  return { name: "list" };
}

function Badge({ children, tone }: { children: React.ReactNode; tone?: "mock" | "ai" | "draft" | "ok" | "warn" | "muted" }) {
  return <span className={`rv-badge rv-badge-${tone ?? "muted"}`}>{children}</span>;
}

// ------------------------------------------------------------------ app shell
export function ReviewApp() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [route, setRoute] = useState<Route>(parseHash);
  const [guide, setGuide] = useState<Guide | null>(null);
  const [concepts, setConcepts] = useState<Concept[]>([]);

  useEffect(() => {
    document.title = "TwinVet 검토 작업";
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    reviewApi.me().then(setMe).catch(() => setMe(null));
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!me) return;
    reviewApi.guide().then(setGuide).catch(() => undefined);
    reviewApi.concepts().then((r) => setConcepts(r.items)).catch(() => undefined);
  }, [me]);

  const onAuthError = useCallback((e: unknown) => {
    if (e instanceof ReviewApiError && e.status === 401) setMe(null);
  }, []);

  if (me === undefined) return null;
  if (me === null) return <LoginScreen onLogin={setMe} />;
  return (
    <div className="rv">
      <header className="rv-header">
        <div className="rv-header-inner">
          <a className="rv-brand" href="#/">TwinVet 검토 작업</a>
          <nav className="rv-nav" aria-label="작업 메뉴">
            <a href="#/" aria-current={route.name !== "candidates" ? "page" : undefined}>검토 목록</a>
            <a href="#/candidates" aria-current={route.name === "candidates" ? "page" : undefined}>지식 후보</a>
          </nav>
          <div className="rv-me">
            <span>{me.display_name}</span>
            <Badge tone={me.role === "vet" ? "ok" : "warn"}>{me.role === "vet" ? "수의사" : "운영자"}</Badge>
            {!me.production && <Badge tone="mock">시험 환경</Badge>}
            <button className="btn btn-ghost" onClick={() => reviewApi.logout().finally(() => setMe(null))}>로그아웃</button>
          </div>
        </div>
        {me.role === "operator" && (
          <p className="rv-banner">운영자 라벨은 화면·흐름 시험용이며 검토 완료로 세지 않습니다. 작업 시간은 '운영자 시연 시간'으로만 기록됩니다.</p>
        )}
      </header>
      <main className="rv-main">
        {route.name === "list" && <ItemList onAuthError={onAuthError} />}
        {route.name === "item" && guide && <ItemScreen key={route.id} id={route.id} me={me} guide={guide} concepts={concepts} onAuthError={onAuthError} />}
        {route.name === "candidates" && <CandidateScreen me={me} itemId={route.itemId} onAuthError={onAuthError} />}
      </main>
    </div>
  );
}

// ------------------------------------------------------------------ S10 login
function LoginScreen({ onLogin }: { onLogin: (m: Me) => void }) {
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      onLogin(await reviewApi.login(key));
    } catch (x) {
      setErr(x instanceof Error ? x.message : "로그인할 수 없습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rv rv-login">
      <form className="card rv-login-card" onSubmit={submit}>
        <h1>TwinVet 검토 작업</h1>
        <p className="help">운영자에게 받은 접근 키를 입력하세요. 보호자용 화면과는 별개입니다.</p>
        <div className="field">
          <label htmlFor="rv-key">접근 키</label>
          <input id="rv-key" className="input" type="password" autoComplete="off" value={key}
            onChange={(e) => setKey(e.target.value)} aria-invalid={err ? true : undefined} aria-describedby={err ? "rv-key-err" : undefined} />
          {err && <p className="error-text" id="rv-key-err" role="alert">{err}</p>}
        </div>
        <button className="btn btn-primary btn-block" disabled={busy || key.trim().length < 10}>로그인</button>
      </form>
    </div>
  );
}

// ------------------------------------------------------------------ S11 list
function ItemList({ onAuthError }: { onAuthError: (e: unknown) => void }) {
  const [status, setStatus] = useState<string>("");
  const [items, setItems] = useState<ItemSummary[] | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setItems(null);
    reviewApi.items(status || undefined).then((r) => setItems(r.items)).catch((e) => {
      onAuthError(e);
      setErr(e instanceof Error ? e.message : "목록을 불러올 수 없습니다.");
    });
  }, [status, onAuthError]);
  return (
    <section aria-labelledby="rv-list-title">
      <h1 id="rv-list-title" className="rv-h1">검토 목록</h1>
      <div className="rv-filters" role="group" aria-label="검토 상태">
        {["", "draft", "vet_reviewed", "independently_verified", "disputed"].map((s) => (
          <button key={s} className="chip" aria-pressed={status === s} onClick={() => setStatus(s)}>{s ? STATUS[s] : "전체"}</button>
        ))}
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
      {items && items.length === 0 && <p className="help">해당하는 검토 묶음이 없습니다.</p>}
      <ul className="rv-list">
        {items?.map((i) => (
          <li key={i.id}>
            <a className="card rv-row" href={`#/items/${i.id}`}>
              <span className="rv-row-badges">
                <Badge tone={i.is_mock ? "mock" : "muted"}>{ORIGIN[i.origin]}</Badge>
                <Badge tone={i.review_status === "disputed" ? "warn" : i.review_status === "draft" ? "draft" : "ok"}>{STATUS[i.review_status]}</Badge>
              </span>
              <span className="rv-row-q">{i.question}</span>
              <span className="rv-row-mine">{i.my_label === "submitted" ? "내 라벨 제출함" : i.my_label === "draft" ? "임시 저장" : "미작성"}</span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

// ------------------------------------------------------------------ S12 bundle + label, S13 compare
function emptyLabel(): LabelBody {
  return { accuracy: null, evidence_support: null, sufficiency: null, guidance: null, safety_issue: false, revised_answer: "",
    revision_reason: "", revision_evidence: [], reuse_scope: null, note: "", concepts: [], active_seconds: 0 };
}

function fromLabel(l: Label): LabelBody {
  return { accuracy: l.accuracy, evidence_support: l.evidence_support, sufficiency: l.sufficiency, guidance: l.guidance,
    safety_issue: l.safety_issue, revised_answer: l.revised_answer ?? "", revision_reason: l.revision_reason ?? "",
    revision_evidence: l.revision_evidence, reuse_scope: l.reuse_scope, note: l.note ?? "", concepts: l.concepts, active_seconds: 0 };
}

// counts seconds while the tab is visible and the reviewer interacted within the last 2 minutes
function useActiveSeconds() {
  const secs = useRef(0);
  const last = useRef(Date.now());
  useEffect(() => {
    const touch = () => { last.current = Date.now(); };
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible" && Date.now() - last.current < 120_000) secs.current += 1;
    }, 1000);
    window.addEventListener("keydown", touch);
    window.addEventListener("pointerdown", touch);
    return () => {
      window.clearInterval(t);
      window.removeEventListener("keydown", touch);
      window.removeEventListener("pointerdown", touch);
    };
  }, []);
  return {
    take: () => {
      const s = Math.min(secs.current, 3600);
      secs.current = 0;
      return s;
    },
  };
}

function ItemScreen({ id, me, guide, concepts, onAuthError }: { id: string; me: Me; guide: Guide; concepts: Concept[]; onAuthError: (e: unknown) => void }) {
  const [d, setD] = useState<ItemDetail | null>(null);
  const [form, setForm] = useState<LabelBody>(emptyLabel);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const timer = useActiveSeconds();

  const load = useCallback((x: ItemDetail) => {
    setD(x);
    if (x.my_label) setForm(fromLabel(x.my_label));
  }, []);
  useEffect(() => {
    reviewApi.item(id).then(load).catch((e) => { onAuthError(e); setMsg(e instanceof Error ? e.message : "불러올 수 없습니다."); });
  }, [id, load, onAuthError]);

  const submitted = d?.my_label?.state === "submitted";
  const send = async (kind: "save" | "submit") => {
    setBusy(true);
    setMsg("");
    setErrors({});
    try {
      const body = { ...form, active_seconds: timer.take() };
      const x = kind === "save" ? await reviewApi.save(id, body) : await reviewApi.submit(id, body);
      load(x);
      setMsg(kind === "save" ? "임시 저장했습니다." : "제출했습니다. 이제 다른 라벨과 비교할 수 있습니다.");
    } catch (e) {
      onAuthError(e);
      if (e instanceof ReviewApiError) {
        setErrors(e.fields);
        setMsg(e.message);
      }
    } finally {
      setBusy(false);
    }
  };

  if (!d) return <p className="help">{msg || "불러오는 중…"}</p>;
  const it = d.item;
  return (
    <section aria-labelledby="rv-item-title">
      <p><a href="#/">← 검토 목록</a></p>
      <h1 id="rv-item-title" className="rv-h1">
        검토 묶음 <Badge tone={it.is_mock ? "mock" : "muted"}>{ORIGIN[it.origin]}</Badge>{" "}
        <Badge tone={it.review_status === "disputed" ? "warn" : it.review_status === "draft" ? "draft" : "ok"}>{STATUS[it.review_status]}</Badge>
      </h1>
      <div className="rv-split">
        <article className="card rv-pane" aria-label="검토 묶음">
          <h2 className="rv-h2">반려견 정보 <small>({it.origin === "dataset" ? "질문 내용에서 추정" : "보호자 입력"})</small></h2>
          <p className="rv-dog">{Object.entries(it.dog).map(([k, v]) => `${DOG_LABEL[k] ?? k}: ${String(v)}`).join(" · ") || "없음"}</p>
          {it.history.length > 0 && (
            <>
              <h2 className="rv-h2">이전 대화</h2>
              <ol className="rv-history">
                {it.history.map((m, i) => <li key={i}><b>{m.role === "user" ? "보호자" : "AI"}</b> {m.content}</li>)}
              </ol>
            </>
          )}
          <h2 className="rv-h2">보호자 질문</h2>
          <p className="rv-quote">{it.question}</p>
          <h2 className="rv-h2">AI 답변 <Badge>{it.response_type ?? "-"}</Badge></h2>
          <p className="rv-quote rv-answer">{it.ai_answer}</p>
          <h2 className="rv-h2">인용 근거</h2>
          {it.citations.length === 0 ? (
            <p className="help">인용 근거 없음</p>
          ) : (
            <ol className="rv-cites">
              {it.citations.map((c, i) => (
                <li key={i}>
                  {c.title}{c.department ? ` (${c.department})` : ""}
                  {c.excerpt && (
                    <details className="rv-excerpt">
                      <summary>근거 내용 보기</summary>
                      <p>{c.excerpt}{c.excerpt.length >= 600 ? "…" : ""}</p>
                    </details>
                  )}
                </li>
              ))}
            </ol>
          )}
          {d.auto_check && <AutoCheckView c={d.auto_check} />}
          <p className="help">버전: {Object.entries(it.versions).map(([k, v]) => `${k}=${v}`).join(", ") || "-"}</p>
          {d.ai_suggestion && !submitted && (
            <div className="rv-ai">
              <h2 className="rv-h2"><Badge tone="ai">AI 제안</Badge> 참고용 초안</h2>
              <LabelSummary l={d.ai_suggestion} guide={guide} concepts={concepts} />
            </div>
          )}
        </article>
        <div className="rv-pane">
          {submitted ? (
            <div className="card rv-card">
              <h2 className="rv-h2">내 라벨 (제출됨)</h2>
              <LabelSummary l={d.my_label!} guide={guide} concepts={concepts} />
            </div>
          ) : (
            <LabelForm form={form} setForm={setForm} errors={errors} guide={guide} concepts={concepts} />
          )}
          {!submitted && (
            <div className="rv-actions">
              <button className="btn" disabled={busy} onClick={() => send("save")}>임시 저장</button>
              <button className="btn btn-primary" disabled={busy} onClick={() => send("submit")}>제출</button>
            </div>
          )}
          {msg && <p className={Object.keys(errors).length ? "error-text" : "help"} role="status">{msg}</p>}
          {!submitted && d.other_label_count > 0 && (
            <p className="help">다른 라벨 {d.other_label_count}건이 있습니다. 독립 라벨링을 위해 제출 후에 보여 드립니다.</p>
          )}
          {submitted && <Compare d={d} me={me} guide={guide} concepts={concepts} onChange={load} onAuthError={onAuthError} />}
        </div>
      </div>
    </section>
  );
}

const ISSUE: Record<string, string> = {
  not_answered: "답변하지 않음",
  retrieval_miss: "검색 실패",
  unsupported_claim: "근거 없는 주장",
  citation_mismatch: "출처 연결 오류",
  condition_missing: "조건·주의사항 누락",
  forbidden_content: "금지 내용 포함",
  reference_conflict: "원천 답변과 충돌",
  medical_hold: "의학적 판단 필요(보류)",
};
const SUPPORT: Record<string, string> = { supported: "근거 있음", partial: "일부", unsupported: "근거 없음" };
const REFERENCE: Record<string, string> = { agree: "일치", partial: "일부 누락", conflict: "충돌", none: "비교 없음" };
const RETRIEVAL: Record<string, string> = { relevant: "질문과 맞음", partial: "일부만 맞음", irrelevant: "맞지 않음" };

function AutoCheckView({ c }: { c: AutoCheck }) {
  return (
    <div className="rv-check-box">
      <h2 className="rv-h2"><Badge tone="ai">자동 점검 결과</Badge> <small>AI가 출처와 대조한 점검 · 안전성·수의학적 검증 아님</small></h2>
      <p className="rv-row-badges">
        {c.issues.length === 0 ? <Badge tone="ok">발견된 문제 없음</Badge> : c.issues.map((t) => <Badge key={t} tone="warn">{ISSUE[t] ?? t}</Badge>)}
      </p>
      <p>{c.summary}</p>
      <dl className="rv-dl">
        <div><dt>검색 근거</dt><dd>{RETRIEVAL[c.retrieval]}{c.top1_score != null ? ` (1위 유사도 ${c.top1_score.toFixed(3)})` : ""}</dd></div>
        {c.missing_conditions.length > 0 && <div><dt>빠진 조건</dt><dd>{c.missing_conditions.join(" / ")}</dd></div>}
        {c.citation_mismatch.length > 0 && <div><dt>연결 오류 근거</dt><dd>{c.citation_mismatch.map((n) => `[${n}]`).join(" ")}</dd></div>}
        {c.forbidden_content.length > 0 && <div><dt>금지 내용</dt><dd>{c.forbidden_content.join(" / ")}</dd></div>}
        <div><dt>원천 답변과</dt><dd>{REFERENCE[c.reference_agreement]}</dd></div>
      </dl>
      {c.claims.length > 0 && (
        <details className="rv-excerpt">
          <summary>주장별 근거 대조 ({c.claims.length})</summary>
          <ul className="rv-history">
            {c.claims.map((x, i) => <li key={i}><b>{SUPPORT[x.support]}</b> {x.claim}{x.sources.length ? ` [${x.sources.join(", ")}]` : ""}</li>)}
          </ul>
        </details>
      )}
      {c.reference_answer && (
        <details className="rv-excerpt">
          <summary>원천 수의사 답변 보기 (AI Hub, 참고 비교용)</summary>
          <p>{c.reference_answer}</p>
        </details>
      )}
      <details className="rv-excerpt">
        <summary>검색된 근거 {c.retrieved.length}건 (번호는 검색 순위)</summary>
        <ol className="rv-cites">
          {c.retrieved.map((h) => <li key={h.rank}>{h.title}{h.department ? ` (${h.department})` : ""} · {h.score.toFixed(3)}{h.cited ? " · 인용함" : ""}</li>)}
        </ol>
      </details>
    </div>
  );
}

const DOG_LABEL: Record<string, string> = { breed: "품종", age_months: "나이(개월)", weight_kg: "체중(kg)", neutered: "중성화", health_notes: "건강 메모", name: "이름", age_basis: "나이 기준" };

function Choice({ name, form, setForm, guide, error }: { name: keyof LabelBody; form: LabelBody; setForm: (f: LabelBody) => void; guide: Guide; error?: string }) {
  const g = guide.fields[name as string];
  return (
    <fieldset className="rv-field" aria-invalid={error ? true : undefined}>
      <legend>{g.label} <span className="req">*</span></legend>
      <p className="help">{g.help}</p>
      <div className="rv-options">
        {Object.entries(g.options ?? {}).map(([v, [label, desc]]) => (
          <label key={v} className="rv-option">
            <input type="radio" name={name as string} value={v} checked={form[name] === v}
              onChange={() => setForm({ ...form, [name]: v })} />
            <span><b>{label}</b> <small>{desc}</small></span>
          </label>
        ))}
      </div>
      {error && <p className="error-text">{error}</p>}
    </fieldset>
  );
}

function LabelForm({ form, setForm, errors, guide, concepts }: { form: LabelBody; setForm: (f: LabelBody) => void; errors: Record<string, string>; guide: Guide; concepts: Concept[] }) {
  const g = guide.fields;
  return (
    <form className="card rv-card" onSubmit={(e) => e.preventDefault()} aria-label="라벨 양식">
      <p className="help">라벨링 지침 {guide.version}</p>
      <Choice name="accuracy" form={form} setForm={setForm} guide={guide} error={errors.accuracy} />
      <Choice name="evidence_support" form={form} setForm={setForm} guide={guide} error={errors.evidence_support} />
      <Choice name="sufficiency" form={form} setForm={setForm} guide={guide} error={errors.sufficiency} />
      <Choice name="guidance" form={form} setForm={setForm} guide={guide} error={errors.guidance} />
      <div className="rv-field">
        <label className="rv-check">
          <input type="checkbox" checked={form.safety_issue} onChange={(e) => setForm({ ...form, safety_issue: e.target.checked })} />
          <b>{g.safety_issue.label}</b>
        </label>
        <p className="help">{g.safety_issue.help}</p>
        {errors.safety_issue && <p className="error-text">{errors.safety_issue}</p>}
      </div>
      <TextArea id="revised_answer" label={g.revised_answer.label} help={g.revised_answer.help} value={form.revised_answer ?? ""}
        onChange={(v) => setForm({ ...form, revised_answer: v })} error={errors.revised_answer} rows={4} />
      <TextArea id="revision_reason" label={g.revision_reason.label} help={g.revision_reason.help} value={form.revision_reason ?? ""}
        onChange={(v) => setForm({ ...form, revision_reason: v })} error={errors.revision_reason} rows={2} />
      <TextArea id="revision_evidence" label={g.revision_evidence.label} help={g.revision_evidence.help + " 한 줄에 하나씩."}
        value={form.revision_evidence.join("\n")} rows={2} error={errors.revision_evidence}
        onChange={(v) => setForm({ ...form, revision_evidence: v.split("\n").map((s) => s.trim()).filter(Boolean) })} />
      <Choice name="reuse_scope" form={form} setForm={setForm} guide={guide} error={errors.reuse_scope} />
      <ConceptPicker tags={form.concepts} onChange={(c) => setForm({ ...form, concepts: c })} guide={guide} concepts={concepts} error={errors.concepts} />
      <TextArea id="note" label="메모(선택)" help="검토자 메모. 보호자에게 보이지 않습니다." value={form.note ?? ""}
        onChange={(v) => setForm({ ...form, note: v })} rows={2} />
    </form>
  );
}

function TextArea({ id, label, help, value, onChange, error, rows }: { id: string; label: string; help: string; value: string; onChange: (v: string) => void; error?: string; rows: number }) {
  return (
    <div className="field rv-field">
      <label htmlFor={`rv-${id}`}>{label}</label>
      <p className="help" id={`rv-${id}-help`}>{help}</p>
      <textarea id={`rv-${id}`} className="textarea rv-textarea" rows={rows} value={value} onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined} aria-describedby={`rv-${id}-help`} />
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function ConceptPicker({ tags, onChange, guide, concepts, error }: { tags: ConceptTag[]; onChange: (t: ConceptTag[]) => void; guide: Guide; concepts: Concept[]; error?: string }) {
  const [q, setQ] = useState("");
  const [assertion, setAssertion] = useState("owner_reported");
  const g = guide.fields.concepts;
  const byCode = useMemo(() => Object.fromEntries(concepts.map((c) => [c.code, c])), [concepts]);
  const hits = useMemo(() => {
    const s = q.trim();
    if (!s) return [];
    return concepts.filter((c) => c.label.includes(s) || c.code.includes(s) || c.synonyms.some((x) => x.includes(s))).slice(0, 8);
  }, [q, concepts]);
  return (
    <div className="rv-field">
      <label htmlFor="rv-concept-q"><b>{g.label}</b></label>
      <p className="help">{g.help}</p>
      <div className="rv-concept-row">
        <input id="rv-concept-q" className="input" placeholder="예: 구토, 귀, 내원" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input rv-select" value={assertion} onChange={(e) => setAssertion(e.target.value)} aria-label="정보 출처">
          {Object.entries(g.assertions ?? {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </div>
      {hits.length > 0 && (
        <ul className="rv-hits">
          {hits.map((c) => (
            <li key={c.code}>
              <button type="button" className="chip" onClick={() => {
                if (!tags.some((t) => t.code === c.code && t.assertion === assertion)) onChange([...tags, { code: c.code, assertion }]);
                setQ("");
              }}>
                {c.label} <small>{c.scheme}</small> {c.status === "draft" && <Badge tone="draft">초안 개념</Badge>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <ul className="rv-tags">
        {tags.map((t, i) => (
          <li key={`${t.code}-${t.assertion}`} className="chip">
            {byCode[t.code]?.label ?? t.code} · {g.assertions?.[t.assertion] ?? t.assertion}
            <button type="button" className="rv-x" aria-label={`${byCode[t.code]?.label ?? t.code} 태그 삭제`}
              onClick={() => onChange(tags.filter((_, j) => j !== i))}>×</button>
          </li>
        ))}
      </ul>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}

function optLabel(guide: Guide, field: string, v: string | null) {
  if (!v) return "-";
  return guide.fields[field]?.options?.[v]?.[0] ?? v;
}

function LabelSummary({ l, guide, concepts }: { l: Label; guide: Guide; concepts: Concept[] }) {
  const byCode = Object.fromEntries(concepts.map((c) => [c.code, c.label]));
  return (
    <dl className="rv-dl">
      {SUMMARY_FIELDS.map((f) => (
        <div key={f}><dt>{guide.fields[f].label}</dt><dd>{optLabel(guide, f, l[f])}</dd></div>
      ))}
      <div><dt>안전 문제</dt><dd>{l.safety_issue ? "있음" : "없음"}</dd></div>
      {l.revised_answer && <div><dt>수정안</dt><dd>{l.revised_answer}</dd></div>}
      {l.revision_reason && <div><dt>수정 사유</dt><dd>{l.revision_reason}</dd></div>}
      {l.concepts.length > 0 && (
        <div><dt>개념 태그</dt><dd>{l.concepts.map((c) => `${byCode[c.code] ?? c.code}(${guide.fields.concepts.assertions?.[c.assertion] ?? c.assertion})`).join(", ")}</dd></div>
      )}
    </dl>
  );
}

function Compare({ d, me, guide, concepts, onChange, onAuthError }: { d: ItemDetail; me: Me; guide: Guide; concepts: Concept[]; onChange: (x: ItemDetail) => void; onAuthError: (e: unknown) => void }) {
  const [type, setType] = useState(d.item.dispute_type ?? "");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");
  const labels: Label[] = [d.my_label!, ...d.other_labels, ...(d.ai_suggestion ? [d.ai_suggestion] : [])];
  const vets = labels.filter((l) => l.author_kind === "vet");
  const mismatch = (f: string) => new Set(vets.map((l) => (l as Record<string, unknown>)[f])).size > 1;
  const reusable = d.my_label?.reuse_scope === "conditional" || d.my_label?.reuse_scope === "general_candidate";
  return (
    <div className="card rv-card" aria-labelledby="rv-compare">
      <h2 id="rv-compare" className="rv-h2">라벨 비교</h2>
      <div className="rv-table-wrap">
        <table className="rv-table">
          <thead>
            <tr><th scope="col">항목</th>{labels.map((l, i) => <th key={l.id} scope="col">{l.id === d.my_label!.id ? "나" : `${AUTHOR[l.author_kind]} ${i}`}</th>)}</tr>
          </thead>
          <tbody>
            {COMPARE_FIELDS.map((f) => (
              <tr key={f}>
                <th scope="row">{guide.fields[f].label}{AGREEMENT.includes(f) && mismatch(f) && <> <Badge tone="warn">불일치</Badge></>}</th>
                {labels.map((l) => <td key={l.id}>{f === "safety_issue" ? (l.safety_issue ? "있음" : "없음") : optLabel(guide, f, l[f])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="help">독립 재검증 판정은 수의사 라벨의 {AGREEMENT.map((f) => guide.fields[f].label).join("·")}을 비교하고, 하나라도 다르면 이견입니다. 수정안·개념 태그 차이는 지식 후보를 작성·승인할 때 맞춥니다. 운영자·AI 라벨은 세지 않습니다.</p>
      {d.item.review_status === "disputed" && (
        <form className="rv-field" onSubmit={async (e) => {
          e.preventDefault();
          try {
            onChange(await reviewApi.adjudicate(d.item.id, type, note));
            setMsg("이견 유형을 기록했습니다.");
          } catch (x) {
            onAuthError(x);
            setMsg(x instanceof Error ? x.message : "기록할 수 없습니다.");
          }
        }}>
          <fieldset className="rv-field">
            <legend>이견 유형 (다수결로 정하지 않습니다)</legend>
            {Object.entries(guide.dispute_types).map(([k, v]) => (
              <label key={k} className="rv-option"><input type="radio" name="dispute" value={k} checked={type === k} onChange={() => setType(k)} /> <span>{v}</span></label>
            ))}
          </fieldset>
          <textarea className="textarea rv-textarea" rows={2} placeholder="조정 메모" value={note} onChange={(e) => setNote(e.target.value)} aria-label="조정 메모" />
          <button className="btn" disabled={!type}>이견 기록</button>
          {msg && <p className="help" role="status">{msg}</p>}
        </form>
      )}
      {reusable && (
        <p><a className="btn" href={`#/candidates?item=${d.item.id}`}>이 사례로 지식 후보 만들기</a></p>
      )}
      {me.role === "operator" && <p className="help">운영자 라벨: {d.my_label?.active_seconds ?? 0}초(운영자 시연 시간)</p>}
      <details><summary>모든 라벨 자세히</summary>{labels.map((l) => <div key={l.id} className="rv-detail"><Badge tone={l.author_kind === "ai" ? "ai" : "muted"}>{AUTHOR[l.author_kind]}</Badge><LabelSummary l={l} guide={guide} concepts={concepts} /></div>)}</details>
    </div>
  );
}

// ------------------------------------------------------------------ S14 knowledge candidates
function emptyCandidate(itemId?: string): CandidateBody {
  return { item_id: itemId ?? null, kind: "case", title: "", content: "", conditions: {}, evidence: [] };
}

function CandidateScreen({ me, itemId, onAuthError }: { me: Me; itemId?: string; onAuthError: (e: unknown) => void }) {
  const [items, setItems] = useState<Candidate[] | null>(null);
  const [editing, setEditing] = useState<{ id: string | null; body: CandidateBody } | null>(itemId ? { id: null, body: emptyCandidate(itemId) } : null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<Record<string, string>>({});
  const refresh = useCallback(() => {
    reviewApi.candidates().then((r) => setItems(r.items)).catch(onAuthError);
  }, [onAuthError]);
  useEffect(refresh, [refresh]);

  const save = async () => {
    if (!editing) return;
    setErrors({});
    try {
      if (editing.id) await reviewApi.updateCandidate(editing.id, editing.body);
      else await reviewApi.createCandidate(editing.body);
      setEditing(null);
      refresh();
    } catch (e) {
      onAuthError(e);
      if (e instanceof ReviewApiError) setErrors({ ...e.fields, _: e.message });
    }
  };
  const approve = async (c: Candidate) => {
    try {
      await reviewApi.approve(c.id);
      setMsg({ ...msg, [c.id]: "발행 승인했습니다. 검색 영향 점검과 활성화는 운영자가 진행합니다." });
      refresh();
    } catch (e) {
      onAuthError(e);
      setMsg({ ...msg, [c.id]: e instanceof Error ? e.message : "승인할 수 없습니다." });
    }
  };

  return (
    <section aria-labelledby="rv-cand-title">
      <h1 id="rv-cand-title" className="rv-h1">지식 후보</h1>
      <p className="help">승인은 지금 내용에 묶입니다. 승인 뒤 내용을 고치면 다시 승인해야 합니다. 후보를 작성·수정한 사람은 승인할 수 없습니다. 적용 조건은 발행되는 지식 본문에 함께 들어갑니다. 활성화는 검색 영향 점검(품질 판정 아님)을 통과한 뒤 운영자 스크립트로 합니다.</p>
      {!editing && <button className="btn" onClick={() => setEditing({ id: null, body: emptyCandidate() })}>새 후보</button>}
      {editing && (
        <form className="card rv-card" onSubmit={(e) => { e.preventDefault(); save(); }} aria-label="지식 후보 양식">
          <fieldset className="rv-field">
            <legend>종류</legend>
            <div className="segmented">
              {(["case", "general"] as const).map((k) => (
                <button type="button" key={k} aria-pressed={editing.body.kind === k}
                  onClick={() => setEditing({ ...editing, body: { ...editing.body, kind: k } })}>{k === "case" ? "사례" : "일반 지식"}</button>
              ))}
            </div>
          </fieldset>
          {editing.body.item_id && <p className="help">출처 검토 묶음: <a href={`#/items/${editing.body.item_id}`}>{editing.body.item_id.slice(0, 8)}</a></p>}
          <TextArea id="c-title" label="제목" help="2~200자" rows={1} value={editing.body.title} error={errors.title}
            onChange={(v) => setEditing({ ...editing, body: { ...editing.body, title: v } })} />
          <TextArea id="c-content" label="내용" help="일반화한 안내. 상담 원문·개인 정보를 넣지 않습니다." rows={5} value={editing.body.content}
            error={errors.content} onChange={(v) => setEditing({ ...editing, body: { ...editing.body, content: v } })} />
          <TextArea id="c-cond" label="적용 조건" help="예: 소형견, 7세 이상. 한 줄에 하나씩." rows={2}
            value={((editing.body.conditions.items as string[] | undefined) ?? []).join("\n")}
            onChange={(v) => setEditing({ ...editing, body: { ...editing.body, conditions: { ...editing.body.conditions, items: v.split("\n").map((s) => s.trim()).filter(Boolean) } } })} />
          <TextArea id="c-evidence" label="근거" help="출처. 한 줄에 하나씩(필수)." rows={2} value={editing.body.evidence.join("\n")} error={errors.evidence}
            onChange={(v) => setEditing({ ...editing, body: { ...editing.body, evidence: v.split("\n").map((s) => s.trim()).filter(Boolean) } })} />
          {errors._ && <p className="error-text" role="alert">{errors._}</p>}
          <div className="rv-actions">
            <button type="button" className="btn" onClick={() => setEditing(null)}>취소</button>
            <button className="btn btn-primary">저장</button>
          </div>
        </form>
      )}
      <ul className="rv-list">
        {items?.map((c) => (
          <li key={c.id} className="card rv-card">
            <p className="rv-row-badges">
              {c.is_mock && <Badge tone="mock">모의</Badge>}
              <Badge>{c.kind === "case" ? "사례" : "일반 지식"}</Badge>
              <Badge tone={c.publish_status === "active" ? "ok" : c.publish_status === "approved" ? "ok" : "draft"}>{PUBLISH[c.publish_status]}</Badge>
              {c.eval_passed != null && (
                <Badge tone={c.eval_passed ? "ok" : "warn"}>
                  검색 영향 점검 {c.eval_result?.valid === false ? "평가 불가" : c.eval_passed ? "통과" : "실패"}
                </Badge>
              )}
            </p>
            <h2 className="rv-h2">{c.title}</h2>
            <p>{c.content}</p>
            <p className="help">근거: {c.evidence.join(", ")}</p>
            <div className="rv-actions">
              {c.publish_status !== "rejected" && c.publish_status !== "retired" && (
                <button className="btn" onClick={() => setEditing({ id: c.id, body: { item_id: c.item_id, kind: c.kind, title: c.title, content: c.content, conditions: c.conditions, evidence: c.evidence } })}>수정</button>
              )}
              {c.publish_status === "unapproved" && <button className="btn btn-primary" onClick={() => approve(c)}>발행 승인</button>}
            </div>
            {msg[c.id] && <p className="help" role="status">{msg[c.id]}</p>}
            {me.role === "operator" && c.publish_status === "approved" && (
              <p className="help">운영자: <code>python -m scripts.hitl_admin evaluate --candidate {c.id} --embedder openai</code> 후 activate</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

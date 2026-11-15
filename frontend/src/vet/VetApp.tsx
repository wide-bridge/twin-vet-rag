// M03 vet screens (docs/05 §6 M03: S05 수의사 홈, S06 질문 상세·회신). Reached on vet.pawcheck.ai or /vet.
// While M03 is off (server switch) only a "준비 중" notice is shown. Separate from HITL review accounts.
import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "../api";
import { formatAge } from "../lib/age";
import "../review/review.css";

type Vet = { id: string; display_name: string; clinic_name: string | null; intro: string | null; is_test: boolean };
type Link = { id: string; status: string; guardian_ref: string; requested_at: string; open_questions: number; expires_at?: string | null };
type Reply = { body: string; sent_at: string; corrected: boolean; history: { kind: string; body: string; created_at: string }[] };
type QItem = { id: string; status: "sent" | "answered" | "cancelled"; cancelled_reason: string | null; created_at: string; replied_at: string | null; guardian_ref: string; question_preview?: string; is_test?: boolean; ai_reviewed?: boolean };
type Verdict = "correct" | "needs_fix" | "different";
type AiReview = { id: string; verdict: Verdict; safety_issue: boolean; corrected_answer: string | null; comment: string | null; created_at: string };
type QDetail = QItem & {
  snapshot?: { question: string; ai_answer: string; note_to_vet: string | null; sources: string[]; shared_at: string; dog: Record<string, string | number> };
  reply_draft?: string | null;
  reply?: Reply | null;
  quality_use_consent?: boolean;
  ai_review?: AiReview | null;
  memo?: { body: string; updated_at: string } | null;
};
type Booking = { phone: string | null; hours: string | null; note: string | null };
const bookingText = (b: Booking | null) => {
  if (!b || !(b.phone || b.hours || b.note)) return "";
  return ["[예약 안내]", b.phone && `전화: ${b.phone}`, b.hours && `진료 시간: ${b.hours}`, b.note].filter(Boolean).join("\n");
};

const V = "/vet";
const at = (iso: string) => new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
const STATUS: Record<string, string> = { sent: "회신 대기", answered: "회신 완료", cancelled: "취소됨" };
const VERDICT: [Verdict, string][] = [["correct", "맞아요"], ["needs_fix", "보완 필요"], ["different", "다르게 봐야 해요"]];
const DOG_LABEL: Record<string, string> = { name: "이름", breed: "품종", age: "나이", weight: "체중", neutered: "중성화", health_notes: "건강 메모" };
const NEUTERED: Record<string, string> = { yes: "했음", no: "안 했음", unknown: "모름" };
const dogValue = (k: string, v: string | number) =>
  k === "age" && typeof v === "number" ? formatAge(v) : k === "weight" ? `${v}kg` : k === "neutered" ? NEUTERED[String(v)] ?? String(v) : String(v);

export function VetApp() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [me, setMe] = useState<Vet | null | undefined>(undefined);
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => {
    request<{ enabled: boolean; vets: boolean }>("GET", "/m03/availability")
      .then((r) => {
        setEnabled(r.vets);  // vet screens open with either switch (관리자 상담 / 수의사 상담)
        if (r.vets) request<Vet>("GET", `${V}/me`).then(setMe).catch(() => setMe(null));
      })
      .catch(() => setEnabled(false));
  }, []);
  const onAuth = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) setMe(null);
  }, []);

  if (enabled === null) return null;
  if (!enabled) {
    return (
      <div className="rv rv-login">
        <div className="card rv-login-card">
          <h1>TwinVet 수의사</h1>
          <p className="help">보호자 질문에 답하는 수의사 화면은 준비 중입니다.</p>
        </div>
      </div>
    );
  }
  if (me === undefined) return null;
  if (me === null) return <Login onLogin={setMe} />;
  return (
    <div className="rv">
      <header className="rv-header">
        <div className="rv-header-inner">
          <a className="rv-brand" href="#/" onClick={() => setOpenId(null)}>TwinVet 수의사</a>
          <div className="rv-me">
            <span>{me.display_name}{me.clinic_name ? ` · ${me.clinic_name}` : ""}</span>
            {me.is_test && <span className="rv-badge rv-badge-mock">시험 계정</span>}
            <button className="btn btn-ghost" onClick={() => request("POST", `${V}/logout`).finally(() => setMe(null))}>로그아웃</button>
          </div>
        </div>
      </header>
      <main className="rv-main">
        {openId ? <QuestionScreen id={openId} onBack={() => setOpenId(null)} onAuth={onAuth} /> : <Home onOpen={setOpenId} onAuth={onAuth} />}
      </main>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (v: Vet) => void }) {
  const [key, setKey] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      onLogin(await request<Vet>("POST", `${V}/login`, { access_key: key }));
    } catch (x) {
      setErr(x instanceof Error ? x.message : "로그인할 수 없습니다.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rv rv-login">
      <form className="card rv-login-card" onSubmit={submit}>
        <h1>TwinVet 수의사</h1>
        <p className="help">운영자에게 받은 수의사 접근 키를 입력하세요. HITL 검토 계정과는 별개입니다.</p>
        <div className="field">
          <label htmlFor="vt-key">접근 키</label>
          <input id="vt-key" className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
            aria-invalid={err ? true : undefined} aria-describedby={err ? "vt-key-err" : undefined} />
          {err && <p className="error-text" id="vt-key-err" role="alert">{err}</p>}
        </div>
        <button className="btn btn-primary btn-block" disabled={busy || key.trim().length < 10}>로그인</button>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------- S05 home: link requests + received questions
function Home({ onOpen, onAuth }: { onOpen: (id: string) => void; onAuth: (e: unknown) => void }) {
  const [links, setLinks] = useState<Link[] | null>(null);
  const [qs, setQs] = useState<QItem[] | null>(null);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    request<{ items: Link[] }>("GET", `${V}/links`).then((r) => setLinks(r.items)).catch((e) => { onAuth(e); setErr((e as Error).message); });
    request<{ items: QItem[] }>("GET", `${V}/questions`).then((r) => setQs(r.items)).catch(onAuth);
  }, [onAuth]);
  useEffect(load, [load]);
  const act = async (path: string) => {
    setErr("");
    try {
      await request("POST", path);
      load();
    } catch (e) {
      onAuth(e);
      setErr((e as Error).message);
    }
  };
  const pending = (links ?? []).filter((l) => l.status === "requested");
  const booking = <BookingCard onAuth={onAuth} />;
  const connected = (links ?? []).filter((l) => l.status === "accepted" || l.status === "expired");
  return (
    <>
      <h1 className="rv-h1">연결 요청</h1>
      {pending.length === 0 ? <p className="help">새 연결 요청이 없습니다.</p> : (
        <ul className="rv-list">
          {pending.map((l) => (
            <li key={l.id} className="card rv-row">
              <span className="rv-badge rv-badge-draft">요청</span>
              <span className="rv-row-q">보호자 {l.guardian_ref} · {at(l.requested_at)} <small className="help">수락 전에는 상담 내용이 공유되지 않습니다.</small></span>
              <span className="row-actions">
                <button className="btn btn-primary" onClick={() => act(`${V}/links/${l.id}/accept`)}>수락</button>
                <button className="btn" onClick={() => act(`${V}/links/${l.id}/decline`)}>거절</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <h2 className="rv-h2">연결된 보호자 <small>{connected.length}명</small></h2>
      {connected.length > 0 && (
        <ul className="rv-list">
          {connected.map((l) => (
            <li key={l.id} className="card rv-row">
              <span className="rv-badge rv-badge-ok">연결됨</span>
              <span className="rv-row-q">보호자 {l.guardian_ref} · 회신 대기 {l.open_questions}건{l.expires_at ? ` · ${new Date(l.expires_at).toLocaleDateString("ko-KR")}까지` : ""}{l.status === "expired" ? " (기간 끝남)" : ""}</span>
              <button className="btn btn-ghost" onClick={() => act(`${V}/links/${l.id}/end`)}>연결 해제</button>
            </li>
          ))}
        </ul>
      )}
      <h1 className="rv-h1" style={{ marginTop: 28 }}>받은 질문</h1>
      {qs && qs.length === 0 && <p className="help">받은 질문이 없습니다.</p>}
      <ul className="rv-list">
        {(qs ?? []).map((q) => (
          <li key={q.id}>
            <button type="button" className="card rv-row" style={{ width: "100%", textAlign: "left", cursor: q.status === "cancelled" ? "default" : "pointer" }}
              onClick={() => q.status !== "cancelled" && onOpen(q.id)} disabled={q.status === "cancelled"}>
              <span className={"rv-badge " + (q.status === "sent" ? "rv-badge-warn" : q.status === "answered" ? "rv-badge-ok" : "rv-badge-muted")}>{STATUS[q.status]}</span>
              <span className="rv-row-q">{q.status === "cancelled" ? (q.cancelled_reason === "link_ended" ? "연결 해제로 취소된 질문" : "보호자가 취소한 질문") : q.question_preview}</span>
              <span className="rv-row-mine">{q.ai_reviewed && <span className="rv-badge rv-badge-ai">AI 검토함</span>} {q.is_test && <span className="rv-badge rv-badge-mock">시험</span>} 보호자 {q.guardian_ref} · {at(q.created_at)}</span>
            </button>
          </li>
        ))}
      </ul>
      {err && <p className="error-text" role="alert">{err}</p>}
      <h2 className="rv-h2" style={{ marginTop: 28 }}>예약 안내 <small>회신에 [예약 안내 넣기]로 넣을 수 있어요</small></h2>
      {booking}
    </>
  );
}

// ---------------------------------------------------------------- 예약 안내: the clinic writes its own phone / hours / note
function BookingCard({ onAuth }: { onAuth: (e: unknown) => void }) {
  const [b, setB] = useState<Booking | null>(null);
  const [f, setF] = useState({ phone: "", hours: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    request<Booking>("GET", `${V}/booking`).then((x) => { setB(x); setF({ phone: x.phone ?? "", hours: x.hours ?? "", note: x.note ?? "" }); }).catch(onAuth);
  }, [onAuth]);
  const dirty = !!b && (f.phone.trim() !== (b.phone ?? "") || f.hours.trim() !== (b.hours ?? "") || f.note.trim() !== (b.note ?? ""));
  const save = async () => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      setB(await request<Booking>("PUT", `${V}/booking`, { phone: f.phone, hours: f.hours, note: f.note }));
      setMsg("저장했습니다.");
    } catch (e) {
      onAuth(e);
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card rv-card">
      <div className="field"><label htmlFor="bk-phone">전화번호</label>
        <input id="bk-phone" className="input" disabled={!b} maxLength={40} value={f.phone} onChange={(e) => { const x = e.target.value; setF((o) => ({ ...o, phone: x })); }} placeholder="예) 02-123-4567" /></div>
      <div className="field"><label htmlFor="bk-hours">진료 시간</label>
        <input id="bk-hours" className="input" disabled={!b} maxLength={300} value={f.hours} onChange={(e) => { const x = e.target.value; setF((o) => ({ ...o, hours: x })); }} placeholder="예) 평일 9-19시, 토 9-13시, 일·공휴일 휴진" /></div>
      <div className="field"><label htmlFor="bk-note">기타 안내 (선택)</label>
        <textarea id="bk-note" className="textarea" disabled={!b} rows={2} maxLength={500} value={f.note} onChange={(e) => { const x = e.target.value; setF((o) => ({ ...o, note: x })); }} placeholder="예) 예약은 전화로 부탁드려요. 응급은 24시 ○○동물병원으로." /></div>
      <div className="row-actions" style={{ alignItems: "center" }}>
        <button className="btn btn-primary" disabled={busy || !dirty} onClick={save}>저장</button>
        {dirty && <span className="help">저장하지 않은 내용이 있어요.</span>}
      </div>
      {msg && !dirty && <p className="ok-text" role="status">{msg}</p>}
      {err && <p className="error-text" role="alert">{err}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- S06 question + reply
function QuestionScreen({ id, onBack, onAuth }: { id: string; onBack: () => void; onAuth: (e: unknown) => void }) {
  const [q, setQ] = useState<QDetail | null>(null);
  const [text, setText] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [booking, setBooking] = useState<Booking | null>(null);
  useEffect(() => {
    request<Booking>("GET", `${V}/booking`).then(setBooking).catch(() => setBooking(null));
  }, []);
  const insertBooking = () => {
    const b = bookingText(booking);
    if (b) setText((t) => (t.trim() ? `${t.trimEnd()}\n\n${b}` : b));
  };
  const bookingBtn = (
    <button type="button" className="btn btn-ghost" onClick={insertBooking} disabled={!bookingText(booking)}
      title={bookingText(booking) ? "" : "목록 화면 아래 '예약 안내'를 먼저 저장하세요"}>예약 안내 넣기</button>
  );
  useEffect(() => {
    request<QDetail>("GET", `${V}/questions/${id}`).then((d) => { setQ(d); setText(d.reply_draft ?? ""); }).catch((e) => { onAuth(e); setErr((e as Error).message); });
  }, [id, onAuth]);
  const call = async (method: string, path: string, body: unknown, ok: string) => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const d = await request<QDetail>(method, path, body);
      setQ(d);
      setMsg(ok);
      if (d.status === "answered") { setText(""); setCorrecting(false); }
    } catch (e) {
      onAuth(e);
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!q) return <>{err && <p className="error-text" role="alert">{err}</p>}</>;
  const s = q.snapshot;
  return (
    <>
      <button type="button" className="link" onClick={onBack}>← 목록</button>
      <h1 className="rv-h1">보호자 {q.guardian_ref}의 질문 <span className={"rv-badge " + (q.status === "sent" ? "rv-badge-warn" : "rv-badge-ok")}>{STATUS[q.status]}</span></h1>
      {s && (
        <div className="rv-split">
          <article className="card rv-pane">
            <h2 className="rv-h2">질문 <small>{at(s.shared_at)} 공유</small></h2>
            <p className="rv-quote">{s.question}</p>
            {s.note_to_vet && (<><h2 className="rv-h2">보호자가 덧붙인 설명</h2><p className="rv-quote">{s.note_to_vet}</p></>)}
            <h2 className="rv-h2">공유된 반려견 정보</h2>
            {Object.keys(s.dog).length === 0 ? <p className="help">공유된 정보가 없습니다.</p> : (
              <p className="rv-dog">{Object.entries(s.dog).map(([k, v]) => `${DOG_LABEL[k] ?? k} ${dogValue(k, v)}`).join(" · ")}</p>
            )}
            <h2 className="rv-h2">보호자가 받은 AI 답변 <small>참고용</small></h2>
            <p className="rv-quote rv-answer">{s.ai_answer}</p>
            {s.sources.length > 0 && <ul className="rv-cites">{s.sources.map((t, i) => <li key={i}>{t}</li>)}</ul>}
          </article>
          <div className="rv-pane">
            <section className="card rv-card">
              {q.status === "sent" && (
                <>
                  <h2 className="rv-h2">회신 작성 <small>보호자에게 전달</small></h2>
                  <p className="rv-caution" role="note">
                    <strong>비대면 진료가 되지 않도록 주의해 주세요.</strong> 회신은 진찰을 대신하지 않는 일반적인 안내입니다.
                    질병을 단정하거나 약 이름·용량을 적지 말고, 상태 확인이 필요하면 내원을 권해 주세요.
                  </p>
                  <textarea className="textarea rv-textarea" rows={8} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
                    aria-label="회신" placeholder="보호자에게 보낼 답변을 작성하세요. 진료·처방이 아닌 안내임을 고려해 주세요." />
                  <div className="row-actions" style={{ marginTop: 8 }}>
                    <button className="btn" disabled={busy} onClick={() => call("PUT", `${V}/questions/${id}/draft`, { text }, "초안을 저장했습니다.")}>초안 저장</button>
                    <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => call("POST", `${V}/questions/${id}/reply`, { text }, "회신을 보냈습니다.")}>회신 보내기</button>
                    {bookingBtn}
                  </div>
                  {!bookingText(booking) && <p className="help">예약 안내(전화번호·진료 시간)를 목록 화면 아래에 저장해 두면 [예약 안내 넣기]로 회신에 넣을 수 있어요.</p>}
                  <p className="help">보낸 뒤에는 정정만 할 수 있고, 처음 답변과 정정 이력이 보호자에게 함께 남습니다.</p>
                </>
              )}
              {q.status === "answered" && q.reply && (
                <>
                  <h2 className="rv-h2">보낸 회신 {q.reply.corrected && <span className="rv-badge rv-badge-ai">정정됨</span>}</h2>
                  <ol className="rv-history">
                    {q.reply.history.map((h, i) => <li key={i}><small>{h.kind === "original" ? "처음 답변" : "정정"} · {at(h.created_at)}</small><p className="rv-quote">{h.body}</p></li>)}
                  </ol>
                  {!correcting ? <button className="btn" onClick={() => setCorrecting(true)}>정정하기</button> : (
                    <>
                      <textarea className="textarea rv-textarea" rows={6} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} aria-label="정정 내용" />
                      <div className="row-actions" style={{ marginTop: 8 }}>
                        <button className="btn" onClick={() => setCorrecting(false)}>취소</button>
                        <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => call("POST", `${V}/questions/${id}/correction`, { text }, "정정을 보냈습니다.")}>정정 보내기</button>
                        {bookingBtn}
                      </div>
                    </>
                  )}
                </>
              )}
              {msg && <p className="ok-text" role="status">{msg}</p>}
              {err && <p className="error-text" role="alert">{err}</p>}
            </section>
            {q.status !== "cancelled" && <AiReviewCard q={q} onSaved={setQ} onAuth={onAuth} />}
            {q.status !== "cancelled" && <MemoCard q={q} onSaved={setQ} onAuth={onAuth} />}
          </div>
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------- AI 답변 검토 ('HITL 우리'): TwinVet only, never the guardian
// One continuous form: checking, fixing and sending are one action. After sending, the form stays open with what the
// vet wrote (never collapsed into a summary), so they can keep reading their own words and send again if they change it.
function AiReviewCard({ q, onSaved, onAuth }: { q: QDetail; onSaved: (d: QDetail) => void; onAuth: (e: unknown) => void }) {
  const ai = q.snapshot?.ai_answer ?? "";
  const consent = !!q.quality_use_consent;
  const last = q.ai_review ?? null;
  const [verdict, setVerdict] = useState<Verdict | null>(last?.verdict ?? null);
  const [safety, setSafety] = useState(last?.safety_issue ?? false);
  const [fixed, setFixed] = useState(last?.corrected_answer ?? ai);
  const [comment, setComment] = useState(last?.comment ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const changed = fixed.trim() !== "" && fixed.trim() !== ai.trim();
  const needsText = consent && verdict !== null && verdict !== "correct" && !changed && !comment.trim();
  const dirty = !last || verdict !== last.verdict || safety !== last.safety_issue
    || (changed ? fixed.trim() : null) !== (last.corrected_answer ?? null) || (comment.trim() || null) !== (last.comment ?? null);
  const save = async () => {
    if (!verdict) return;
    setBusy(true);
    setErr("");
    try {
      onSaved(await request<QDetail>("PUT", `${V}/questions/${q.id}/ai-review`, {
        verdict, safety_issue: safety,
        corrected_answer: consent && changed ? fixed.trim() : null, comment: consent ? comment.trim() || null : null,
      }));
    } catch (e) {
      onAuth(e);
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card rv-card" aria-labelledby="ai-review-title">
      <h2 className="rv-h2" id="ai-review-title">AI 답변 검토 및 수정 <small>TwinVet에만 전달 · 보호자에게 보내지 않음</small></h2>
      <p className="help">보호자가 받은 AI 답변을 검토·수정해 TwinVet에 보내 주시면 AI 답변 품질을 개선하는 데 씁니다. 보호자에게는 보내지 않으며, 회신과는 별개입니다.</p>
      <p className="rv-caution" role="note"><strong>검토에는 이 아이의 개인 정보를 넣지 마세요.</strong> 평소 상태·병력·이름처럼 이 아이에게만 해당하는 내용은 아래 <strong>메모</strong>에 남겨 주세요. 검토는 다른 보호자의 AI 답변에도 쓰일 수 있습니다.</p>
      <div className="rv-filters" role="group" aria-label="결론">
        {VERDICT.map(([k, label]) => (
          <button key={k} type="button" className="chip" aria-pressed={verdict === k} onClick={() => setVerdict(k)}>{label}</button>
        ))}
      </div>
      <label className="rv-check"><input type="checkbox" checked={safety} onChange={(e) => setSafety(e.target.checked)} /> 안전 문제가 있어요 (따르면 위험한 내용, 빠진 응급 경고 등)</label>
      {consent ? (
        <>
          <div className="field" style={{ marginTop: 8 }}>
            <label htmlFor="ai-fixed">AI 답변 고치기 <small className="help">AI 답변이 채워져 있어요. 고칠 부분만 수정하세요.</small></label>
            <textarea id="ai-fixed" className="textarea rv-textarea" rows={8} maxLength={4000} value={fixed} onChange={(e) => setFixed(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="ai-comment">검토 의견 (선택)</label>
            <textarea id="ai-comment" className="textarea rv-textarea" rows={3} maxLength={4000} value={comment} onChange={(e) => setComment(e.target.value)}
              placeholder="예) 응급 경고가 빠졌어요. 노령견은 각막 질환을 먼저 확인해야 해요. (일반적인 내용만)" />
          </div>
          {needsText && <p className="help">보완 필요·다르게 봐야 해요는 AI 답변을 고치거나 의견을 적어 주세요.</p>}
        </>
      ) : (
        <p className="help">보호자가 품질 개선 활용에 동의하지 않은 질문이에요. 결론과 안전 체크만 보낼 수 있고, 내용은 저장되지 않습니다.</p>
      )}
      <div className="row-actions" style={{ marginTop: 8, alignItems: "center" }}>
        <button className="btn btn-primary" disabled={busy || !verdict || needsText || !dirty} onClick={save}>검토 보내기</button>
        {!verdict && <span className="help">결론(맞아요 / 보완 필요 / 다르게 봐야 해요)을 먼저 골라 주세요.</span>}
        {last && <span className="help" role="status">{dirty ? `${at(last.created_at)}에 보낸 뒤 고친 내용이 있어요. 아직 보내지 않았어요.` : `${at(last.created_at)} 검토 보냄`}</span>}
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- 메모: the vet's own notes about this dog (personal info)
function MemoCard({ q, onSaved, onAuth }: { q: QDetail; onSaved: (d: QDetail) => void; onAuth: (e: unknown) => void }) {
  const last = q.memo?.body ?? "";
  const [text, setText] = useState(last);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const dirty = text.trim() !== last;
  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      onSaved(await request<QDetail>("PUT", `${V}/questions/${q.id}/memo`, { text }));
    } catch (e) {
      onAuth(e);
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card rv-card" aria-labelledby="memo-title">
      <h2 className="rv-h2" id="memo-title">메모 <small>이 아이에 대한 수의사님의 메모 · TwinVet·보호자에게 보내지 않음</small></h2>
      <p className="help">평소 상태, 병력, 성격처럼 이 아이에게만 해당하는 내용을 적어 두세요. 이 보호자의 다음 질문에서도 보입니다.</p>
      <textarea className="textarea rv-textarea" rows={4} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)}
        aria-label="메모" placeholder="예) 평소 건강하고 활동량이 많음. 작년 슬개골 수술." />
      <div className="row-actions" style={{ marginTop: 8, alignItems: "center" }}>
        <button className="btn" disabled={busy || !dirty} onClick={save}>메모 저장</button>
        {q.memo && <span className="help" role="status">{dirty ? "저장하지 않은 내용이 있어요." : `${at(q.memo.updated_at)} 저장됨`}</span>}
      </div>
      {err && <p className="error-text" role="alert">{err}</p>}
    </section>
  );
}

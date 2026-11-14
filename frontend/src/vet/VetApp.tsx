// M03 vet screens (docs/05 §6 M03: S05 수의사 홈, S06 질문 상세·회신). Reached on vet.pawcheck.ai or /vet.
// While M03 is off (server switch) only a "준비 중" notice is shown. Separate from HITL review accounts.
import { useCallback, useEffect, useState } from "react";
import { ApiError, request } from "../api";
import { formatAge } from "../lib/age";
import "../review/review.css";

type Vet = { id: string; display_name: string; clinic_name: string | null; intro: string | null; is_test: boolean };
type Link = { id: string; status: string; guardian_ref: string; requested_at: string; open_questions: number };
type Reply = { body: string; sent_at: string; corrected: boolean; history: { kind: string; body: string; created_at: string }[] };
type QItem = { id: string; status: "sent" | "answered" | "cancelled"; cancelled_reason: string | null; created_at: string; replied_at: string | null; guardian_ref: string; question_preview?: string };
type QDetail = QItem & {
  snapshot?: { question: string; ai_answer: string; note_to_vet: string | null; sources: string[]; shared_at: string; dog: Record<string, string | number> };
  reply_draft?: string | null;
  reply?: Reply | null;
};

const V = "/vet";
const at = (iso: string) => new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });
const STATUS: Record<string, string> = { sent: "회신 대기", answered: "회신 완료", cancelled: "취소됨" };
const DOG_LABEL: Record<string, string> = { name: "이름", breed: "품종", age: "나이", weight: "체중", neutered: "중성화", health_notes: "건강 메모" };
const NEUTERED: Record<string, string> = { yes: "했음", no: "안 했음", unknown: "모름" };
const dogValue = (k: string, v: string | number) =>
  k === "age" && typeof v === "number" ? formatAge(v) : k === "weight" ? `${v}kg` : k === "neutered" ? NEUTERED[String(v)] ?? String(v) : String(v);

export function VetApp() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [me, setMe] = useState<Vet | null | undefined>(undefined);
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => {
    request<{ enabled: boolean }>("GET", "/m03/availability")
      .then((r) => {
        setEnabled(r.enabled);
        if (r.enabled) request<Vet>("GET", `${V}/me`).then(setMe).catch(() => setMe(null));
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
  const connected = (links ?? []).filter((l) => l.status === "accepted");
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
              <span className="rv-row-q">보호자 {l.guardian_ref} · 회신 대기 {l.open_questions}건</span>
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
              <span className="rv-row-mine">보호자 {q.guardian_ref} · {at(q.created_at)}</span>
            </button>
          </li>
        ))}
      </ul>
      {err && <p className="error-text" role="alert">{err}</p>}
    </>
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
                  <h2 className="rv-h2">회신 작성</h2>
                  <textarea className="textarea rv-textarea" rows={8} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)}
                    aria-label="회신" placeholder="보호자에게 보낼 답변을 작성하세요. 진료·처방이 아닌 안내임을 고려해 주세요." />
                  <div className="row-actions" style={{ marginTop: 8 }}>
                    <button className="btn" disabled={busy} onClick={() => call("PUT", `${V}/questions/${id}/draft`, { text }, "초안을 저장했습니다.")}>초안 저장</button>
                    <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={() => call("POST", `${V}/questions/${id}/reply`, { text }, "회신을 보냈습니다.")}>회신 보내기</button>
                  </div>
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
                      </div>
                    </>
                  )}
                </>
              )}
              {msg && <p className="ok-text" role="status">{msg}</p>}
              {err && <p className="error-text" role="alert">{err}</p>}
            </section>
          </div>
        </div>
      )}
    </>
  );
}

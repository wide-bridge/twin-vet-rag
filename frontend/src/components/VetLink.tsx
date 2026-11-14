// M03 guardian side (docs/05 §6 M03: S04 수의사 연결, S04-1 질문 보내기, S07 회신 확인).
// Shown only when the server says M03 is enabled. The server enforces every access rule; these screens only
// reflect what it returns.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  api, type ApiError, type Message, type ShareField, type VetInfo, type VetLink, type VetQuestion,
} from "../api";
import { formatAge } from "../lib/age";
import { Dialog } from "./Common";
import { IconCheck, IconError, IconInfo } from "./Icons";

type M03 = {
  enabled: boolean;
  links: VetLink[];
  questions: VetQuestion[];
  refresh: () => Promise<void>;
};
const M03Context = createContext<M03>({ enabled: false, links: [], questions: [], refresh: async () => undefined });
export const useM03 = () => useContext(M03Context);

export function M03Provider({ hasProfile, children }: { hasProfile: boolean; children: ReactNode }) {
  const [enabled, setEnabled] = useState(false);
  const [links, setLinks] = useState<VetLink[]>([]);
  const [questions, setQuestions] = useState<VetQuestion[]>([]);
  useEffect(() => {
    api.m03Availability().then((r) => setEnabled(r.enabled)).catch(() => setEnabled(false));
  }, []);
  const refresh = useCallback(async () => {
    if (!enabled || !hasProfile) {
      setLinks([]);
      setQuestions([]);
      return;
    }
    try {
      const [l, q] = await Promise.all([api.vetLinks(), api.vetQuestions()]);
      setLinks(l.items);
      setQuestions(q.items);
    } catch {
      /* keep what we have; the next action shows its own error */
    }
  }, [enabled, hasProfile]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  const value = useMemo(() => ({ enabled, links, questions, refresh }), [enabled, links, questions, refresh]);
  return <M03Context.Provider value={value}>{children}</M03Context.Provider>;
}

const LINK_LABEL: Record<string, string> = { requested: "요청 중", accepted: "연결됨", declined: "거절됨", cancelled: "취소함", ended: "해제됨" };
const vetName = (v: VetInfo | null) => (v ? `${v.display_name}${v.clinic_name ? ` · ${v.clinic_name}` : ""}` : "수의사");
const at = (iso: string) => new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "numeric", minute: "2-digit" });

// ---------------------------------------------------------------- S04 수의사 연결 (inside 설정)
export function VetConnectSection({ hasProfile }: { hasProfile: boolean }) {
  const { enabled, links, refresh } = useM03();
  const [vets, setVets] = useState<VetInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (enabled && hasProfile) api.vets().then((r) => setVets(r.items)).catch((e: ApiError) => setError(e.message));
  }, [enabled, hasProfile]);
  if (!enabled) return null;
  const open = links.filter((l) => l.status === "requested" || l.status === "accepted");
  const openIds = new Set(open.map((l) => l.vet?.id));
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card settings-card" aria-labelledby="vet-h">
      <h2 id="vet-h">수의사 연결 <small>(선택)</small></h2>
      <p className="help">연결된 수의사에게 상담 중 고른 질문만 보낼 수 있어요. 연결만으로는 상담 내용이 공유되지 않아요. 주치동물병원 등록과는 별개예요.</p>
      {!hasProfile && <p className="help"><IconInfo small /> 반려견 정보를 먼저 저장하면 연결할 수 있어요.</p>}
      {open.length > 0 && (
        <ul className="vet-list">
          {open.map((l) => (
            <li key={l.id} className="vet-row">
              <div><strong>{vetName(l.vet)}</strong><span className={"vet-badge is-" + l.status}>{LINK_LABEL[l.status]}</span></div>
              <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => act(() => api.closeVetLink(l.id))}>
                {l.status === "requested" ? "요청 취소" : "연결 해제"}
              </button>
            </li>
          ))}
        </ul>
      )}
      {hasProfile && vets && vets.filter((v) => !openIds.has(v.id)).length > 0 && (
        <ul className="vet-list" aria-label="연결할 수 있는 수의사">
          {vets.filter((v) => !openIds.has(v.id)).map((v) => (
            <li key={v.id} className="vet-row">
              <div>
                <strong>{vetName(v)}</strong>{v.is_test && <span className="vet-badge">시험 계정</span>}
                {v.intro && <span className="help">{v.intro}</span>}
              </div>
              <button type="button" className="btn" disabled={busy} onClick={() => act(() => api.requestVetLink(v.id))}>연결 요청</button>
            </li>
          ))}
        </ul>
      )}
      {hasProfile && vets && vets.length === 0 && <p className="help">지금은 연결할 수 있는 수의사가 없어요.</p>}
      {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- S04-1 질문 보내기 + S07 회신 (under an AI answer)
const FIELD_LABEL: Record<ShareField, string> = { name: "이름", breed: "품종", age: "나이", weight: "체중", neutered: "중성화", health_notes: "건강 메모" };
const DEFAULT_FIELDS: ShareField[] = ["breed", "age", "weight", "neutered"];

export function VetAsk({ answer, question }: { answer: Message; question: string }) {
  const { enabled, links, questions } = useM03();
  const [open, setOpen] = useState(false);
  if (!enabled) return null;
  const sent = questions.filter((q) => q.answer_message_id === answer.id);
  const accepted = links.filter((l) => l.status === "accepted");
  const sentTo = new Set(sent.filter((q) => q.status !== "cancelled").map((q) => q.link_id));
  const canSend = accepted.some((l) => !sentTo.has(l.id));
  if (!sent.length && !canSend) return null;
  return (
    <div className="vet-ask">
      {sent.map((q) => <VetQuestionCard key={q.id} q={q} />)}
      {canSend && <button type="button" className="btn" onClick={() => setOpen(true)}>수의사에게 질문 보내기</button>}
      {open && <ShareSheet answer={answer} question={question} links={accepted.filter((l) => !sentTo.has(l.id))} onClose={() => setOpen(false)} />}
    </div>
  );
}

function ShareSheet({ answer, question, links, onClose }: { answer: Message; question: string; links: VetLink[]; onClose: () => void }) {
  const { refresh } = useM03();
  const [linkId, setLinkId] = useState(links[0]?.id ?? "");
  const [note, setNote] = useState("");
  const [fields, setFields] = useState<ShareField[]>(DEFAULT_FIELDS);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const toggle = (f: ShareField) => setFields((cur) => (cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]));
  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.sendVetQuestion({ link_id: linkId, answer_message_id: answer.id, note_to_vet: note.trim() || null, shared_fields: fields, quality_use_consent: consent });
      setToken(r.access_token);
      await refresh();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  if (token) {
    const url = `${window.location.origin}/q/${token}`;
    return (
      <Dialog title="질문을 보냈어요" onClose={onClose} sheet actions={<button type="button" className="btn btn-primary" onClick={onClose}>확인</button>}>
        <p>수의사가 확인하면 이 답변 아래에 회신이 표시돼요. 알림은 보내지 않으니 나중에 다시 들어와 확인해 주세요.</p>
        <div className="field">
          <label htmlFor="vet-link-url">다른 기기에서 확인할 링크 (지금만 보여요)</label>
          <input id="vet-link-url" className="input" readOnly value={url} onFocus={(e) => e.currentTarget.select()} />
          <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(url)}>링크 복사</button>
          <p className="help">링크를 가진 사람은 이 질문과 회신을 볼 수 있어요. 다른 사람에게 보내지 마세요.</p>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog title="수의사에게 질문 보내기" onClose={onClose} sheet
      actions={<>
        <button type="button" className="btn" onClick={onClose}>취소</button>
        <button type="button" className="btn btn-primary" onClick={send} disabled={busy || !linkId}>{busy ? "보내는 중…" : "보내기"}</button>
      </>}>
      {links.length > 1 ? (
        <div className="field">
          <label htmlFor="vet-to">받는 수의사</label>
          <select id="vet-to" className="input" value={linkId} onChange={(e) => setLinkId(e.target.value)}>
            {links.map((l) => <option key={l.id} value={l.id}>{vetName(l.vet)}</option>)}
          </select>
        </div>
      ) : <p className="help">받는 수의사: <strong>{vetName(links[0]?.vet ?? null)}</strong></p>}
      <div className="share-preview">
        <p className="label">보낼 질문</p>
        <p className="quote">{question}</p>
        <p className="label">함께 보내는 AI 답변</p>
        <p className="quote clamp">{answer.content}</p>
      </div>
      <div className="field">
        <label htmlFor="vet-note">수의사에게 덧붙일 설명 <small>(선택)</small></label>
        <textarea id="vet-note" className="textarea" rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)}
          placeholder="예: 어제 물어본 사료 이야기예요. 지금은 구토가 멈췄어요" />
        <span className="counter">{note.length}/500</span>
      </div>
      <fieldset className="field share-fields">
        <legend className="label">함께 보낼 반려견 정보</legend>
        {(Object.keys(FIELD_LABEL) as ShareField[]).map((f) => (
          <label key={f} className="check"><input type="checkbox" checked={fields.includes(f)} onChange={() => toggle(f)} /> {FIELD_LABEL[f]}</label>
        ))}
      </fieldset>
      <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> 서비스 품질 개선에 활용하는 데 동의해요 (선택, 언제든 철회)</label>
      <p className="help"><IconInfo small /> 이전 대화와 다른 상담은 보내지 않아요. 응급이면 회신을 기다리지 말고 동물병원에 연락하세요.</p>
      {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
    </Dialog>
  );
}

function VetQuestionCard({ q, standalone }: { q: VetQuestion; standalone?: boolean }) {
  const { refresh } = useM03();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  };
  const status = q.status === "answered" ? "답변 완료" : q.status === "sent" ? "수의사 확인 대기" : q.cancelled_reason === "link_ended" ? "연결 해제로 취소됨" : "취소됨";
  return (
    <section className={"vet-card is-" + q.status} aria-label="수의사 질문">
      <div className="answer-head">
        <span className="vet-tag">수의사 질문 · {vetName(q.vet)}</span>
        <span className={"vet-badge is-" + q.status}>{status}</span>
      </div>
      {standalone && <p className="quote">{q.snapshot.question}</p>}
      {q.reply && (
        <div className="vet-reply">
          <p className="vet-reply-head"><IconCheck small /> 수의사 답변 · {at(q.reply.corrected_at ?? q.reply.sent_at)}{q.reply.corrected && " · 정정됨"}</p>
          <p className="vet-reply-body">{q.reply.body}</p>
          {q.reply.corrected && (
            <button type="button" className="link" onClick={() => setShowHistory(!showHistory)}>{showHistory ? "이력 닫기" : "정정 이력 보기"}</button>
          )}
          {showHistory && (
            <ol className="vet-history">
              {q.reply.history.map((h, i) => <li key={i}><small>{h.kind === "original" ? "처음 답변" : "정정"} · {at(h.created_at)}</small><p>{h.body}</p></li>)}
            </ol>
          )}
          <p className="fine">수의사 답변도 진료나 처방이 아니에요. 응급이면 바로 동물병원에 연락하세요.</p>
        </div>
      )}
      {!standalone && (
        <div className="row-actions">
          {q.status === "sent" && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => act(() => api.cancelVetQuestion(q.id))}>보내기 취소</button>}
          {q.quality_use_consent && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => act(() => api.setVetConsent(q.id, false))}>품질 개선 동의 철회</button>}
          {q.status !== "sent" && <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => act(() => api.deleteVetQuestion(q.id))}>삭제</button>}
        </div>
      )}
      {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
    </section>
  );
}

// ---------------------------------------------------------------- confirmation link page (/q/<token>)
export function VetQuestionPage({ token }: { token: string }) {
  const [q, setQ] = useState<VetQuestion | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.vetQuestionByToken(token).then(setQ).catch((e: ApiError) => setError(e.status === 404 ? "링크가 만료되었거나 삭제된 질문이에요." : e.message));
  }, [token]);
  const dog = q?.snapshot.dog;
  return (
    <div className="page-shell">
      <main className="page">
        <h1 className="page-title">수의사 질문 확인</h1>
        {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
        {q && (
          <>
            <p className="help">{at(q.created_at)}에 보낸 질문{dog?.breed ? ` · ${dog.breed}` : ""}{typeof dog?.age === "number" ? ` · ${formatAge(dog.age)}` : ""}</p>
            <VetQuestionCard q={q} standalone />
          </>
        )}
        <a className="link" href="/">TwinVet Care로 가기</a>
      </main>
    </div>
  );
}

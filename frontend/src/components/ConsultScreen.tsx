// S02 상담 (docs/05 §3, §5, §9). Server state (thread, runs) is the source of truth; a refresh restores
// from GET /consultations/{id}/messages and keeps polling any queued/running run.
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, newRequestId, type ApiError, type CitationRef, type Run, type Thread } from "../api";
import { describeAge } from "../lib/age";
import { emptyDogForm, formFromDog, validateDog, type DogForm } from "../lib/dogForm";
import { store } from "../lib/store";
import { AnswerCard, FailedCard, ProcessingCard, time } from "./AnswerCard";
import { Dialog, Header } from "./Common";
import { Composer } from "./Composer";
import { DogCard } from "./DogCard";
import { IconBulb, IconError, IconInfo, IconQuestion } from "./Icons";
import { SourceDetail } from "./SourceDetail";

const EXAMPLES = [
  "하루에 두 번 토했는데 병원에 가야 할까요?",
  "귀를 자주 긁고 냄새가 나요",
  "슬개골 탈구는 집에서 어떻게 관리하나요?",
  "눈곱이 갑자기 많아졌어요",
];
const POLL_MS = 1200;

export function ConsultScreen({ maxChars, onExpired }: { maxChars: number; onExpired: () => void }) {
  const [cid, setCid] = useState<string | null>(() => store.consultationId());
  const [thread, setThread] = useState<Thread | null>(null);
  const [loading, setLoading] = useState(Boolean(cid));
  const [form, setForm] = useState<DogForm>(() => {
    const saved = store.lastDog();
    return saved ? formFromDog(saved.dog, saved.savedAt) : emptyDogForm();
  });
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [source, setSource] = useState<{ c: CitationRef; label: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const handleError = useCallback((e: unknown) => {
    const err = e as ApiError;
    if (err.status === 401) onExpired();
    else setNotice(err.message);
  }, [onExpired]);

  const reload = useCallback(async (id: string) => {
    try {
      setThread(await api.thread(id));
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 404) {
        store.setConsultationId(null);
        setCid(null);
        setThread(null);
      } else handleError(e);
    } finally {
      setLoading(false);
    }
  }, [handleError]);

  useEffect(() => {
    if (cid) reload(cid);
  }, [cid, reload]);

  // poll the active run until it finishes, then refresh the thread
  const activeRun = useMemo(() => thread?.runs.find((r) => r.status === "queued" || r.status === "running") ?? null, [thread]);
  useEffect(() => {
    if (!activeRun || !cid) return;
    let alive = true;
    const t = window.setInterval(async () => {
      try {
        const r = await api.run(activeRun.id);
        if (alive && (r.status === "completed" || r.status === "failed")) await reload(cid);
      } catch (e) {
        if ((e as ApiError).status === 401) onExpired();
      }
    }, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [activeRun, cid, reload, onExpired]);

  // Serverless hosting has no background worker: ask the server to run each queued answer. Called right after
  // sending/retrying, and once for a queued run found on load (e.g. the tab was closed before it started).
  const kicked = useRef(new Set<string>());
  const kick = useCallback((runId: string, consultationId: string) => {
    kicked.current.add(runId);
    api.execute(runId)
      .then(() => reload(consultationId))
      .catch((e) => {
        if ((e as ApiError).status === 401) onExpired();
        // otherwise the poller keeps checking; a stuck run times out and can be retried
      });
  }, [reload, onExpired]);
  useEffect(() => {
    if (cid && activeRun && activeRun.status === "queued" && !kicked.current.has(activeRun.id)) kick(activeRun.id, cid);
  }, [activeRun, cid, kick]);

  // keep the newest message in view
  const count = (thread?.messages.length ?? 0) + (activeRun ? 1 : 0);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && count > 0) el.scrollTop = el.scrollHeight; // the empty intake form stays at the top
  }, [count]);

  // S03 on mobile: browser back closes the modal
  const openSource = (c: CitationRef, label: string) => {
    setSource({ c, label });
    if (window.matchMedia("(max-width: 1023px)").matches) window.history.pushState({ tvcSource: true }, "");
  };
  const closeSource = useCallback(() => {
    if (window.history.state?.tvcSource) window.history.back();
    else setSource(null);
  }, []);
  useEffect(() => {
    const onPop = () => setSource(null);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const validation = validateDog(form);
  const needDog = !cid;
  const send = async (textArg?: string) => {
    const text = (textArg ?? draft).trim();
    if (!text || sending) return;
    setSending(true);
    setNotice(null);
    try {
      let id = cid;
      if (!id) {
        if (!validation.dog) return;
        const created = await api.createConsultation(validation.dog);
        id = created.consultation_id;
        store.setConsultationId(id);
        store.setLastDog(created.dog);
        setCid(id);
      }
      const { run_id } = await api.ask(id, text, newRequestId());
      kicked.current.add(run_id); // the effect below must not start it a second time
      setDraft("");
      await reload(id);
      kick(run_id, id);
    } catch (e) {
      handleError(e);
    } finally {
      setSending(false);
    }
  };

  const retry = async (run: Run, question: string) => {
    if (!cid) return;
    setRetrying(run.id);
    setNotice(null);
    try {
      await api.ask(cid, question, run.client_request_id);
      await reload(cid);
      kick(run.id, cid);
    } catch (e) {
      handleError(e);
    } finally {
      setRetrying(null);
    }
  };

  const newConsultation = () => {
    store.setConsultationId(null);
    const saved = thread ? { dog: thread.dog, savedAt: thread.created_at } : store.lastDog();
    setForm(saved ? formFromDog(saved.dog, saved.savedAt) : emptyDogForm());
    setCid(null);
    setThread(null);
    setDraft("");
    setNotice(null);
    setSource(null);
  };

  const doDelete = async () => {
    if (!cid) return;
    setDeleting(true);
    try {
      await api.deleteConsultation(cid);
      setConfirmDelete(false);
      setDeleteError(false);
      newConsultation();
    } catch (e) {
      setConfirmDelete(false);
      if ((e as ApiError).status === 401) onExpired();
      else if ((e as ApiError).status === 404) newConsultation();
      else setDeleteError(true);
    } finally {
      setDeleting(false);
    }
  };

  const dog = thread?.dog;
  const summary = dog ? [dog.breed, describeAge(dog), dog.weight_kg != null ? `${dog.weight_kg}kg` : null].filter(Boolean).join(" · ") : null;
  const runsByQuestion = new Map((thread?.runs ?? []).map((r) => [r.question_message_id, r]));
  const showExamples = !thread || thread.messages.length === 0;
  const dogErrors = needDog && draft.trim() ? validation.errors : {};

  return (
    <div className="consult">
      <Header actions={[{ label: "새 상담", onClick: newConsultation }, ...(cid ? [{ label: "대화 삭제", onClick: () => setConfirmDelete(true) }] : [])]}>
        {summary && (
          <div className="summary-bar">
            <span className="summary-chip"><IconInfo small /> <span>{summary}</span></span>
          </div>
        )}
      </Header>
      <div className="consult-body">
        <div className="thread-scroll" ref={scrollRef}>
          <main className="thread" aria-live="polite">
            {deleteError && (
              <div className="banner" role="alert">
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}><IconError small /> 대화를 삭제하지 못했어요. 다시 시도해 주세요.</span>
                <button type="button" className="btn" onClick={doDelete} disabled={deleting}>다시 시도</button>
              </div>
            )}
            {loading && <p className="help">불러오는 중…</p>}
            {needDog && !loading && <DogCard form={form} errors={dogErrors} onChange={setForm} />}
            {showExamples && !loading && (
              <section className="examples">
                <h3><IconBulb small /> 이런 질문을 할 수 있어요</h3>
                <ul>
                  {EXAMPLES.map((q) => (
                    <li key={q}><button type="button" onClick={() => setDraft(q)}><span style={{ color: "var(--color-primary)" }}><IconQuestion small /></span>{q}</button></li>
                  ))}
                </ul>
              </section>
            )}
            {thread?.messages.map((m) => {
              if (m.role === "assistant") return <AnswerCard key={m.id} msg={m} onOpenSource={openSource} />;
              const run = runsByQuestion.get(m.id);
              return (
                <Fragment key={m.id}>
                  <div className="bubble-user">{m.content}</div>
                  <span className="time">{time(m.created_at)}</span>
                  {run && (run.status === "queued" || run.status === "running") && <ProcessingCard />}
                  {run && run.status === "failed" && (
                    <FailedCard message={run.error?.message ?? "잠시 후 다시 시도해 주세요."} retrying={retrying === run.id}
                      onRetry={() => retry(run, m.content)} />
                  )}
                </Fragment>
              );
            })}
            {notice && <p className="error-text" role="alert"><IconError small /> {notice}</p>}
          </main>
        </div>
        {source && <SourceDetail citation={source.c} label={source.label} onClose={closeSource} />}
      </div>
      <Composer value={draft} onChange={setDraft} onSend={() => send()} maxChars={maxChars}
        disabled={sending || Boolean(activeRun)} sendDisabled={needDog && !validation.dog}
        hint={needDog && !validation.dog ? "품종과 나이를 입력하면 보낼 수 있어요" : activeRun ? "답변을 준비하는 동안에는 보낼 수 없어요" : undefined} />
      {confirmDelete && (
        <Dialog title="이 대화를 삭제할까요?" onClose={() => setConfirmDelete(false)} sheet
          actions={<>
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>취소</button>
            <button type="button" className="btn btn-danger" onClick={doDelete} disabled={deleting}>삭제</button>
          </>}>
          <p>질문·답변·입력한 반려견 정보가 모두 삭제되며 되돌릴 수 없어요.</p>
        </Dialog>
      )}
    </div>
  );
}

// S08 케어 (docs/05 §3-1): one card per consultation, same day + same topic grouped, [병원에 보여주기].
// The criterion is "can this be shown to the hospital as is": facts and the AI's gist, no scores or grades.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ApiError, type CareCard, type Profile } from "../api";
import { dogSummary } from "../lib/age";
import { TAGS, TOPICS, clinicPhone, dateTime, groupCards, kakaoMapHref, shortDate, telHref, type CareGroup } from "../lib/care";
import { store } from "../lib/store";
import { Dialog, Header } from "./Common";
import { IconError, IconInfo } from "./Icons";
import { useTabs } from "./Tabs";

type SummaryState = Record<string, "loading" | "error">;

function TopicChip({ card }: { card: { topic: CareCard["topic"] } }) {
  if (!card.topic) return <span className="topic-chip muted">주제 정리 중</span>;
  const t = TOPICS[card.topic];
  return <span className="topic-chip"><span aria-hidden="true">{t.icon}</span> {t.label}</span>;
}

function Tags({ tags }: { tags: CareCard["tags"] }) {
  if (!tags.length) return null;
  return (
    <div className="tag-row">
      {tags.map((t) => <span key={t} className={"tag" + (t === "vet_advised" ? " tag-vet" : "")}>{t === "vet_advised" ? "🩺 " : ""}{TAGS[t]}</span>)}
    </div>
  );
}

function Summary({ card, state, onRetry }: { card: CareCard; state?: "loading" | "error"; onRetry: () => void }) {
  if (state === "loading" || (!card.summary && !state && !card.summary_fresh && !card.tags.includes("in_progress"))) return <p className="help">AI 요약을 만드는 중…</p>;
  if (state === "error" && !card.summary) {
    return <p className="help">요약을 만들지 못했어요. <button type="button" className="link" onClick={onRetry}>다시 시도</button></p>;
  }
  if (!card.summary) return null;
  return <p className="care-summary"><span className="ai-mini">AI 요약</span> {card.summary}</p>;
}

function Handoff({ cards, profile, onClose }: { cards: CareCard[]; profile: Profile | null; onClose: () => void }) {
  const ordered = [...cards].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const dog = ordered[ordered.length - 1].dog;
  const clinic = profile?.clinic ?? null;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const sameTopic = ordered.length > 1 && ordered.every((c) => c.topic && c.topic === ordered[0].topic);
  return (
    <div className="handoff" role="dialog" aria-modal="true" aria-labelledby="handoff-h" tabIndex={-1} ref={ref}>
      <header>
        <h2 id="handoff-h">병원에 보여줄 요약</h2>
        <button type="button" className="btn" onClick={onClose}>닫기</button>
      </header>
      <div className="handoff-body">
        <dl className="kv">
          <dt>반려견</dt><dd>{dogSummary(dog)}</dd>
          {dog.neutered && <><dt>중성화</dt><dd>{{ yes: "예", no: "아니오", unknown: "모름" }[dog.neutered]}</dd></>}
          {dog.health_notes && <><dt>기존 질환·약</dt><dd>{dog.health_notes}</dd></>}
          {clinic && <><dt>주치동물병원</dt><dd>{clinic.name}</dd></>}
        </dl>
        {sameTopic && (
          <p className="note"><IconInfo small /> 같은 날 {TOPICS[ordered[0].topic!].label} 관련으로 {ordered.length}번 문의했어요.</p>
        )}
        <ol className="handoff-list">
          {ordered.map((c) => (
            <li key={c.id}>
              <div className="handoff-meta">{dateTime(c.created_at)} · <TopicChip card={c} /></div>
              <p><strong>보호자 질문</strong> {c.first_question}</p>
              {c.summary && <p><strong>AI 안내 요지</strong> {c.summary}</p>}
              <Tags tags={c.tags.filter((t) => t !== "in_progress")} />
            </li>
          ))}
        </ol>
        <p className="fine">AI가 상담 자료를 바탕으로 정리한 정보이며 진단이 아닙니다. 정확한 판단은 수의사에게 받으세요. · TwinVet Care</p>
      </div>
    </div>
  );
}

export function CareScreen({ profile, onExpired }: { profile: Profile | null; onExpired: () => void }) {
  const tabs = useTabs();
  const [cards, setCards] = useState<CareCard[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summaries, setSummaries] = useState<SummaryState>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [handoff, setHandoff] = useState<CareCard[] | null>(null);
  const [confirm, setConfirm] = useState<CareCard | null>(null);
  const started = useRef(new Set<string>());

  const fail = useCallback((e: unknown) => {
    if ((e as ApiError).status === 401) onExpired();
    else setError((e as ApiError).message);
  }, [onExpired]);

  useEffect(() => {
    api.care().then(({ items }) => setCards(items)).catch(fail);
  }, [fail]);

  const summarize = useCallback(async (id: string) => {
    started.current.add(id);
    setSummaries((s) => ({ ...s, [id]: "loading" }));
    try {
      const c = await api.summarize(id);
      setCards((cs) => cs?.map((x) => (x.id === id ? c : x)) ?? cs);
      setSummaries((s) => {
        const { [id]: _, ...rest } = s;
        return rest;
      });
    } catch (e) {
      if ((e as ApiError).status === 401) return onExpired();
      setSummaries((s) => ({ ...s, [id]: "error" }));
    }
  }, [onExpired]);

  // make missing/stale summaries one at a time (cheap model, first view only)
  useEffect(() => {
    if (!cards) return;
    const next = cards.find((c) => !c.summary_fresh && !c.tags.includes("in_progress") && !started.current.has(c.id));
    if (next && !Object.values(summaries).includes("loading")) summarize(next.id);
  }, [cards, summaries, summarize]);

  const reopen = (id: string) => {
    store.setConsultationId(id);
    tabs?.go("consult");
  };
  const remove = async (c: CareCard) => {
    try {
      await api.deleteConsultation(c.id);
      if (store.consultationId() === c.id) store.setConsultationId(null);
      setCards((cs) => cs?.filter((x) => x.id !== c.id) ?? cs);
    } catch (e) {
      fail(e);
    } finally {
      setConfirm(null);
    }
  };

  const clinic = profile?.clinic ?? null;
  const phone = clinicPhone(clinic, profile?.clinic_phone ?? null);
  const groups: CareGroup[] = cards ? groupCards(cards) : [];

  const cardView = (c: CareCard, inGroup = false) => (
    <article key={c.id} className={"care-card" + (inGroup ? " in-group" : "")}>
      <div className="care-head">
        <span className="care-date">{inGroup ? dateTime(c.created_at) : shortDate(c.created_at)}</span>
        {!inGroup && <TopicChip card={c} />}
      </div>
      <p className="care-q">{c.first_question}</p>
      <Summary card={c} state={summaries[c.id]} onRetry={() => summarize(c.id)} />
      <Tags tags={c.tags} />
      <div className="care-actions">
        <button type="button" className="btn" onClick={() => reopen(c.id)}>다시 열기</button>
        {!inGroup && <button type="button" className="btn" onClick={() => setHandoff([c])}>병원에 보여주기</button>}
        <button type="button" className="btn btn-ghost" onClick={() => setConfirm(c)}>삭제</button>
      </div>
    </article>
  );

  return (
    <div className="page-shell">
      <Header />
      <main className="page care">
        <h1 className="page-title">케어</h1>
        <section className="card care-top" aria-label="우리 아이">
          {profile ? (
            <>
              <strong>{dogSummary(profile.dog)}</strong>
              {clinic ? (
                <div className="care-clinic">
                  <span>주치동물병원: {clinic.name}</span>
                  <span className="place-actions">
                    {phone && <a className="btn btn-primary" href={telHref(phone)}>📞 전화</a>}
                    <a className="btn" href={kakaoMapHref(clinic)} target="_blank" rel="noopener noreferrer">카카오맵</a>
                  </span>
                </div>
              ) : (
                <button type="button" className="link" onClick={() => tabs?.go("settings")}>주치동물병원 등록</button>
              )}
            </>
          ) : (
            <p className="help">첫 상담을 하면 반려견 정보가 여기에 보여요. <button type="button" className="link" onClick={() => tabs?.go("settings")}>설정에서 입력</button></p>
          )}
        </section>

        <h2 className="section-title">최근 상담 <small>(7일 보관)</small></h2>
        {error && <p className="error-text" role="alert"><IconError small /> {error}</p>}
        {!cards && !error && <p className="help">불러오는 중…</p>}
        {cards && cards.length === 0 && (
          <div className="empty">
            <p>최근 7일 상담이 없어요.</p>
            <button type="button" className="btn btn-primary" onClick={() => tabs?.go("consult")}>상담하기</button>
          </div>
        )}
        <div className="care-list">
          {groups.map((g) => {
            if (g.cards.length === 1) return cardView(g.cards[0]);
            const latest = g.cards[0];
            const vet = g.cards.some((c) => c.vet_advised);
            const expanded = Boolean(open[g.key]);
            return (
              <article key={g.key} className="care-card group">
                <div className="care-head">
                  <span className="care-date">{shortDate(latest.created_at)}</span>
                  <TopicChip card={g} />
                  <span className="count">상담 {g.cards.length}건</span>
                </div>
                <p className="care-q">최근: {latest.first_question}</p>
                <Summary card={latest} state={summaries[latest.id]} onRetry={() => summarize(latest.id)} />
                {vet && <Tags tags={["vet_advised"]} />}
                <div className="care-actions">
                  <button type="button" className="btn" aria-expanded={expanded} onClick={() => setOpen((o) => ({ ...o, [g.key]: !expanded }))}>
                    {expanded ? "접기" : "펼치기"}
                  </button>
                  <button type="button" className="btn" onClick={() => setHandoff(g.cards)}>병원에 보여주기</button>
                </div>
                {expanded && <div className="group-items">{[...g.cards].reverse().map((c) => cardView(c, true))}</div>}
              </article>
            );
          })}
        </div>
        <p className="help"><IconInfo small /> AI 요약은 상담 내용을 정리한 것이며 진단이 아니에요.</p>
      </main>
      {handoff && <Handoff cards={handoff} profile={profile} onClose={() => setHandoff(null)} />}
      {confirm && (
        <Dialog title="이 상담을 삭제할까요?" onClose={() => setConfirm(null)} sheet
          actions={<>
            <button type="button" className="btn" onClick={() => setConfirm(null)}>취소</button>
            <button type="button" className="btn btn-danger" onClick={() => remove(confirm)}>삭제</button>
          </>}>
          <p>질문·답변·요약이 모두 삭제되며 되돌릴 수 없어요.</p>
        </Dialog>
      )}
    </div>
  );
}

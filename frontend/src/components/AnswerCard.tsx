// Four answer types + processing + failure, each told apart by icon, title text and border (docs/05 §8-4).
import { useState } from "react";
import { api, type ApiError, type CitationRef, type Feedback, type Message } from "../api";
import { RichText } from "../lib/richText";
import {
  IconBook, IconChat, IconCheck, IconDoc, IconError, IconInfo, IconQuestion, IconSearchOff, IconSpark, IconThumbDown, IconThumbUp,
} from "./Icons";

const time = (iso: string) => new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit" });

export function citationLabel(c: CitationRef, i: number): string {
  const kind = c.document_type === "reference" ? "전문 자료" : "상담 사례";
  return [`근거 ${i + 1}`, kind, c.department].filter(Boolean).join(" · ");
}

export function AnswerCard({ msg, onOpenSource }: { msg: Message; onOpenSource: (c: CitationRef, label: string) => void }) {
  const t = msg.response_type;
  if (t === "clarification") {
    return (
      <article className="answer is-clarify" aria-label="추가 확인 요청">
        <div className="answer-head"><span className="ai-tag"><IconSpark small /> AI 답변</span><span className="time">{time(msg.created_at)}</span></div>
        <h3 className="answer-title"><IconQuestion /> 정확히 안내하려면 조금 더 알려주세요</h3>
        <RichText text={msg.content} />
      </article>
    );
  }
  if (t === "insufficient_evidence") {
    return (
      <article className="answer is-insufficient" aria-label="근거 부족">
        <div className="answer-head"><span className="ai-tag"><IconSpark small /> AI 답변</span><span className="time">{time(msg.created_at)}</span></div>
        <h3 className="answer-title"><IconSearchOff /> 확인할 수 있는 자료가 부족해요</h3>
        <p style={{ margin: 0 }}>증상·기간·상황을 더 구체적으로 적어 주시거나 동물병원에 문의해 주세요.</p>
      </article>
    );
  }
  if (t === "out_of_scope") {
    return (
      <article className="answer is-scope" aria-label="지원 범위 밖">
        <div className="answer-head"><span className="ai-tag"><IconSpark small /> AI 답변</span><span className="time">{time(msg.created_at)}</span></div>
        <h3 className="answer-title"><IconInfo /> 반려견 건강 질문만 도와드릴 수 있어요</h3>
        <p style={{ margin: 0 }}>고양이나 다른 동물, 건강과 관계없는 질문에는 답하지 않아요.</p>
      </article>
    );
  }
  const cites = msg.citations ?? [];
  return (
    <article className="answer" aria-label="AI 답변">
      <div className="answer-head"><span className="ai-tag"><IconSpark small /> AI 답변</span><span className="time">{time(msg.created_at)}</span></div>
      <RichText text={msg.content} />
      {cites.length > 0 && (
        <div className="sources" aria-label="답변 근거">
          {cites.map((c, i) => {
            const label = citationLabel(c, i);
            return (
              <button key={c.id} type="button" className="chip" onClick={() => onOpenSource(c, label)}>
                {c.document_type === "reference" ? <IconDoc small /> : <IconBook small />}<span>{label}</span>
              </button>
            );
          })}
        </div>
      )}
      <FeedbackBar messageId={msg.id} initial={msg.feedback ?? null} />
    </article>
  );
}

function FeedbackBar({ messageId, initial }: { messageId: string; initial: Feedback | null }) {
  const [fb, setFb] = useState<Feedback | null>(initial);
  const [editing, setEditing] = useState(false);
  const [comment, setComment] = useState(initial?.comment ?? "");
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");

  const save = async (helpful: boolean, c: string | null) => {
    setStatus("saving");
    try {
      await api.feedback(messageId, helpful, c);
      setFb({ helpful, comment: c });
      setStatus("saved");
      setEditing(false);
    } catch (e) {
      setError((e as ApiError).message);
      setStatus("error");
    }
  };

  return (
    <div className="feedback">
      <button type="button" className="btn" aria-pressed={fb?.helpful === true} onClick={() => save(true, fb?.comment ?? null)}>
        <IconThumbUp small /> 도움 됐어요
      </button>
      <button type="button" className="btn" aria-pressed={fb?.helpful === false} onClick={() => save(false, fb?.comment ?? null)}>
        <IconThumbDown small /> 아쉬워요
      </button>
      <button type="button" className="btn" aria-expanded={editing} onClick={() => setEditing(!editing)}>
        <IconChat small /> 의견 남기기
      </button>
      {editing && (
        <div className="feedback-form">
          <label className="sr-only" htmlFor={"fb" + messageId}>의견</label>
          <textarea id={"fb" + messageId} className="textarea" rows={3} maxLength={300} placeholder="답변에 대한 의견을 남겨 주세요 (선택)"
            value={comment} onChange={(e) => setComment(e.target.value)} />
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <span className="counter">{comment.length}/300</span>
            <button type="button" className="btn btn-primary" disabled={status === "saving"}
              onClick={() => save(fb?.helpful ?? true, comment.trim() || null)}>저장</button>
          </div>
        </div>
      )}
      {status === "saved" && <span className="saved" role="status"><IconCheck small /> 저장됐어요</span>}
      {status === "error" && <span className="error-text" role="alert"><IconError small /> {error}</span>}
    </div>
  );
}

export function ProcessingCard() {
  return (
    <div className="answer processing" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <div>
        <strong>답변을 준비하고 있어요</strong>
        <div className="help">상담 사례와 수의학 자료를 확인하는 중이에요</div>
      </div>
    </div>
  );
}

export function FailedCard({ message, onRetry, retrying }: { message: string; onRetry: () => void; retrying: boolean }) {
  return (
    <div className="answer is-failed" role="alert">
      <h3 className="answer-title"><IconError /> 답변을 가져오지 못했어요</h3>
      <p style={{ margin: 0 }}>{message}</p>
      <div><button type="button" className="btn" onClick={onRetry} disabled={retrying}>다시 시도</button></div>
    </div>
  );
}

export { time };

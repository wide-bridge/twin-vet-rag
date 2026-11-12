// Bottom input bar (docs/05 §3, §9-3): one-line emergency notice, auto-growing textarea (max 4 lines),
// PC Enter = send / Shift+Enter = newline, mobile Enter = newline.
import { useEffect, useRef } from "react";
import { IconSend, IconWarning } from "./Icons";

const coarse = () => typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

export function Composer({ value, onChange, onSend, onEmergency, disabled, sendDisabled, maxChars, hint }: {
  value: string; onChange: (v: string) => void; onSend: () => void; onEmergency: () => void; disabled: boolean;
  sendDisabled: boolean; maxChars: number; hint?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const mobile = coarse();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = el.scrollHeight + 2 + "px";
  }, [value]);

  const tooLong = value.length > maxChars;
  const canSend = !disabled && !sendDisabled && value.trim().length > 0 && !tooLong;

  return (
    <div className="composer">
      <button type="button" className="emergency-line" onClick={onEmergency} aria-haspopup="dialog">
        <IconWarning small /> 응급하면 바로 동물병원에 연락하세요
      </button>
      <div className="composer-inner">
        <form className="composer-row" onSubmit={(e) => { e.preventDefault(); if (canSend) onSend(); }}>
          <label htmlFor="question" className="sr-only">질문</label>
          <textarea id="question" ref={ref} className="textarea" rows={1} placeholder="반려견에 대해 궁금한 점을 적어 주세요"
            value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} aria-invalid={tooLong}
            enterKeyHint={mobile ? "enter" : "send"}
            onKeyDown={(e) => {
              if (!mobile && e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (canSend) onSend();
              }
            }} />
          <button type="submit" className="btn btn-primary" disabled={!canSend}>보내기 <IconSend small /></button>
        </form>
        <div className="composer-meta">
          <span>{hint ?? "AI가 정리한 정보이며 진단이 아닙니다"}</span>
          <span style={{ color: tooLong ? "var(--color-danger)" : undefined, whiteSpace: "nowrap" }}>{value.length}/{maxChars}</span>
        </div>
      </div>
    </div>
  );
}

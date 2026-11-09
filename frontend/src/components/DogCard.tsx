// 우리 아이 정보 (docs/05 §3): breed + age required, health status optional and collapsed.
import { useId, useState } from "react";
import { BREEDS, type DogErrors, type DogForm } from "../lib/dogForm";
import type { Neutered } from "../api";
import { IconChevron, IconError, IconInfo, IconPaw } from "./Icons";

const NEUTERED: [Neutered, string][] = [["yes", "예"], ["no", "아니오"], ["unknown", "모름"]];

function Err({ id, text }: { id: string; text?: string }) {
  return text ? <p id={id} className="error-text"><IconError small /> {text}</p> : null;
}

export function DogCard({ form, errors, onChange }: { form: DogForm; errors: DogErrors; onChange: (f: DogForm) => void }) {
  const id = useId();
  const [open, setOpen] = useState(Boolean(form.weight || form.neutered || form.notes));
  const set = <K extends keyof DogForm>(k: K, v: DogForm[K]) => onChange({ ...form, [k]: v });
  const numeric = (v: string) => v.replace(/[^\d]/g, "").slice(0, 2);

  return (
    <section className="card dog-card" aria-labelledby={id + "h"}>
      <h2 id={id + "h"}>
        <span style={{ display: "flex", gap: 8, alignItems: "center" }}><span style={{ color: "var(--color-primary)" }}><IconPaw /></span>우리 아이 정보</span>
        <small><span className="req">*</span> 필수 항목</small>
      </h2>
      <div className="dog-grid">
        <div className="field">
          <label htmlFor={id + "b"}>품종 <span className="req" aria-hidden="true">*</span></label>
          <input id={id + "b"} className="input" list={id + "bl"} placeholder="예: 말티즈, 믹스견, 모름" maxLength={40}
            value={form.breed} onChange={(e) => set("breed", e.target.value)} aria-required="true"
            aria-invalid={Boolean(errors.breed)} aria-describedby={errors.breed ? id + "be" : undefined} autoComplete="off" />
          <datalist id={id + "bl"}>{BREEDS.map((b) => <option key={b} value={b} />)}</datalist>
          <Err id={id + "be"} text={errors.breed} />
        </div>
        <div className="field" role="group" aria-labelledby={id + "al"}>
          <span id={id + "al"} className="label">나이 <span className="req" aria-hidden="true">*</span></span>
          <div className="row-2">
            <div className="input-suffix">
              <input className="input" inputMode="numeric" placeholder="0" aria-label="나이 년" value={form.years}
                onChange={(e) => set("years", numeric(e.target.value))} aria-invalid={Boolean(errors.age)} aria-describedby={id + "ah"} />
              <span>년</span>
            </div>
            <div className="input-suffix">
              <input className="input" inputMode="numeric" placeholder="0" aria-label="나이 개월" value={form.months}
                onChange={(e) => set("months", numeric(e.target.value))} aria-invalid={Boolean(errors.age)} aria-describedby={id + "ah"} />
              <span>개월</span>
            </div>
          </div>
          {errors.age ? <Err id={id + "ah"} text={errors.age} /> : <p id={id + "ah"} className="help"><IconInfo small /> 정확하지 않아도 괜찮아요</p>}
        </div>
        <div className="span-2">
          <button type="button" className="toggle" aria-expanded={open} aria-controls={id + "hs"} onClick={() => setOpen(!open)}>
            건강상태 (선택) <IconChevron open={open} />
          </button>
        </div>
        {open && (
          <div id={id + "hs"} className="span-2 dog-grid" style={{ gap: 16 }}>
            <div className="field">
              <label htmlFor={id + "w"}>체중 (선택)</label>
              <div className="input-suffix">
                <input id={id + "w"} className="input" inputMode="decimal" placeholder="예: 3.2" value={form.weight}
                  onChange={(e) => set("weight", e.target.value.replace(/[^\d.]/g, "").slice(0, 5))} aria-invalid={Boolean(errors.weight)} />
                <span>kg</span>
              </div>
              <Err id={id + "we"} text={errors.weight} />
            </div>
            <div className="field" role="group" aria-labelledby={id + "nl"}>
              <span id={id + "nl"} className="label">중성화 (선택)</span>
              <div className="segmented">
                {NEUTERED.map(([v, label]) => (
                  <button key={v} type="button" aria-pressed={form.neutered === v} onClick={() => set("neutered", form.neutered === v ? null : v)}>{label}</button>
                ))}
              </div>
            </div>
            <div className="field span-2">
              <label htmlFor={id + "n"}>기존 질환·복용 약 (선택)</label>
              <textarea id={id + "n"} className="textarea" rows={2} maxLength={300} placeholder="예: 심장약 복용 중, 닭고기 알레르기"
                value={form.notes} onChange={(e) => set("notes", e.target.value)} aria-invalid={Boolean(errors.notes)} />
              <span className="counter">{form.notes.length}/300</span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// Per-browser conveniences only (docs/05 §3): the current consultation id for refresh recovery and the
// last dog form to prefill a new consultation. Never server state; storage may be unavailable.
import type { Dog } from "../api";
import { kstDay } from "./care";

const K_CONSULTATION = "tvc.consultation";
const K_DOG = "tvc.lastDog";
const K_CLINIC_PROMPT = "tvc.clinicPromptDone";

function get(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function set(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private mode / blocked storage: the app still works without it */
  }
}

export const store = {
  /** The consultation open on the 상담 tab — only for the Korean calendar day it was opened. On a new day the
   *  tab starts a new consultation (with the dog info as it is now); older ones are reopened from 케어. */
  consultationId: (): string | null => {
    const raw = get(K_CONSULTATION);
    if (!raw) return null;
    try {
      const v = JSON.parse(raw);
      return v && typeof v.id === "string" && v.day === kstDay(new Date().toISOString()) ? v.id : null;
    } catch {
      return null; // a plain id stored before 2026-11-12: treat as an earlier day
    }
  },
  setConsultationId: (id: string | null) =>
    set(K_CONSULTATION, id === null ? null : JSON.stringify({ id, day: kstDay(new Date().toISOString()) })),
  /** The last dog and when it was saved (a prefilled estimate is advanced by the time since). */
  lastDog: (): { dog: Dog; savedAt: string | null } | null => {
    const raw = get(K_DOG);
    if (!raw) return null;
    try {
      const v = JSON.parse(raw);
      const wrapped = Boolean(v && typeof v === "object" && v.dog);
      const d = (wrapped ? v.dog : v) as Partial<Dog>; // entries before birth dates stored the dog itself
      if (typeof d.breed !== "string" || !Number.isInteger(d.age_months)) return null;
      const dog: Dog = {
        name: typeof d.name === "string" ? d.name : null, breed: d.breed, age_months: d.age_months as number, age_basis: d.age_basis ?? "estimate",
        birth_year: d.birth_year ?? null, birth_month: d.birth_month ?? null, birth_day: d.birth_day ?? null,
        weight_kg: d.weight_kg ?? null, neutered: d.neutered ?? null, health_notes: d.health_notes ?? null,
      };
      return { dog, savedAt: wrapped ? v.savedAt ?? null : null };
    } catch {
      return null;
    }
  },
  /** the one-time "register your 주치동물병원" notice after the first consultation was answered or dismissed */
  clinicPromptDone: () => get(K_CLINIC_PROMPT) === "1",
  setClinicPromptDone: () => set(K_CLINIC_PROMPT, "1"),
  setLastDog: (d: Dog | null) => set(K_DOG, d ? JSON.stringify({ dog: d, savedAt: new Date().toISOString() }) : null),
};

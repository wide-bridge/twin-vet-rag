// Per-browser conveniences only (docs/05 §3): the current consultation id for refresh recovery and the
// last dog form to prefill a new consultation. Never server state; storage may be unavailable.
import type { Dog } from "../api";

const K_CONSULTATION = "tvc.consultation";
const K_DOG = "tvc.lastDog";

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
  consultationId: () => get(K_CONSULTATION),
  setConsultationId: (id: string | null) => set(K_CONSULTATION, id),
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
        breed: d.breed, age_months: d.age_months as number, age_basis: d.age_basis ?? "estimate",
        birth_year: d.birth_year ?? null, birth_month: d.birth_month ?? null, birth_day: d.birth_day ?? null,
        weight_kg: d.weight_kg ?? null, neutered: d.neutered ?? null, health_notes: d.health_notes ?? null,
      };
      return { dog, savedAt: wrapped ? v.savedAt ?? null : null };
    } catch {
      return null;
    }
  },
  setLastDog: (d: Dog | null) => set(K_DOG, d ? JSON.stringify({ dog: d, savedAt: new Date().toISOString() }) : null),
};

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
  lastDog: (): Dog | null => {
    const raw = get(K_DOG);
    if (!raw) return null;
    try {
      const d = JSON.parse(raw) as Dog;
      return typeof d.breed === "string" && Number.isInteger(d.age_months) ? d : null;
    } catch {
      return null;
    }
  },
  setLastDog: (d: Dog | null) => set(K_DOG, d ? JSON.stringify(d) : null),
};

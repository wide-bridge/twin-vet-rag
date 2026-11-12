// Care (P09, docs/05 §3-1) helpers: topic names/icons, status labels, same-day grouping, clinic links.
import type { CareCard, CareTag, Clinic, Topic } from "../api";

export const TOPICS: Record<Topic, { label: string; icon: string }> = {
  ear: { label: "귀", icon: "👂" },
  eye: { label: "눈", icon: "👁️" },
  skin: { label: "피부", icon: "🩹" },
  oral: { label: "구강", icon: "🦷" },
  digestive: { label: "소화", icon: "🍽️" },
  urinary: { label: "비뇨", icon: "💧" },
  respiratory: { label: "호흡", icon: "🫁" },
  other: { label: "기타/행동", icon: "🐾" },
};

export const TAGS: Record<CareTag, string> = {
  vet_advised: "수의사 확인 권유",
  in_progress: "답변 준비 중",
  clarifying: "추가 질문 중",
  insufficient: "근거 부족",
  out_of_scope: "지원 범위 밖",
};

const KST = "Asia/Seoul";
/** "2026-11-12" in Korean time (cards are grouped per Korean calendar day). */
export function kstDay(iso: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: KST, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(iso));
}
/** "11/12" */
export function shortDate(iso: string): string {
  const [, m, d] = kstDay(iso).split("-");
  return `${Number(m)}/${Number(d)}`;
}
/** "11/12 14:05" */
export function dateTime(iso: string): string {
  const t = new Intl.DateTimeFormat("ko-KR", { timeZone: KST, hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
  return `${shortDate(iso)} ${t}`;
}

export type CareGroup = { key: string; day: string; topic: Topic | null; cards: CareCard[] };

/** Same Korean day + same topic -> one group (newest first). A card without a topic yet stays alone. */
export function groupCards(cards: CareCard[]): CareGroup[] {
  const out: CareGroup[] = [];
  const index = new Map<string, CareGroup>();
  for (const c of cards) {
    const day = kstDay(c.created_at);
    const key = c.topic ? `${day}|${c.topic}` : `single|${c.id}`;
    let g = index.get(key);
    if (!g) {
      g = { key, day, topic: c.topic, cards: [] };
      index.set(key, g);
      out.push(g);
    }
    g.cards.push(c);
  }
  return out;
}

/** Phone used for the call button: the guardian's own entry first, then the public-data landline. */
export function clinicPhone(clinic: Clinic | null, typed: string | null): string | null {
  return typed || clinic?.public_phone || null;
}
export function telHref(phone: string): string {
  return "tel:" + phone.replace(/[^\d+]/g, "");
}
/** Kakao Map page for the clinic: the saved place URL, else a name search. */
export function kakaoMapHref(clinic: Clinic): string {
  return clinic.kakao_place_url || "https://map.kakao.com/link/search/" + encodeURIComponent(clinic.name);
}
export const KAKAO_24H_SEARCH = "https://map.kakao.com/link/search/" + encodeURIComponent("24시 동물병원");

export function distanceText(m: number | null): string {
  if (m === null) return "";
  return m < 1000 ? `${m}m` : `${(m / 1000).toFixed(1)}km`;
}

/** Browser location for "내 주변" (not stored anywhere). */
export function currentPosition(): Promise<{ x: number; y: number }> {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) return reject(new Error("unsupported"));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ x: p.coords.longitude, y: p.coords.latitude }),
      (e) => reject(e),
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  });
}

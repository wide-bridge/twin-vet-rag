// Age display (docs/05 §7). Screens show only the age — the birth date is stored (the day for birthday features)
// but not shown, since it repeats the age. How exact the age is still shows: "약" for a birth year only,
// "(추정)" for an estimate. The server-derived life stage is search-only and never shown.
import type { AgeBasis } from "../api";

export function formatAge(ageMonths: number): string {
  if (!Number.isInteger(ageMonths) || ageMonths < 0) throw new RangeError("ageMonths must be a non-negative integer");
  if (ageMonths === 0) return "1개월 미만";
  const years = Math.floor(ageMonths / 12);
  const months = ageMonths % 12;
  if (years === 0) return `${months}개월`;
  return months === 0 ? `${years}세` : `${years}세 ${months}개월`;
}

export function toAgeMonths(years: number, months: number): number {
  return years * 12 + months;
}

/** Same rule as the server (backend/app/domain/dog.py age_from_birth): year only assumes July 1. */
export function ageFromBirth(year: number, month: number | null, day: number | null, today: Date): number {
  const m = month ?? 7;
  let months = (today.getFullYear() - year) * 12 + (today.getMonth() + 1 - m);
  if (month && day && today.getDate() < day) months -= 1;
  return Math.max(months, 0);
}

export function isFutureBirth(year: number, month: number | null, day: number | null, today: Date): boolean {
  const ty = today.getFullYear(), tm = today.getMonth() + 1, td = today.getDate();
  if (year > ty) return true;
  if (month !== null && year === ty && month > tm) return true;
  return day !== null && year === ty && month === tm && day > td;
}

export function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

type AgeLike = { age_months: number; age_basis: AgeBasis };

/** "4세 8개월" (birth month known), "약 4세" (birth year only), "약 4세 6개월(추정)" (estimate) */
export function describeAge(d: AgeLike): string {
  if (d.age_basis === "day" || d.age_basis === "month") return formatAge(d.age_months);
  if (d.age_basis === "year") {
    const years = Math.floor(d.age_months / 12);
    return `약 ${years > 0 ? `${years}세` : formatAge(d.age_months)}`;
  }
  return `약 ${formatAge(d.age_months)}(추정)`;
}

type DogLike = AgeLike & { name: string | null; breed: string; weight_kg: number | null };

/** Consultation summary: "두부(푸들) | 11세 5개월 | 5kg" — without a name "푸들 | …", weight only if entered. */
export function dogSummary(d: DogLike): string {
  const who = d.name ? `${d.name}(${d.breed})` : d.breed;
  return [who, describeAge(d), d.weight_kg != null ? `${d.weight_kg}kg` : null].filter(Boolean).join(" | ");
}

// Age display (docs/05 §7). Screens show the age and, when known, the birth year/month — never the day,
// which is kept only for birthday features. The server-derived life stage is search-only and never shown.
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

type AgeLike = { age_months: number; age_basis: AgeBasis; birth_year: number | null; birth_month: number | null };

/** "2021년 3월생 · 4세 8개월", "2021년생 · 약 4세", "약 4세 6개월(추정)" */
export function describeAge(d: AgeLike): string {
  if ((d.age_basis === "day" || d.age_basis === "month") && d.birth_year && d.birth_month) {
    return `${d.birth_year}년 ${d.birth_month}월생 · ${formatAge(d.age_months)}`;
  }
  if (d.age_basis === "year" && d.birth_year) {
    const years = Math.floor(d.age_months / 12);
    return `${d.birth_year}년생 · 약 ${years > 0 ? `${years}세` : formatAge(d.age_months)}`;
  }
  return `약 ${formatAge(d.age_months)}(추정)`;
}

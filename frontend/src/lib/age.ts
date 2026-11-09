// Screens show only the age the guardian entered (docs/05 §7). The server-derived life stage
// is used for search only and is never rendered.
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

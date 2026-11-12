// Dog form values and validation, mirroring the server rules (docs/03 §6) so errors show before sending.
// Age: a birth date as far as the guardian knows it (year required, month and day optional), or an estimate.
import type { Dog, DogIn, Neutered } from "../api";
import { ageFromBirth, daysInMonth, isFutureBirth, toAgeMonths } from "./age";

export type AgeMode = "birth" | "estimate";
export type DogForm = {
  name: string;
  breed: string;
  ageMode: AgeMode;
  birthYear: string;
  birthMonth: string;
  birthDay: string;
  years: string;
  months: string;
  weight: string;
  neutered: Neutered | null;
  notes: string;
};
export type DogErrors = Partial<Record<"name" | "breed" | "age" | "weight" | "notes", string>>;

export const BREEDS = [
  "말티즈", "푸들", "포메라니안", "비숑 프리제", "시츄", "치와와", "요크셔테리어", "진돗개", "웰시코기",
  "골든 리트리버", "래브라도 리트리버", "닥스훈트", "프렌치 불독", "시바견", "보더콜리", "믹스견", "모름",
];
export const MAX_AGE_MONTHS = 371;
export const MAX_NAME = 20;
// same rule as the server: no control characters, markup-like brackets or backslash
const NAME_FORBIDDEN = /[\u0000-\u001f\u007f<>[\]{}`\\]/;

export const emptyDogForm = (): DogForm => ({
  name: "", breed: "", ageMode: "birth", birthYear: "", birthMonth: "", birthDay: "", years: "", months: "",
  weight: "", neutered: null, notes: "",
});

const monthsBetween = (from: Date, to: Date) =>
  Math.max(0, (to.getFullYear() - from.getFullYear()) * 12 + (to.getMonth() - from.getMonth()) - (to.getDate() < from.getDate() ? 1 : 0));

/** Prefill from a saved dog. A birth date stays exact; an estimate grows with the time since it was saved. */
export function formFromDog(d: Dog, savedAt?: string | null, today = new Date()): DogForm {
  const rest = {
    name: d.name ?? "",
    breed: d.breed,
    weight: d.weight_kg != null ? String(d.weight_kg) : "",
    neutered: d.neutered,
    notes: d.health_notes ?? "",
  };
  if (d.birth_year) {
    return {
      ...emptyDogForm(), ...rest, ageMode: "birth", birthYear: String(d.birth_year),
      birthMonth: d.birth_month ? String(d.birth_month) : "", birthDay: d.birth_day ? String(d.birth_day) : "",
    };
  }
  const since = savedAt ? new Date(savedAt) : null;
  const grown = since && !Number.isNaN(since.getTime()) ? monthsBetween(since, today) : 0;
  const age = Math.min(MAX_AGE_MONTHS, d.age_months + grown);
  return { ...emptyDogForm(), ...rest, ageMode: "estimate", years: String(Math.floor(age / 12)), months: String(age % 12) };
}

const isInt = (s: string) => /^\d+$/.test(s.trim());
const num = (s: string) => (s.trim() === "" ? null : Number(s));

/** Age in months the form describes, or null when incomplete/invalid (for the live preview). */
export function formAgeMonths(f: DogForm, today = new Date()): number | null {
  const { dog } = validateDog({ ...f, breed: f.breed.trim() || "-" }, today);
  if (!dog) return null;
  return dog.birth_year !== null ? ageFromBirth(dog.birth_year, dog.birth_month, dog.birth_day, today) : dog.age_months;
}

export function validateDog(f: DogForm, today = new Date()): { dog: DogIn | null; errors: DogErrors } {
  const errors: DogErrors = {};
  const name = f.name.trim();
  if (!name) errors.name = "이름을 입력해 주세요";
  else if (name.length > MAX_NAME) errors.name = `이름은 ${MAX_NAME}자 이내로 입력해 주세요`;
  else if (NAME_FORBIDDEN.test(name)) errors.name = "이름에 쓸 수 없는 기호가 있어요";
  const breed = f.breed.trim();
  if (!breed) errors.breed = "품종을 입력해 주세요";
  else if (breed.length > 40) errors.breed = "품종은 40자 이내로 입력해 주세요";

  let birth: Pick<DogIn, "birth_year" | "birth_month" | "birth_day"> = { birth_year: null, birth_month: null, birth_day: null };
  let estimate: number | null = null;
  if (f.ageMode === "birth") {
    const y = num(f.birthYear), m = num(f.birthMonth), d = num(f.birthDay);
    if (y === null) errors.age = "태어난 해를 선택해 주세요";
    else if (d !== null && m === null) errors.age = "태어난 달을 먼저 선택해 주세요";
    else if (d !== null && m !== null && d > daysInMonth(y, m)) errors.age = "없는 날짜예요";
    else if (isFutureBirth(y, m, d, today)) errors.age = "오늘 이후 날짜는 입력할 수 없어요";
    else if (ageFromBirth(y, m, d, today) > MAX_AGE_MONTHS) errors.age = "30년 11개월 이하로 입력해 주세요";
    else birth = { birth_year: y, birth_month: m, birth_day: d };
  } else {
    const y = f.years.trim(), m = f.months.trim();
    if (y === "" && m === "") errors.age = "대략적인 나이를 입력해 주세요";
    else if ((y !== "" && !isInt(y)) || (m !== "" && !isInt(m))) errors.age = "나이는 숫자로 입력해 주세요";
    else if (Number(y || 0) > 30 || Number(m || 0) > 11) errors.age = "나이는 0~30년, 0~11개월로 입력해 주세요";
    else estimate = toAgeMonths(Number(y || 0), Number(m || 0));
  }

  let weight: number | null = null;
  if (f.weight.trim() !== "") {
    weight = Math.round(Number(f.weight.trim()) * 10) / 10;
    if (!Number.isFinite(weight) || weight < 0.1 || weight > 120) errors.weight = "체중은 0.1~120kg 사이로 입력해 주세요";
  }
  if (f.notes.trim().length > 300) errors.notes = "300자 이내로 입력해 주세요";

  if (Object.keys(errors).length) return { dog: null, errors };
  return {
    dog: { name: name || null, breed, ...birth, age_months: estimate, weight_kg: weight, neutered: f.neutered, health_notes: f.notes.trim() || null },
    errors,
  };
}

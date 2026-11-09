// Dog form values and validation, mirroring the server rules (docs/03 §6) so errors show before sending.
import type { Dog, Neutered } from "../api";
import { toAgeMonths } from "./age";

export type DogForm = { breed: string; years: string; months: string; weight: string; neutered: Neutered | null; notes: string };
export type DogErrors = Partial<Record<"breed" | "age" | "weight" | "notes", string>>;

export const BREEDS = [
  "말티즈", "푸들", "포메라니안", "비숑 프리제", "시츄", "치와와", "요크셔테리어", "진돗개", "웰시코기",
  "골든 리트리버", "래브라도 리트리버", "닥스훈트", "프렌치 불독", "시바견", "보더콜리", "믹스견", "모름",
];

export const emptyDogForm = (): DogForm => ({ breed: "", years: "", months: "", weight: "", neutered: null, notes: "" });

export function formFromDog(d: Dog): DogForm {
  return {
    breed: d.breed,
    years: String(Math.floor(d.age_months / 12)),
    months: String(d.age_months % 12),
    weight: d.weight_kg != null ? String(d.weight_kg) : "",
    neutered: d.neutered,
    notes: d.health_notes ?? "",
  };
}

const isInt = (s: string) => /^\d+$/.test(s.trim());

export function validateDog(f: DogForm): { dog: Dog | null; errors: DogErrors } {
  const errors: DogErrors = {};
  const breed = f.breed.trim();
  if (!breed) errors.breed = "품종을 입력해 주세요";
  else if (breed.length > 40) errors.breed = "품종은 40자 이내로 입력해 주세요";

  const y = f.years.trim(), m = f.months.trim();
  if (y === "" && m === "") errors.age = "나이를 입력해 주세요";
  else if ((y !== "" && !isInt(y)) || (m !== "" && !isInt(m))) errors.age = "나이는 숫자로 입력해 주세요";
  else if (Number(y || 0) > 30 || Number(m || 0) > 11) errors.age = "나이는 0~30년, 0~11개월로 입력해 주세요";

  let weight: number | null = null;
  if (f.weight.trim() !== "") {
    weight = Math.round(Number(f.weight.trim()) * 10) / 10;
    if (!Number.isFinite(weight) || weight < 0.1 || weight > 120) errors.weight = "체중은 0.1~120kg 사이로 입력해 주세요";
  }
  if (f.notes.trim().length > 300) errors.notes = "300자 이내로 입력해 주세요";

  if (Object.keys(errors).length) return { dog: null, errors };
  return {
    dog: {
      breed,
      age_months: toAgeMonths(Number(y || 0), Number(m || 0)),
      weight_kg: weight,
      neutered: f.neutered,
      health_notes: f.notes.trim() || null,
    },
    errors,
  };
}

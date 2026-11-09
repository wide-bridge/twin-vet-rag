// API/DB codes stay as-is; only the on-screen wording changes (docs/05 §7).
export type LifeStage = "puppy" | "adult" | "senior";

export const LIFE_STAGE_LABEL: Record<LifeStage, string> = {
  puppy: "어린 강아지",
  adult: "성장한 반려견",
  senior: "나이 든 반려견",
};

export function lifeStageLabel(stage: LifeStage): string {
  return LIFE_STAGE_LABEL[stage];
}

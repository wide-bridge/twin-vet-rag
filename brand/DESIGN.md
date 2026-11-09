---
name: TwinVet Care
colors:
  primary: '#0060C0'
  on-primary: '#FFFFFF'
  primary-container: '#E6F0FA'
  secondary: '#402878'
  on-secondary: '#FFFFFF'
  tertiary: '#F86860'
  background: '#F7F9FC'
  surface: '#FFFFFF'
  on-surface: '#102858'
  on-surface-variant: '#526078'
  outline: '#7A879A'
  outline-variant: '#DCE3EE'
  error: '#B42318'
  error-container: '#FFF0EE'
typography:
  headline-lg: {fontFamily: Noto Sans, fontSize: 28px, fontWeight: '700', lineHeight: 38px}
  headline-sm: {fontFamily: Noto Sans, fontSize: 20px, fontWeight: '700', lineHeight: 28px}
  title-md: {fontFamily: Noto Sans, fontSize: 18px, fontWeight: '600', lineHeight: 26px}
  body-md: {fontFamily: Noto Sans, fontSize: 16px, fontWeight: '400', lineHeight: 26px}
  label-md: {fontFamily: Noto Sans, fontSize: 14px, fontWeight: '600', lineHeight: 20px}
rounded:
  DEFAULT: 0.75rem
  full: 9999px
spacing:
  margin: 1rem
  gutter: 1rem
---

# TwinVet Care — DESIGN.md (Stitch design system source)

Source of truth: brand/BRAND.md (tokens), docs/05_화면설계서.md §8–9 (screens, copy, responsive rules).

## Brand & Style
TwinVet Care is a Korean responsive web service where dog guardians get AI-organized health information drawn from consultation cases and veterinary references. It is NOT diagnosis, prescription, or a vet consultation.
Tone: calm, trustworthy, warm. Avoid both cold hospital-chart aesthetics and cute cartoon-app playfulness.
Header brand: the TwinVet logo image (speech-bubble mark + "TwinVet" wordmark, purple "Twin", blue "Vet") followed by the word "Care" in TwinVet Purple at the same cap height. In mockups, put only the plain text "TwinVet Care" there as a placeholder, with no icon (no paw, chat or other symbol) next to it; it is swapped for the real logo image after export. Never redraw, recolor or imitate the logo or its speech-bubble mark.

## Colors
- Primary action / TwinVet Blue `#0060C0` (hover/pressed `#004E9E`): primary buttons (상담 시작, 보내기), links, selected state, focus ring.
- Brand / TwinVet Purple `#402878`: page titles, brand emphasis, the "AI 답변" label.
- Text / Deep Navy `#102858`; muted text `#526078`; dividers `#DCE3EE`; input borders `#7A879A`.
- Background `#F7F9FC`; surfaces/cards `#FFFFFF`.
- Small accents only: Coral `#F86860` (care/warmth, tiny icons), Leaf Green `#58B828`, Warm Orange `#FF7000`. Never on primary buttons; never white text on them.
- Emergency notice and errors only: text `#B42318` on `#FFF0EE`. No other red anywhere.
- States are never color-only: always icon + text label.

## Typography
Noto Sans (Korean). Body ≥ 16px with generous line height; inputs 16px (prevents iOS zoom).

## Layout
Responsive web. Mobile (≤767px): single column, 16px side margins, input bar fixed at bottom. Tablet (768–1023px): single column, content max-width 720px centered. PC (≥1024px): content max-width 720px centered; source detail opens as a right side panel (~360px). No horizontal scroll at any width. Touch targets ≥ 44px.

## Shapes & Elevation
12px radius for cards, inputs, dialogs; pill chips. Flat surfaces with 1px `#DCE3EE` borders; only overlays use a soft shadow.

## Components
- Answer cards: four distinct types, each with icon + title text + border style: 근거 있는 답변 (normal card, labeled "AI 답변", source chips below), 추가 확인 (question card), 근거 부족 (muted dashed card), 범위 밖 (neutral info card). Failure state shows error text + [다시 시도].
- Source chips appear only on grounded answers ("근거 1 · 상담 사례 · 내과").
- Emergency: S01 shows a full emergency box; S02 shows a single fixed line above the input ("⚠ 응급하면 바로 동물병원에 연락하세요"). It is static guidance, never styled like an AI answer, and never an AI "triage" badge.

## Never include
Login/sign-up, conversation history sidebar, animal-type selector (cats etc.), life-stage labels or age ranges ("노령견", "7세 이상"), vet-verified badges, live vet chat, image/voice input, booking/payment, star ratings, AI mascot/avatar, white-coat doctor imagery, triage/urgency scoring badges, quoted source passages.

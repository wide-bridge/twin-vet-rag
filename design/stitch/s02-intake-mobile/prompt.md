# S02 상담 첫 진입 · 모바일 (MOBILE)

TwinVet Care: Korean responsive web service for dog guardians. AI organizes health information from consultation cases and veterinary references; it is NOT diagnosis, prescription or a vet chat. Use the project design system for all colors, fonts and radii. All UI text in Korean, exactly as given below; do not invent features, promises or extra copy. Header shows the brand text 'TwinVet Care' (Twin and Care purple, Vet blue). Do NOT include: login, conversation history sidebar, animal-type selector, life-stage labels or age ranges, vet-verified badges, live vet chat, image/voice input, booking, star ratings, AI mascot or avatar, doctor imagery, triage/urgency badges, quoted source passages.

Consultation screen before the first question, mobile 390px:
1. Header: brand text 'TwinVet Care' left, a menu icon button right (menu holds '새 상담', '대화 삭제').
2. Card '우리 아이 정보' with fields stacked vertically: text input with autocomplete '품종 *' (placeholder '예: 말티즈, 믹스견, 모름'); age row '나이 *' with two small number inputs '년' and '개월' side by side and helper text '정확하지 않아도 괜찮아요'; a collapsed toggle row '건강상태 (선택) ▸'.
3. Section '이런 질문을 할 수 있어요' with four tappable example chips: '하루에 두 번 토했는데 병원에 가야 할까요?', '귀를 자주 긁고 냄새가 나요', '슬개골 탈구는 집에서 어떻게 관리하나요?', '눈곱이 갑자기 많아졌어요'.
4. Empty conversation area.
5. Bottom area fixed to the bottom: one short line with a small warning icon '응급하면 바로 동물병원에 연락하세요' (single line, red text, compact), then a multiline text input '반려견에 대해 궁금한 점을 적어 주세요' with character count '0/1000' and a primary send button '보내기' (disabled look because 품종 and 나이 are empty), and tiny helper text 'AI가 정리한 정보이며 진단이 아닙니다'.

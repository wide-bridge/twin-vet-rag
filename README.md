# twin-vet-rag
반려동물 상담 데이터 기반 RAG 서비스 — TwinVet Care (PoC: 반려견 전용 RAG 상담)

설계 문서: [docs/](docs/) (서비스기획서·사용자흐름·데이터모델·개발명세·화면설계 5종)

## 환경
- Python: 기존 conda 환경 `smart_tour` (Python 3.12). 새 가상환경을 만들지 않는다.
- Node 20.10.0 + Vite 6.4.5 (Vite 7은 Node 20.19+ 필요 — docs/04 §8)
- Docker: `pgvector/pgvector:pg16` (호스트 포트 55433)

```powershell
conda activate smart_tour
pip install -r requirements.txt      # 필요한 패키지만 추가. 기존 패키지 일괄 업그레이드 금지
docker compose up -d db              # 프로젝트명 twinvet-care, 포트 55433
```

## 설정 우선순위
프로세스 환경변수 > 프로젝트 `.env` > `ENV_FILE`(기본 `D:\PyProject\env_keys\.env`) > 코드 기본값.
설정 이름은 [.env.example](.env.example) 참고. 비밀값은 저장소·프론트 번들(VITE_ 변수)에 넣지 않는다.

## 실행 (개발)
```powershell
docker compose up -d db
alembic upgrade head
uvicorn backend.app.main:app --reload --port 8000     # API + 답변 워커(스레드). 문서: http://localhost:8000/api/docs
cd frontend; npm ci; npm run dev                      # http://localhost:5173, /api는 8000으로 프록시
```
`cd frontend; npm run build` 후에는 FastAPI가 `frontend/dist`를 같은 주소(http://localhost:8000)로 제공한다.
OPENAI_API_KEY가 없으면 API는 뜨지만 질문은 처리되지 않는다(health의 `openai_key: missing`).

## 데이터 전처리 (로컬, 유료 API 미사용)
```powershell
python -m scripts.prepare_data --version v2
```
`AIHUB_DATA_DIR9.반려견 성장 및 질병 관련 말뭉치 데이터`를 읽어 `DATA_OUTPUT_DIR/prepared/v2/`에
qa_cases / reference_docs / reference_chunks / eval_dev / eval_final / excluded / spacing_changes / report 를 만든다.
원본은 수정하지 않는다. 띄어쓰기 복원(조사·어미 앞 공백, 원천의 이↔가 뒤바뀜)은 정제본에만 적용하고
모든 수정은 `spacing_changes.jsonl`에 남는다. 평가 세트 질문은 입력된 그대로 둔다. 153번 데이터는 사용하지 않는다.

## DB 마이그레이션·지식 적재
```powershell
alembic upgrade head                                            # 스키마 생성·갱신
python -m scripts.load_knowledge --version v2 --embedder fake    # 비용 없음, 의미 없는 시험용 벡터
python -m scripts.load_knowledge --version v2 --embedder openai --max-usd 1.0   # 유료: 예상 비용 표시 후 확인
python -m scripts.load_knowledge --deactivate <source_record_id>                 # 즉시 검색 제외
```
임베딩 입력은 `자료명/진료과`(참고 자료) 또는 `진료과/질병`(상담 사례) 머리말 한 줄 + 본문이다(화면 표시 본문은 머리말 없음).
현재 색인: prepared/v2 + text-embedding-3-small, 27,574청크, 실제 17,367,967토큰(약 $0.35), 2026-11-09 적재. 처리 단계·보장은 docs/03 7절.

## RAG 답변 (docs/04 4절)
분류(gpt-5-nano: 범위·추가 확인·검색 문장) → pgvector 검색(생애주기 필터, top 6) → 최고 점수 < `RETRIEVAL_MIN_SCORE`(0.40)면 근거 부족
→ 답변(gpt-5-mini, JSON) → 사용 근거 번호를 이번 검색 결과와 대조(유효 근거 없으면 근거 부족). 모델·임계값·top_k는 `.env.example` 설정.
질문은 DB에 실행(run)으로 저장되고 API 프로세스 안의 워커 스레드가 처리한다. 새로고침·재시작 후에도 상태가 남고, 오래 걸린 실행은 실패(재시도 가능)로 바뀐다.

## 평가
```powershell
python -m scripts.eval_retrieval --subset dev --n 200                   # 검색 지표(질문 임베딩만, 약 $0.001)
python -m scripts.eval_answers --subset dev --n 30 --judge              # 답변 전체 흐름 + LLM 채점(약 $0.08)
```
2026-11-10 dev 표본 결과: 검색 dept@6 0.96(200문항) / 답변 30문항 grounded 83%, 인용 진료과 일치 100%,
채점 일치도 4.88·안전성 5.0(5점 만점, LLM 채점은 참고 지표), 질문당 약 $0.002, 지연 중앙값 약 14초.
최종 검증군(final)은 확정 직전에만 쓴다.

## 검사·빌드·테스트
```powershell
python -m pytest -q                                   # 단위 테스트
$env:TWINVET_DB_TESTS="1"; python -m pytest -q        # DB·API 포함 (docker compose up -d db 필요, twinvet_test DB 사용)
cd frontend; npm run typecheck; npm run build
pip install -r requirements-dev.txt                   # 브라우저 점검 도구(Playwright, 설치된 Chrome 사용)
python tests/e2e/ui_smoke.py --widths 320,390,768,1280   # 실행 중인 서버 대상, 실제 LLM 호출(수 센트), 스크린샷 data_output/e2e
```

## 운영 작업
```powershell
python -m scripts.cleanup_sessions      # 만료 24시간 지난 세션과 상담 데이터 영구 삭제. 하루 1회 예약 실행
```

## 배포 (방향: 단일 컨테이너 + 관리형 PostgreSQL)
```powershell
docker compose --profile app up -d --build    # 이미지 = React 빌드 + FastAPI, 시작 시 alembic upgrade head
```
- 이미지 하나가 API와 화면을 같은 주소로 제공한다. 앞단에 HTTPS(리버스 프록시 또는 플랫폼 TLS)가 필수다(세션 쿠키 Secure).
- 환경변수: `DATABASE_URL`, `OPENAI_API_KEY`(플랫폼 비밀값으로 주입, 이미지·저장소에 넣지 않음), `ALLOWED_ORIGINS`(서비스 주소), `COOKIE_SECURE=true`.
- DB: pgvector가 되는 PostgreSQL 16. 지식 색인은 로컬에서 만든 DB를 `pg_dump`로 옮기거나 서버에서 `load_knowledge`를 실행한다(원천 데이터는 로컬 D 드라이브에만 있음).
- 답변 워커가 API 프로세스 안에 있으므로 **API 프로세스는 1개**로 운영한다(재시작 시 진행 중 실행은 실패 처리 → 사용자가 재시도).
- 배포 대상·도메인·백업 정책은 미확정(docs/04 6절). 공개 전 원문 이용 조건 확인 필요.

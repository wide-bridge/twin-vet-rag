# twin-vet-rag
반려동물 상담 데이터 기반 RAG 서비스 — TwinVet Care (PoC: 반려견 전용 RAG 상담)

설계 문서: [docs/](docs/) (서비스기획서·사용자흐름·데이터모델·개발명세·화면설계 5종)

**서비스 주소:** https://twinvet.pawcheck.ai (Vercel + Neon, 2026-11-11 배포·점검 완료. 예비 주소 https://twin-vet-rag.vercel.app). 자세한 내용은 아래 '배포' 절.

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
python -m scripts.load_clinics                                  # 공공데이터 동물병원 목록(약 1만 건) 적재·갱신. DATA_GO_KR_SERVICE_KEY 필요
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

## MVP1 수의사 HITL (docs/01 4절, docs/04 7절)
작업 화면: `/review` (보호자 화면과 별개, 접근 키 로그인). A단계(참여 수의사 0명)에는 운영자만 접근한다.
```powershell
python -m scripts.hitl_admin seed-ontology                                  # 온톨로지 0.1 초안(draft) 적재
python -m scripts.hitl_admin create-reviewer --role operator --name "운영자"  # 접근 키는 한 번만 표시(해시만 저장)
python -m scripts.hitl_admin mock-items                                     # 모의 검토 묶음 + AI 제안
python -m scripts.hitl_admin dataset-items --n 5 --max-usd 0.1 --yes        # 실제 질문 묶음 + 자동 점검(유료, 묶음당 약 $0.008)
python -m scripts.hitl_admin recheck --item <id> --yes                     # 같은 질문 재답변·재점검(유료)
python -m scripts.hitl_admin issues                                         # 자동 점검 문제 유형 집계
python -m scripts.hitl_admin evaluate --candidate <id> --embedder openai    # 검색 영향 점검(참고 지표, 품질 판정 아님)
python -m scripts.hitl_admin activate --candidate <id> --embedder openai --yes
python -m scripts.hitl_admin status
python -m scripts.make_split_table --version v2                             # 분할표(seed 20261113, 진료과 층화, 중복 그룹 같은 fold)
```
- 현재 상태(2026-11-14): 수의사 없이 진행하는 **출처 근거 품질 점검** 중이다. 실제 질문 묶음·자동 점검·재점검, 비교 실행 기록·프롬프트 선택, 적재 되돌리기가 구현됐다(아래 '비교 실행과 되돌리기'). 아래 발행·승인 규칙은 수의사 갈래의 현재 구현이다(docs/01 §4).
- 운영 규칙(`hitl_production`): Vercel(APP_ENV=production) 또는 로컬이 아닌 DB(Neon)에 연결하면 운영 환경으로 본다. 운영 환경에서는 운영자가 발행 승인할 수 없고, 모의 지식은 활성화되지 않으며, 검색도 모의 지식을 반환하지 않는다.
- 재검증: 수의사 2명의 선택 항목(정확성·근거·충분성·안내·안전 문제·재사용 범위)이 모두 같아야 독립 재검증 완료, 하나라도 다르면 이견.
- 활성화 조건: 독립 재검증 완료 사례 + 후보를 작성·수정하지 않은 수의사의 승인(내용 해시 고정) + 같은 내용·같은 임베딩 모델의 유효한 검색 영향 점검 통과(실제 후보는 비교 가능한 질문 30개 이상). 적용 조건은 발행 본문에 들어간다. 발행 문서는 dataset_code=vetreview, document_type=vet_knowledge.
- 검토 기록·지식 후보는 RAG 검색 대상이 아니다. 상담 원문을 지식으로 직접 넣지 않는다.

### 비교 실행과 되돌리기 (docs/04 §7 '수의사 없는 품질 점검')

```bash
# 비교 실행: 한 번에 하나만 바꾼다. 결과는 DATA_OUTPUT_DIR/eval/runs/<run_id>.json (유료, 사례당 약 $0.008)
python -m scripts.eval_answers --cases cases.json --prompt current --yes            # cases.json = [{"id": "<eval_dev id>", "role": "dev"|"confirm"}]
python -m scripts.eval_answers --inputs-from <run.json> --prompt <변형> --yes       # 같은 질문·반려견·생애주기·대화로 재실행
python -m scripts.eval_answers --rescore <run.json> --yes                           # 점검기만 다시(답변 생성 없음)

# 원천 본문 정제 적재: 제목·진료과·질병명·생애주기·메타데이터가 바뀌거나 새 문서가 있으면 쓰기 전에 거부
python -m scripts.load_knowledge --version <정제본> --embedder openai --content-only
# 되돌리기: 출력된 적재 실행 ID로. 전체 복귀 또는 전체 거부(변경 없음)
python -m scripts.load_knowledge --rollback-run <ingestion_run id>
```

- 사례는 eval_dev에서만 고른다(eval_final은 `--final-check`일 때만). 검색 색인에 들어 있는 사례는 거부한다.
- 프롬프트 변형은 `rag.prompts.ANSWER_SYSTEM_VARIANTS`에 추가한다. 앱은 항상 `current`를 쓰고, 비교로 채택이 확정되면 기본값을 바꾸고 PROMPT_VERSION을 올린다.
- 되돌리기 거부 사유: 직전 버전 기록 없음(이 기능 이전 실행), 이미 되돌림, 이후 다른 실행이 다시 바꾼 문서, 삭제된 문서, 직전 버전 청크·임베딩 없음, 직전 버전 임베딩 모델 다름. 하나라도 있으면 아무것도 바꾸지 않고 목록만 출력한다.
- 적재 활성화·되돌리기·문서 비활성/삭제는 같은 데이터셋 잠금을 써서 동시에 돌지 않는다.
- 운영(Neon) 반영 절차: 로컬에서 비교·되돌리기를 확인한 정제본(파일 체크섬)·설정·임베딩 모델을 그대로 `load_knowledge --content-only`로 적재(사용자 실행) → 새 버전 활성화·수정 근거 검색·이전 내용 미잔존 확인 → 문제 시 `--rollback-run`.

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
python -m scripts.cleanup_sessions      # 만료 24시간 지난 세션·상담 + 보관 기한 지난 반려견 프로필 영구 삭제. 하루 1회 예약 실행
python -m scripts.load_clinics          # 동물병원 목록 갱신(개업·폐업·주소·전화). 원천은 매일 갱신, 주 1회 권장
```

### 설정·주치동물병원·케어 (P08·P09, 2026-11-12)
- 설정: 반려견 정보와 주치동물병원을 `dog_profile`에 저장. 브라우저별 익명 쿠키 `tvc_profile`(해시만 저장), 마지막 사용 후 `PROFILE_TTL_DAYS`(기본 90)일 보관. 첫 상담 때 자동 저장되고 이후 상담은 저장된 정보로 시작. 상담의 반려견 정보는 생성 시점 복사본이라 설정을 바꿔도 지난 상담은 그대로.
- 병원 찾기: `GET /api/v1/clinics/search`가 카카오 로컬 검색 결과를 화면에 그대로 전달(저장·로그 없음). 고르면 `POST /clinics/match`가 공공데이터 목록에서 같은 병원(도로명주소+이름)을 찾고, 저장은 공공데이터 레코드 + 카카오 장소 ID·링크만. 못 찾으면 보호자가 이름을 직접 입력. 전화번호는 보호자가 입력·확인. 실측 매칭률 110/112(2026-11-12, 8개 지역 검색).
- 응급 안내: 누르면 주치동물병원 전화·카카오맵, 가까운 24시 동물병원(위치 권한, 위치 저장 안 함), 전화할 때 말할 것.
- 케어: 상담 1건 = 카드 1장(7일 보관). 처음 볼 때 `LLM_CLASSIFIER_MODEL`로 주제(8개)·2문장 요약·수의사 확인 권유 여부를 만들어 상담에 저장하고, 대화가 늘면 다시 만든다. 같은 날 같은 주제는 묶음 카드. [병원에 보여주기]는 저장된 요약으로 화면에서만 구성(추가 호출·저장 없음).

## 배포 (DB: Neon PostgreSQL / 앱: Vercel, 2026-11-11 배포·점검 완료)
로컬·Docker 실행(선택). 실제 서비스 배포는 아래 'Vercel 배포' 절을 따른다.
```powershell
docker compose --profile app up -d --build    # 이미지 = React 빌드 + FastAPI, 시작 시 alembic upgrade head
```
- 이미지 하나가 API와 화면을 같은 주소로 제공한다. 앞단에 HTTPS(리버스 프록시 또는 플랫폼 TLS)가 필수다(세션 쿠키 Secure).
- 환경변수: `DATABASE_URL`, `OPENAI_API_KEY`(플랫폼 비밀값으로 주입, 이미지·저장소에 넣지 않음), `ALLOWED_ORIGINS`(서비스 주소), `COOKIE_SECURE=true`.
- DB: pgvector가 되는 PostgreSQL 16. 지식 색인은 로컬에서 만든 DB를 `pg_dump`로 옮기거나 서버에서 `load_knowledge`를 실행한다(원천 데이터는 로컬 D 드라이브에만 있음).
- 답변 워커가 API 프로세스 안에 있으므로 **API 프로세스는 1개**로 운영한다(재시작 시 진행 중 실행은 실패 처리 → 사용자가 재시도).
- DB는 Neon 무료 플랜의 전용 프로젝트 twin-vet-care로 확정(2026-11-10 사용자 결정). 기존 테이블명 유지. 앱 호스팅은 Vercel로 결정(2026-11-11). 정기 백업 운영 정책은 미확정. 원문 이용 조건 검토는 사용자 요청으로 후속 작업으로 둔다.

### Neon 연결 및 이전 순서 (2026-11-11 수행 완료)

- 결과: `python -m scripts.neon_restore`(덤프 sha256 확인 → 빈 DB에 pg_restore → 검증)로 복원했고 `--verify` 결과 PASS. document 19,417 / document_version 38,834 / chunk·임베딩 27,574, alembic head 7c2a9e1f4b10, pgvector 0.8.0, HNSW 색인 사용 확인, 상담 표 비어 있음, DB 511 MB.
- 연결 문자열은 git 제외 파일 `.env.neon`(NEON_DIRECT_URL, NEON_POOLED_URL)에만 둔다.
- 아래는 다시 이전할 때의 절차 기록이다.

1. Neon에서 전용 프로젝트 twin-vet-care와 PostgreSQL 16 DB를 만든다. 다른 앱의 DB와 합치지 않으며 테이블 접두사는 추가하지 않는다.
2. Connect에서 실제 연결 문자열을 가져온다. 앱 DATABASE_URL은 pooled 연결, ALEMBIC_DATABASE_URL은 direct 연결을 사용한다. 둘 다 같은 프로젝트·브랜치·DB를 가리켜야 한다.
3. Python/Alembic에는 postgresql+psycopg:// 형식으로 넣고 sslmode=require 등 원래 쿼리 옵션은 유지한다. pg_dump/pg_restore에는 일반 postgresql:// 형식을 사용한다. 비밀번호의 특수문자는 URL 인코딩한다. 비밀값은 플랫폼 secret 또는 git에서 제외된 .env에만 저장한다.
4. 이전 전 로컬 DB 크기를 확인한다: SELECT pg_size_pretty(pg_database_size(current_database())); 테이블·벡터·색인 전체와 향후 상담 데이터가 무료 용량에 들어가야 한다. 현재 플랜 한도는 https://neon.com/pricing 에서 재확인한다.
5. 기존 임베딩을 재사용한다. 쓰기를 중단한 로컬 DB에서 pg_dump -Fc --no-owner --no-acl --file=<안전한_백업경로> <로컬_direct_URL> 로 전체 덤프를 만들고, 비어 있는 Neon DB에 pg_restore --no-owner --no-acl --exit-on-error --dbname=<Neon_direct_URL> <덤프경로> 로 복원한다. 명령의 URL은 비밀값을 기록하지 않도록 환경변수/비밀번호 파일로 전달한다. PostgreSQL 16 클라이언트를 사용한다.
6. 전체 복원에는 alembic_version·스키마·벡터가 포함된다. 복원 전에 alembic upgrade head로 테이블을 먼저 만들지 않는다. 복원 실패 시 앱을 시작하지 말고 오류를 해결한다. 기존 데이터가 있는 DB에 --clean을 사용하지 않는다.
7. 복원 후 alembic upgrade head를 실행하고, 로컬/Neon의 주요 테이블(document, document_version, chunk) 행 수와 pgvector 확장·벡터 색인·실제 검색 결과를 비교한다. 덤프에는 로컬 테스트 상담도 포함되므로 이전 대상 데이터는 전송 전에 검토한다.
8. 로컬 Docker에서 Neon을 연결할 때는 docker compose -f docker-compose.neon.yml up -d --build 를 사용한다. 이 파일은 단독 사용하며 로컬 db 컨테이너를 띄우지 않는다. 기존 docker-compose.yml은 로컬 개발 전용으로 유지한다.
9. 호스팅(Vercel, 아래 절)에 환경변수를 설정하고 HTTPS 주소를 ALLOWED_ORIGINS에 넣는다. Docker로 운영할 경우 이미지 포트는 `$PORT`(기본 8000), API 프로세스는 1개다. /api/v1/health와 질문·근거·피드백·삭제 흐름을 확인한다. 이 확인은 실제 모델 비용이 발생한다.
10. 무료 DB의 복구 기능을 7일 백업으로 간주하지 않는다. 배포 전 덤프를 안전하게 보관하고 별도 빈 DB에서 복구 검증한다. 정기 백업과 cleanup_sessions 하루 1회 실행은 아직 예약되지 않았다.

### Vercel 배포 (2026-11-11 결정: 앱 Vercel + DB Neon)

- 구성: 루트 `index.py`가 FastAPI `app`을 내보내고(Vercel 진입점), `vercel.json`이 React 빌드(`cd frontend && npm ci && npm run build`), 지역 `sin1`(Neon 싱가포르와 같은 지역), 함수 최대 300초(Hobby 한도)를 정한다. 빌드된 화면은 `app.frontend()`로 CDN에 올라간다.
- 답변 처리: Vercel에는 상시 실행 워커가 없다. `VERCEL` 환경에서는 `RUN_MODE=request`가 기본이 되어 워커 스레드를 띄우지 않고, 브라우저가 질문 접수 직후 `POST /api/v1/runs/{id}/execute`를 호출해 그 요청 안에서 답변을 만든다(약 10~20초). 여러 번 호출해도 한 번만 처리하고, 새로고침 뒤 대기 중인 작업은 화면이 다시 실행을 요청한다. 시간 초과 정리는 조회 시 수행한다. Docker·로컬은 기존 워커 방식(`RUN_MODE=worker`).
- Vercel 환경변수(Production·Preview): `DATABASE_URL`=Neon **pooled** URL(`postgresql://`→`postgresql+psycopg://`), `OPENAI_API_KEY`, `KAKAO_REST_API_KEY`(병원 검색), `ALLOWED_ORIGINS`=연결한 도메인(예: `https://twinvet.pawcheck.ai`). `*.vercel.app` 배포 주소는 자동 허용.
- 마이그레이션은 Vercel에서 실행하지 않는다. 스키마 변경 시 로컬에서 `ALEMBIC_DATABASE_URL`=Neon direct URL로 `alembic upgrade head`.
- 런타임 의존성은 `requirements.txt`(테스트·데이터 준비 도구는 `requirements-dev.txt`). 업로드 제외는 `.vercelignore`.
- 요청 횟수 제한은 함수 인스턴스별 메모리 값이라 인스턴스가 여러 개면 느슨해진다(PoC 허용). 만료 세션 정리는 Vercel Cron으로 옮길 수 있다(미적용).

### 무료 운영 점검 결과 (2026-11-11)

- **용량:** Neon 무료 한도는 프로젝트당 1 GB(2026-10 기준, https://neon.com/pricing 재확인). 로컬 DB는 1,043 MB로 보이지만 대부분 임베딩 갱신 때 생긴 빈 공간이다. pg_dump → 빈 DB pg_restore로 측정한 실제 이전 크기는 **512 MB**(chunk 표 259 MB + 벡터 색인 215 MB, 나머지 약 40 MB). 상담 데이터는 수백 KB 수준이다.
  - 여유를 더 원하면 벡터를 halfvec(반정밀도)로 바꾸면 **293 MB**(재임베딩 없이 형 변환, 색인 108 MB). 검색 품질 비교 후 결정한다(미적용).
  - 색인을 다시 만들거나 재적재하면 일시적으로 공간이 더 필요하므로, 재적재는 로컬에서 하고 덤프로 옮긴다.
- **워커:** 대기 중에는 DB를 조회하지 않는다. 질문이 접수되면 API가 워커를 깨우고(notify), 처리 중일 때만 10초마다 시간 초과를 정리한다. 시작 시 남은 queued 작업은 한 번 확인한다. `WORKER_IDLE_POLL_SECONDS`(기본 0=조회 안 함)로 대기 중 안전 조회를 켤 수 있으나, 5분 이하로 두면 Neon 절전을 막는다.
- **헬스 체크:** 호스팅 플랫폼의 헬스 체크 경로는 DB에 접속하지 않는 `/api/v1/live`로 설정한다. `/api/v1/health`는 DB·pgvector·활성 문서 수를 확인하는 수동 점검용이다. 외부 업타임 감시로 주기 호출하지 않는다.
- **연결 풀:** SQLAlchemy `pool_pre_ping=True`로 DB 절전 뒤 끊긴 연결을 자동으로 다시 연다. 트랜잭션을 열어 둔 채 대기하지 않는다(답변 생성 중에는 트랜잭션을 닫음).
- DB/API 자동 테스트는 로컬 DB만 사용한다. Neon DATABASE_URL을 둔 채 TWINVET_DB_TESTS=1을 실행하지 않는다(fixture가 원격 호스트를 차단).
- **이전 전 정리:** 테스트 상담을 제외한 덤프로 이전했고, 복원 후 상담 관련 표가 비어 있음을 확인했다.
- **Neon 연결 확인(로컬 → Neon):** 로컬 서버를 Neon pooled URL로 띄워 브라우저 점검(4개 폭)과 실제 질문·답변을 확인했고, 점검 상담은 삭제해 상담 표가 비어 있음을 재확인했다(2026-11-11 `--verify` PASS).
- **Vercel 배포 (2026-11-11 확인):** 프로젝트 twin-vet-rag(FastAPI 단일 프로젝트), 주소 https://twin-vet-rag.vercel.app , 배포 보호(Vercel Authentication) 해제. `/api/v1/health` → db up, pgvector available, 활성 문서 19,417. 배포 주소 대상 브라우저 점검(320·390·768·1280px: 입력 오류 → 질문 → 근거 답변 → 출처 → 범위 밖 → 삭제) 통과. 점검 상담은 화면의 삭제 기능으로 지워 상담 표가 비어 있다(익명 세션 4건은 만료 후 정리 대상). 사용자 도메인 https://twinvet.pawcheck.ai 연결(whois CNAME → Vercel, Let's Encrypt 인증서 자동 발급, http→https 308), 같은 점검(390·1280px) 통과.

### 기존 개발 작업 검토 (2026-11-10)

> 2026-11-10 시점의 검토 기록이다. 이후 되묻기 개선·생년월일 입력·Neon 이전·Vercel 배포가 반영됐고, 2026-11-11 기준 자동 테스트는 130개 통과(로컬 DB 포함)다.

현재 feat/poc-rag-api-ui의 커밋과 코드에서 확인:
- 3e47479: PostgreSQL/pgvector 스키마, 마이그레이션, 재개 가능한 적재·검색 기반.
- a91aa97: 정제본 띄어쓰기 복원과 임베딩 머리말.
- b42d0e9: 분류→검색→생성→근거 검증, DB answer_run 작업 큐, API 및 가짜 모델 기반 API 테스트.
- 7805324: React S01~S03와 Chrome 브라우저 점검 스크립트.
- 12afd2a: 평가 스크립트, 만료 세션 정리, 단일 Docker 이미지, 실행 문서.

배포는 로컬 Compose DB를 기준으로 준비됐고 Neon 전용 연결 설정은 없었다. 테스트 fixture는 설정된 DB 서버에서 twinvet_test를 삭제·재생성하므로 원격 DB 차단이 필요했다. 기존 73개 통과·실제 모델 평가 결과는 개발자 보고이며 이번 검토의 재실행 결과와 구분한다.

검증 결과(이번 Neon 설정 반영): 무료 단위/회귀 테스트 72 passed, 28 skipped. DB/API 통합 테스트는 로컬 DB 연결을 켜지 않아 제외했다. URL 인코딩 비밀번호의 Alembic SQL 생성과 원격 테스트 DB 접속 차단 회귀 테스트 2개 포함. Neon Compose 설정은 가짜 환경변수로 docker compose config --quiet 통과, git diff --check 통과. 실제 클라우드 연결/배포 성공을 뜻하지 않는다.

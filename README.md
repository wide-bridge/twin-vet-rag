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

## 실행
```powershell
uvicorn backend.app.main:app --reload --port 8000     # API (/api/v1/health)
cd frontend; npm ci; npm run dev                      # http://localhost:5173, /api는 8000으로 프록시
```

## 데이터 전처리 (로컬, 유료 API 미사용)
```powershell
python -m scripts.prepare_data --version v1
```
`AIHUB_DATA_DIR\59.반려견 성장 및 질병 관련 말뭉치 데이터`를 읽어 `DATA_OUTPUT_DIR/prepared/v1/`에
qa_cases / reference_docs / reference_chunks / eval_dev / eval_final / excluded / report 를 만든다.
원본은 수정하지 않는다. 153번 데이터는 사용하지 않는다.

## DB 마이그레이션·지식 적재
```powershell
alembic upgrade head                                            # 스키마 생성·갱신
python -m scripts.load_knowledge --version v1 --embedder fake    # 비용 없음, 의미 없는 시험용 벡터
python -m scripts.load_knowledge --version v1 --embedder openai --max-usd 1.0   # 유료: 예상 비용 표시 후 확인
python -m scripts.load_knowledge --deactivate <source_record_id>                 # 즉시 검색 제외
```
처리 단계·보장은 docs/03 7절 참고.

## 검사·빌드·테스트
```powershell
python -m pytest -q                                   # 단위 테스트
$env:TWINVET_DB_TESTS="1"; python -m pytest -q        # DB·pgvector 포함 (docker compose up -d db 필요)
cd frontend; npm run typecheck; npm run build
```

## 아직 없는 것
RAG 답변 생성·API·화면·배포 절차는 다음 단계에서 추가한다.

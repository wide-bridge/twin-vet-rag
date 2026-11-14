"""Local M03 demo server for tests/e2e/m03_smoke.py: LOCAL dev DB only, M03 on, vet host vet.localhost, FAKE LLM and
embedder (no API cost). Creates one test vet and one operator and writes their keys to data_output/e2e/ (git-ignored).

    python tests/e2e/m03_server.py      # then, in another shell: python tests/e2e/m03_smoke.py
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import uvicorn
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.llm import FakeLLM
from backend.app.services import m03

s = Settings(m03_enabled=True, vet_host="vet.localhost", cookie_secure=False, retrieval_min_score=-1.0, app_env="development")
assert "127.0.0.1" in s.database_url or "localhost" in s.database_url
llm = FakeLLM({"classify": {"scope": "in_scope", "search_query": "말티즈 구토", "question_age_text": "", "question_unclear": False,
                            "clarification_question": ""},
               "answer": {"response_type": "grounded", "answer": "하루 두 번 구토라면 탈수 여부(잇몸, 피부 탄력)를 먼저 확인하세요.", "used_sources": [1]},
               "care_summary": {"topic": "digestive", "topics": ["digestive"], "summary": "구토", "vet_advised": False}})
app = create_app(s, llm=llm, embedder=FakeEmbedder())
with app.state.Session() as db:
    from datetime import datetime
    name = "김시험" + datetime.now().strftime("%H%M%S")  # unique per run: earlier runs leave their own test vets
    v, key = m03.create_vet(db, name, "시험동물병원", "내과 진료 10년", is_test=True)
OUT = ROOT / "data_output" / "e2e"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "m03_vet_key.txt").write_text(key, encoding="utf-8")
(OUT / "m03_vet_name.txt").write_text(name, encoding="utf-8")
from backend.app.services import review as review_svc  # noqa: E402  operator for the HITL import smoke
with app.state.Session() as db:
    _, op_key = review_svc.create_reviewer(db, "operator", "운영자" + name[-6:])
(OUT / "m03_operator_key.txt").write_text(op_key, encoding="utf-8")
uvicorn.run(app, host="127.0.0.1", port=8011, log_level="warning")

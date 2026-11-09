"""Loader + retrieval on a throwaway DB with SYNTHETIC documents and fake (non-semantic) vectors."""
import uuid

from sqlalchemy import select, text

from backend.app.db.models import Chunk, Document, DocumentVersion
from backend.app.domain.dog import LifeStage
from backend.app.ingest import load
from backend.app.ingest.load import DocInput
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.retrieval import search

E = FakeEmbedder()


def qa(rid: str, content: str, stages: list[str], h: str | None = None) -> DocInput:
    return DocInput(rid, "qa_case", f"합성 사례 {rid}", "내과", [], stages, h or f"hash-{rid}-{content}", f"synthetic/{rid}.json", {},
                    [{"ordinal": 0, "content": content, "content_hash": "c", "tokens": 10, "locator": None}])


def ref(rid: str, parts: list[str]) -> DocInput:
    return DocInput(rid, "reference", f"합성 교재 {rid}", "내과", [], [], f"hash-{rid}", f"synthetic/{rid}.json", {},
                    [{"ordinal": i, "content": p, "content_hash": f"c{i}", "tokens": 10, "locator": {"char_start": 0, "char_end": 1}} for i, p in enumerate(parts)])


def run_all(db, docs, prepared="v1"):
    run = load.start_run(db, "synthetic", prepared, E.model)
    stage = load.stage(db, run, docs)
    load.embed_pending(db, run, E)
    report = load.validate(db, run, E.model, sample=5)
    assert report["ok"], report
    load.activate(db, run)
    return stage


def q(content):
    return E.embed([content])[0]


def test_load_activate_and_search_with_life_stage_filter(db):
    run_all(db, [qa("p1", "강아지 예방접종 일정", ["puppy"]), qa("s1", "노령견 관절 관리", ["senior"]), ref("r1", ["공통 교재 문단 A", "공통 교재 문단 B"])])
    hits = search(db, q("노령견 관절 관리"), model=E.model, life_stage=LifeStage.senior, top_k=10)
    titles = {h.title for h in hits}
    assert hits[0].content == "노령견 관절 관리" and hits[0].score > 0.999  # identical text -> identical fake vector
    assert "합성 사례 p1" not in titles  # puppy-only case filtered out for a senior dog
    assert "합성 교재 r1" in titles  # unlabelled reference is always eligible
    assert all(h.index_version == "synthetic/v1+fake-hash-embedding" for h in hits)


def test_unchanged_reload_is_skipped_and_changed_doc_switches_version(db):
    run_all(db, [qa("a", "원래 내용", ["adult"])])
    assert run_all(db, [qa("a", "원래 내용", ["adult"])])["unchanged"] == 1
    stage = run_all(db, [qa("a", "바뀐 내용", ["adult"])], prepared="v2")
    assert stage["changed"] == 1
    versions = db.execute(select(DocumentVersion.version, DocumentVersion.status).order_by(DocumentVersion.version)).all()
    assert [(v.version, v.status) for v in versions] == [(1, "inactive"), (2, "active")]
    hits = search(db, q("원래 내용"), model=E.model, life_stage=None, top_k=5)
    assert [h.content for h in hits] == ["바뀐 내용"]  # old version no longer searchable


def test_failed_run_keeps_active_index_and_resumes_without_re_embedding(db):
    run_all(db, [qa("a", "활성 내용", ["adult"])])
    run = load.start_run(db, "synthetic", "v2", E.model)
    load.stage(db, run, [qa("a", "새 내용", ["adult"]), qa("b", "새 문서", ["adult"])])
    load.fail_run(db, run, "simulated_crash")  # e.g. API outage before activation
    hits = search(db, q("활성 내용"), model=E.model, life_stage=None, top_k=5)
    assert [h.content for h in hits] == ["활성 내용"]  # untouched; doc b never became searchable

    run2 = load.start_run(db, "synthetic", "v2", E.model)
    assert load.stage(db, run2, [qa("a", "새 내용", ["adult"]), qa("b", "새 문서", ["adult"])])["reused_staging"] == 2
    load.embed_pending(db, run2, E)
    assert load.validate(db, run2, E.model)["ok"]
    load.activate(db, run2)
    assert {h.content for h in search(db, q("새 내용"), model=E.model, life_stage=None, top_k=5)} == {"새 내용", "새 문서"}


def test_deactivate_and_delete(db):
    run_all(db, [qa("a", "비활성화 대상", ["adult"]), qa("b", "남는 문서", ["adult"])])
    load.set_document_status(db, "synthetic", ["a"], "inactive")
    assert [h.content for h in search(db, q("비활성화 대상"), model=E.model, life_stage=None, top_k=5)] == ["남는 문서"]
    # reload must not resurrect an operator-deactivated document
    run_all(db, [qa("a", "비활성화 대상", ["adult"])])
    assert db.execute(select(Document.status).where(Document.source_record_id == "a")).scalar_one() == "inactive"

    load.set_document_status(db, "synthetic", ["b"], "deleted")
    assert db.execute(text("SELECT count(*) FROM chunk c JOIN document_version v ON v.id=c.document_version_id "
                           "JOIN document d ON d.id=v.document_id WHERE d.source_record_id='b'")).scalar_one() == 0
    assert search(db, q("남는 문서"), model=E.model, life_stage=None, top_k=5) == []


def test_vectors_of_another_model_are_not_mixed(db):
    run_all(db, [qa("a", "모델 분리", ["adult"])])
    assert search(db, q("모델 분리"), model="text-embedding-3-small", life_stage=None) == []


def test_validate_flags_missing_embeddings(db):
    run = load.start_run(db, "synthetic", "v1", E.model)
    load.stage(db, run, [qa("a", "임베딩 안 함", ["adult"])])
    report = load.validate(db, run, E.model)
    assert not report["ok"] and report["missing_embeddings"] == 1
    assert db.execute(select(Chunk.embedding).limit(1)).scalar_one() is None
    assert uuid.UUID(str(run.id))


class _OtherModel(FakeEmbedder):
    model = "other-model"


def test_embedding_model_switch_reindexes_unchanged_content(db):
    run_all(db, [qa("a", "모델 전환", ["adult"])])
    other = _OtherModel()
    run = load.start_run(db, "synthetic", "v1", other.model)
    assert load.stage(db, run, [qa("a", "모델 전환", ["adult"])])["changed"] == 1
    load.embed_pending(db, run, other)
    assert load.validate(db, run, other.model)["ok"]
    load.activate(db, run)
    assert [h.content for h in search(db, q("모델 전환"), model=other.model, life_stage=None)] == ["모델 전환"]
    assert search(db, q("모델 전환"), model=E.model, life_stage=None) == []  # old-model index retired

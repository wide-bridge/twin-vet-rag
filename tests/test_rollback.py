"""Index rollback (docs/03 §4 되돌리기 규칙): all-or-nothing return to the recorded previous versions, the shared
dataset lock, and the content-only guard for the source text cleaning path. Local test DB only, fake vectors."""
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.db.models import Chunk, Document, DocumentVersion, IngestionRun
from backend.app.ingest import load
from backend.app.ingest.load import DocInput
from backend.app.rag.embeddings import FakeEmbedder
from backend.app.rag.retrieval import search

E = FakeEmbedder()
DS = "synthetic"


def qa(rid: str, content: str, title: str | None = None, stages=("adult",)) -> DocInput:
    return DocInput(rid, "qa_case", title or f"합성 사례 {rid}", "내과", [], list(stages), f"hash-{rid}-{content}",
                    f"synthetic/{rid}.json", {},
                    [{"ordinal": 0, "content": content, "content_hash": f"c-{content}", "tokens": 10, "locator": None}])


def load_run(db, docs, prepared="v1", content_only=False) -> IngestionRun:
    run = load.start_run(db, DS, prepared, E.model)
    load.stage(db, run, docs, content_only=content_only)
    load.embed_pending(db, run, E)
    assert load.validate(db, run, E.model, sample=3)["ok"]
    load.activate(db, run)
    return run


def contents(db, query: str) -> list[str]:
    return [h.content for h in search(db, E.embed([load.embedding_text("qa_case", "", "내과", [], query)])[0],
                                      model=E.model, life_stage=None, top_k=50)]


def snapshot(db) -> tuple:
    db.expire_all()
    docs = db.execute(select(Document.id, Document.active_version_id, Document.status).order_by(Document.id)).all()
    vers = db.execute(select(DocumentVersion.id, DocumentVersion.status).order_by(DocumentVersion.id)).all()
    nchunks = db.execute(text("SELECT count(*) FROM chunk")).scalar_one()
    return [tuple(r) for r in docs], [tuple(r) for r in vers], nchunks


def test_activate_records_previous_versions_and_keeps_other_counts(db):
    r1 = load_run(db, [qa("a", "원래 A")])
    r2 = load_run(db, [qa("a", "바뀐 A"), qa("n", "새 문서")], prepared="v2")
    db.refresh(r2)
    assert {"stage", "embed", "validate", "activate"} <= set(r2.counts)
    pv = {e["document_id"]: e for e in r2.counts["activate"]["previous_versions"]}
    a = db.execute(select(Document).where(Document.source_record_id == "a")).scalar_one()
    n = db.execute(select(Document).where(Document.source_record_id == "n")).scalar_one()
    first_a = db.execute(select(DocumentVersion.id).where(DocumentVersion.ingestion_run_id == r1.id)).scalar_one()
    assert pv[str(a.id)]["previous_version_id"] == str(first_a) and pv[str(n.id)]["previous_version_id"] is None


def test_rollback_restores_a_content_only_run(db):
    load_run(db, [qa("a", "원래 A"), qa("b", "원래 B")])
    r2 = load_run(db, [qa("a", "바뀐 A"), qa("b", "원래 B")], prepared="v2", content_only=True)
    assert "바뀐 A" in contents(db, "바뀐 A")
    out = load.rollback(db, r2.id)
    assert out == {"ok": True, "run_id": str(r2.id), "restored": 1, "unlinked_new": 0}
    found = contents(db, "원래 A")
    assert "원래 A" in found and "바뀐 A" not in found and "원래 B" in found
    # a second rollback of the same run is refused and changes nothing
    before = snapshot(db)
    again = load.rollback(db, r2.id)
    assert not again["ok"] and again["conflicts"][0]["reason"] == "already_rolled_back"
    assert snapshot(db) == before


def test_one_conflict_refuses_everything(db):
    """10 documents changed by a run; a later run changes 1 of them -> the other 9 must not move either."""
    load_run(db, [qa(f"d{i}", f"원래 {i}") for i in range(10)])
    r2 = load_run(db, [qa(f"d{i}", f"바뀐 {i}") for i in range(10)], prepared="v2", content_only=True)
    load_run(db, [qa("d3", "또 바뀐 3")], prepared="v3")
    before = snapshot(db)
    out = load.rollback(db, r2.id)
    assert not out["ok"] and [c["reason"] for c in out["conflicts"]] == ["changed_by_later_run"]
    assert out["conflicts"][0]["source_record_id"] == "d3"
    assert snapshot(db) == before
    assert "바뀐 5" in contents(db, "바뀐 5")  # untouched documents still on the run's version


@pytest.mark.parametrize("damage, reason", [
    ("delete_doc", "document_deleted"),
    ("drop_prev_chunks", "previous_version_has_no_chunks"),
    ("prev_other_model", "previous_embedding_model_differs"),
    ("no_record", "no_previous_version_record"),
])
def test_refusal_reasons_change_nothing(db, damage, reason):
    r1 = load_run(db, [qa("a", "원래 A"), qa("b", "원래 B")])
    r2 = load_run(db, [qa("a", "바뀐 A"), qa("b", "바뀐 B")], prepared="v2", content_only=True)
    prev_a = db.execute(select(DocumentVersion.id).join(Document, Document.id == DocumentVersion.document_id)
                        .where(DocumentVersion.ingestion_run_id == r1.id, Document.source_record_id == "a")).scalar_one()
    if damage == "delete_doc":
        load.set_document_status(db, DS, ["a"], "deleted")
    elif damage == "drop_prev_chunks":
        db.execute(text("DELETE FROM chunk WHERE document_version_id = :v"), {"v": prev_a})
        db.commit()
    elif damage == "prev_other_model":
        db.execute(text("UPDATE chunk SET embedding_model = 'other-model' WHERE document_version_id = :v"), {"v": prev_a})
        db.commit()
    else:  # a run activated before this feature: no record
        db.refresh(r2)
        r2.counts = {k: v for k, v in r2.counts.items() if k != "activate"} | {"activate": {"versions": 2}}
        db.commit()
    before = snapshot(db)
    out = load.rollback(db, r2.id)
    assert not out["ok"] and reason in [c["reason"] for c in out["conflicts"]]
    assert snapshot(db) == before


def test_shared_lock_blocks_status_change_during_rollback_window(db, test_db_url):
    """activate / rollback / status changes take the same dataset lock."""
    load_run(db, [qa("a", "원래 A")])
    load.dataset_lock(db, DS)  # held until this transaction ends
    other = Session(bind=db.get_bind())
    try:
        other.execute(text("SET lock_timeout = '300ms'"))
        with pytest.raises(OperationalError):
            load.set_document_status(other, DS, ["a"], "inactive")
        other.rollback()
    finally:
        other.close()
    db.rollback()  # release; now it goes through
    assert load.set_document_status(db, DS, ["a"], "inactive") == 1


def test_content_only_guard_applies_to_the_cleaning_path_only(db):
    load_run(db, [qa("a", "원래 A 불필요한 반복 문장"), qa("b", "원래 B")])
    n_versions = db.execute(text("SELECT count(*) FROM document_version")).scalar_one()
    # cleaning path: a title change or a new document is refused before anything is written
    for docs, why in (([qa("a", "원래 A", title="바뀐 제목")], "metadata_changed"),
                      ([qa("a", "원래 A", stages=("senior",))], "metadata_changed"),
                      ([qa("z", "새 문서")], "new_document")):
        run = load.start_run(db, DS, "v2", E.model)
        with pytest.raises(load.ContentOnlyViolation) as ei:
            load.stage(db, run, docs, content_only=True)
        assert ei.value.problems[0]["reason"] == why
        load.fail_run(db, run, "content_only_violation")
    assert db.execute(text("SELECT count(*) FROM document_version")).scalar_one() == n_versions
    # body-only cleaning goes through, and can be rolled back
    r = load_run(db, [qa("a", "원래 A")], prepared="v2", content_only=True)
    assert "원래 A" in contents(db, "원래 A")
    assert load.rollback(db, r.id)["ok"]
    # an ordinary load (not the cleaning path) may still change titles as before
    load_run(db, [qa("b", "원래 B 개정", title="정식으로 바꾼 제목")], prepared="v3")  # new content + title
    assert db.execute(select(Document.title).where(Document.source_record_id == "b")).scalar_one() == "정식으로 바꾼 제목"


def test_rollback_unknown_run(db):
    assert load.rollback(db, uuid.uuid4())["conflicts"][0]["reason"] == "run_not_found"


def test_ordinary_run_cannot_be_rolled_back(db):
    """Codex P1: an ordinary load changes title / life stages at stage time; restoring only the version would mix
    old text with new metadata, so the whole rollback is refused and nothing changes."""
    load_run(db, [qa("a", "원래 A")])
    r2 = load_run(db, [qa("a", "바뀐 A", title="바뀐 제목", stages=("senior",)), qa("n", "새 문서")], prepared="v2")
    before = snapshot(db)
    out = load.rollback(db, r2.id)
    assert not out["ok"] and "not_content_only_run" in [c["reason"] for c in out["conflicts"]]
    assert snapshot(db) == before
    a = db.execute(select(Document).where(Document.source_record_id == "a")).scalar_one()
    assert a.title == "바뀐 제목" and "바뀐 A" in contents(db, "바뀐 A")  # left exactly as the run made it

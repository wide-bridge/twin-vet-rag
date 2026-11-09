"""Data-prep tests on SYNTHETIC fixtures (not real AI Hub records)."""
import json
from pathlib import Path

from backend.app.ingest.prepare import EXTRACTED_SUBPATH, chunk_paragraphs, mask_pii, normalize_text, prepare


def char_tokens(text: str) -> int:  # deterministic stand-in for tiktoken in unit tests
    return len(text)


def _qa(root: Path, split: str, dept: str, rid: str, q: str, a: str, life: str = "성견", disease: str = "기타"):
    folder = root / EXTRACTED_SUBPATH / split / "02.라벨링데이터" / f"X_질의응답데이터_{dept}"
    folder.mkdir(parents=True, exist_ok=True)
    body = {"meta": {"lifeCycle": life, "department": dept, "disease": disease}, "qa": {"instruction": "너는 수의사야.", "input": q, "output": a}}
    (folder / f"{rid}.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _ref(root: Path, split: str, rid: str, title: str, body: str):
    folder = root / EXTRACTED_SUBPATH / split / "01.원천데이터" / "X_말뭉치데이터_내과"
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"title": title, "author": "합성", "publisher": "합성", "department": "내과", "disease": body}
    (folder / f"{rid}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_normalize_only_unescapes_when_asked():
    assert normalize_text("a\\nb") == "a\\nb"
    assert normalize_text("a\\nb", unescape_literal_newlines=True) == "a\nb"
    assert normalize_text("  x \t y \n\n\n\n z ") == "x y\n\nz"


def test_mask_pii():
    text, n = mask_pii("연락처 010-1234-5678, 메일 a.b@test.com")
    assert n == 2 and "010" not in text and "@" not in text


def test_chunk_paragraphs_overlap_and_locator():
    body = "\n".join(f"문단{i} " + "가" * 40 for i in range(10))
    chunks = chunk_paragraphs(body, char_tokens, target_tokens=100, max_tokens=150, overlap_paragraphs=1)
    assert len(chunks) > 1
    for c in chunks:
        assert body[c.start : c.end].strip() == c.text
        assert c.tokens <= 150
    # consecutive chunks share one paragraph
    assert chunks[0].text.split("\n")[-1] == chunks[1].text.split("\n")[0]


def test_long_paragraph_is_split():
    body = "다. ".join(["가" * 30] * 20)
    chunks = chunk_paragraphs(body, char_tokens, target_tokens=100, max_tokens=150)
    assert len(chunks) > 1 and all(c.tokens <= 150 for c in chunks)


def test_prepare_end_to_end(tmp_path):
    root = tmp_path / "ds"
    _qa(root, "Training", "내과", "t1", "구토를 해요", "탈수를 확인하세요", life="노령견", disease="중독")
    _qa(root, "Training", "내과", "t2", "구토를 해요!", "탈수를 확인하세요.")  # exact dup after punctuation removal
    _qa(root, "Training", "외과", "t3", "다리를 절어요 010-1111-2222", "슬개골을 확인하세요")
    _qa(root, "Validation", "내과", "v1", "구토를 해요", "다른 답")  # question leaks from train
    for i in range(8):
        _qa(root, "Validation", "외과" if i % 2 else "내과", f"v{i+2}", f"검증 질문 {i}", f"검증 답 {i}")
    _ref(root, "Training", "r1", "합성 교재", "1장\\n첫 문단입니다.\\n둘째 문단입니다.")
    _ref(root, "Validation", "r2", "합성 교재", "검증 분할 문서")

    out = tmp_path / "out"
    res = prepare(root, out, count_tokens=char_tokens, dev_ratio=0.25)
    rows = [json.loads(line) for line in (out / "qa_cases.jsonl").read_text(encoding="utf-8").splitlines()]

    assert {r["source_record_id"] for r in rows} == {"t1", "t3"}
    t1 = next(r for r in rows if r["source_record_id"] == "t1")
    assert t1["life_stages"] == ["senior"] and t1["disease_names"] == ["중독"]
    assert "너는 수의사야" not in t1["content"]  # instruction excluded
    t3 = next(r for r in rows if r["source_record_id"] == "t3")
    assert "010" not in t3["content"]
    assert all(not Path(r["source_relative_path"]).is_absolute() for r in rows)

    reasons = res.report["excluded_by_reason"]
    assert reasons["duplicate_in_train"] == 1 and reasons["leak_duplicate_of_train"] == 1

    dev = (out / "eval_dev.jsonl").read_text(encoding="utf-8").splitlines()
    final = (out / "eval_final.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(dev) + len(final) == 8 and len(dev) == 2
    eval_ids = {json.loads(x)["source_record_id"] for x in dev + final}
    assert not eval_ids & {r["source_record_id"] for r in rows}  # eval never indexed

    chunks = [json.loads(x) for x in (out / "reference_chunks.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {c["source_record_id"] for c in chunks} == {"r1"}  # validation reference not indexed
    assert "\\n" not in chunks[0]["content"] and "\n" in chunks[0]["content"]
    assert res.report["reference_valid_not_indexed"]["files"] == 1

    # deterministic split
    res2 = prepare(root, tmp_path / "out2", count_tokens=char_tokens, dev_ratio=0.25)
    assert (tmp_path / "out2" / "eval_dev.jsonl").read_text(encoding="utf-8") == "\n".join(dev) + "\n"
    assert res2.report["outputs"] == res.report["outputs"]


def test_spacing_fixed_in_index_copy_only(tmp_path):
    root = tmp_path / "ds"
    _qa(root, "Training", "내과", "t1", "체중이 증가 하였어요", "효과 를 확인하세요")
    for i in range(4):
        _qa(root, "Validation", "내과", f"v{i}", f"체중이 증가 하였어요 {i}", "답")
    _ref(root, "Training", "r1", "합성 교재", "당뇨가 발생 하게 된다.")
    raw = sorted((root / EXTRACTED_SUBPATH).rglob("*.json"))
    raw_before = [p.read_bytes() for p in raw]

    out = tmp_path / "out"
    res = prepare(root, out, count_tokens=char_tokens, dev_ratio=0.5)

    qa = json.loads((out / "qa_cases.jsonl").read_text(encoding="utf-8"))
    assert "증가하였어요" in qa["content"] and "효과를" in qa["content"]
    assert qa["metadata"]["spacing_fixes"] == 2
    chunk = json.loads((out / "reference_chunks.jsonl").read_text(encoding="utf-8"))
    assert chunk["content"] == "당뇨가 발생하게 된다."
    evals = [json.loads(x) for f in ("eval_dev.jsonl", "eval_final.jsonl") for x in (out / f).read_text(encoding="utf-8").splitlines()]
    assert all("증가 하였어요" in e["question"] for e in evals)  # user-typed text kept as is
    log = [json.loads(x) for x in (out / "spacing_changes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == res.report["spacing_fixes"]["changes"] == 3
    assert [p.read_bytes() for p in raw] == raw_before  # raw files untouched


def test_embedding_text_header():
    from backend.app.ingest.load import embedding_text
    assert embedding_text("reference", "동물행동학", "외과", [], "본문") == "자료명: 동물행동학 · 진료과: 외과\n본문"
    assert embedding_text("qa_case", "내과 상담 사례 · 중독", "내과", ["중독"], "보호자 질문: …") == "진료과: 내과 · 질병: 중독\n보호자 질문: …"
    assert embedding_text("qa_case", "내과 상담 사례", "내과", [], "Q") == "진료과: 내과\nQ"

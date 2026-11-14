"""Browser check of M03 -> HITL on the local demo server (tests/e2e/m03_server.py; local DB, fake LLM, no cost):
a guardian sends a question WITH quality-use consent -> the vet replies -> the operator sees numbers-only stats and the
import queue in hitl-review (/review), cleans the text and imports it -> the vet corrects -> the copy shows
'재확인 필요' -> the guardian withdraws consent -> the copy is gone. Screenshots in data_output/e2e/m03_hitl_*.png."""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
SP = Path(__file__).resolve().parents[2] / "data_output" / "e2e"
G = "http://localhost:8011"
VH = "http://vet.localhost:8011"
VET_KEY = (SP / "m03_vet_key.txt").read_text(encoding="utf-8").strip()
OP_KEY = (SP / "m03_operator_key.txt").read_text(encoding="utf-8").strip()
NAME = (SP / "m03_vet_name.txt").read_text(encoding="utf-8").strip()
SECRET = "010-9876-5432"


def tab(p, name):
    p.get_by_role("button", name=name, exact=True).locator("visible=true").first.click()


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--host-resolver-rules=MAP vet.localhost 127.0.0.1"])
    # guardian with a dog and one answer
    a = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    a.goto(G + "/?frame")
    a.get_by_role("button", name="상담 시작").click()
    a.get_by_placeholder("반려견에 궁금한 것을 적어주세요").fill("사료를 바꾼 뒤 설사를 해요")
    a.get_by_placeholder("예: 두부").fill("보리")
    a.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("푸들")
    a.get_by_role("button", name="정확히 몰라요").click()
    a.get_by_label("대략적인 나이 년").fill("5")
    a.get_by_role("button", name="보내기").click()
    expect(a.get_by_role("article", name="AI 답변")).to_be_visible(timeout=30_000)
    tab(a, "설정")
    a.locator(".vet-row", has_text=NAME).get_by_role("button", name="연결 요청").click()

    v = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    v.goto(VH + "/")
    v.get_by_label("접근 키").fill(VET_KEY)
    v.get_by_role("button", name="로그인").click()
    v.locator(".rv-row", has_text="요청").first.get_by_role("button", name="수락").click()
    expect(v.get_by_text("연결된 보호자")).to_be_visible()

    a.reload()
    tab(a, "상담")
    a.get_by_role("button", name="수의사에게 질문 보내기").first.click()
    dlg = a.get_by_role("dialog")
    dlg.get_by_label("수의사에게 덧붙일 설명 (선택)").fill(f"연락은 {SECRET}로 주세요")
    dlg.get_by_label("서비스 품질 개선에 활용하는 데 동의해요 (선택, 언제든 철회)").check()
    dlg.get_by_role("button", name="보내기").click()
    a.get_by_role("button", name="확인").click()

    v.reload()
    v.locator(".rv-list button.rv-row").first.click()
    v.get_by_label("회신").fill("사료를 천천히 바꾸고 이틀 넘게 계속되면 진료받으세요.")
    v.get_by_role("button", name="회신 보내기").click()
    expect(v.get_by_text("회신을 보냈습니다.")).to_be_visible()

    # operator in hitl-review: stats (numbers only) -> queue -> clean -> import
    op = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    op.goto(G + "/review")
    op.get_by_label("접근 키").fill(OP_KEY)
    op.get_by_role("button", name="로그인").click()
    op.get_by_role("link", name="운영자 현황").click()
    expect(op.get_by_role("heading", name="운영자 현황")).to_be_visible()
    stats_text = op.locator(".rv-stats").inner_text()
    assert "설사" not in stats_text and SECRET not in stats_text
    op.screenshot(path=str(SP / "m03_hitl_1_stats.png"), full_page=True)
    op.locator(".rv-list a.rv-row", has_text="사료를 바꾼 뒤").click()
    expect(op.get_by_role("heading", name="검토 묶음으로 이관")).to_be_visible()
    note = op.get_by_label("보호자가 덧붙인 설명 (비우면 넘기지 않음)")
    assert SECRET in note.input_value()
    note.fill("연락처 정리함")
    expect(op.get_by_role("button", name="검토 묶음으로 이관")).to_be_disabled()  # must confirm reading first
    op.get_by_label("본문을 모두 읽고 식별 정보를 정리했습니다").check()
    op.screenshot(path=str(SP / "m03_hitl_2_import.png"), full_page=True)
    op.get_by_role("button", name="검토 묶음으로 이관").click()
    op.get_by_role("link", name="검토 묶음 열기").click()
    expect(op.get_by_text("수의사 회신")).to_be_visible()
    body = op.locator("main").inner_text()
    assert SECRET not in body and "보리" not in body and "사료를 천천히 바꾸고" in body

    # vet corrects -> copy flagged, recorded reply unchanged
    item_url = op.url
    v.get_by_role("button", name="정정하기").click()
    v.get_by_label("정정 내용").fill("사료를 일주일에 걸쳐 바꾸고, 혈변이 보이면 바로 진료받으세요.")
    v.get_by_role("button", name="정정 보내기").click()
    expect(v.get_by_text("정정됨")).to_be_visible()
    op.reload()
    expect(op.get_by_text("재확인 필요").first).to_be_visible()
    assert "사료를 천천히 바꾸고" in op.locator("main").inner_text()
    op.screenshot(path=str(SP / "m03_hitl_3_recheck.png"), full_page=True)

    # guardian withdraws consent -> the copy disappears from HITL
    a.reload()
    tab(a, "상담")
    a.locator(".vet-card").get_by_role("button", name="품질 개선 동의 철회").click()
    expect(a.locator(".vet-card").get_by_role("button", name="품질 개선 동의 철회")).to_have_count(0)
    op.goto(item_url)
    op.reload()
    expect(op.get_by_text("찾을 수 없어요").first).to_be_visible(timeout=10_000)
    b.close()
print("m03 hitl smoke ok")

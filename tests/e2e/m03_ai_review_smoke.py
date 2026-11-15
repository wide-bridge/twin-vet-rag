"""Browser check of the vet's AI-answer review ('HITL 우리', docs/06 §20) on the local M03 demo server (fake LLM):
guardian sends a question with quality-use consent -> vet sees the reply caution, sends an AI review without replying
-> the guardian sees no review. Needs tests/e2e/m03_server.py running. Screenshots in data_output/e2e/m03_review_*.png."""
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
SP = Path(__file__).resolve().parents[2] / "data_output" / "e2e"
G = "http://localhost:8011"
VH = "http://vet.localhost:8011"
key = (SP / "m03_vet_key.txt").read_text(encoding="utf-8").strip()
NAME = (SP / "m03_vet_name.txt").read_text(encoding="utf-8").strip()


def start_guardian(b, name, question):
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    p = ctx.new_page()
    p.goto(G + "/?frame")
    p.get_by_role("button", name="상담 시작").click()
    p.get_by_placeholder("반려견에 궁금한 것을 적어주세요").fill(question)
    p.get_by_placeholder("예: 두부").fill(name)
    p.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("시츄")
    p.get_by_role("button", name="정확히 몰라요").click()
    p.get_by_label("대략적인 나이 년").fill("11")
    p.get_by_role("button", name="보내기").click()
    expect(p.get_by_role("article", name="AI 답변")).to_be_visible(timeout=30_000)
    return ctx, p


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--host-resolver-rules=MAP vet.localhost 127.0.0.1"])
    _, a = start_guardian(b, "두부", "아이가 눈곱이 많아졌어요.")
    a.get_by_role("button", name="설정", exact=True).locator("visible=true").first.click()
    a.locator(".vet-row", has_text=NAME).get_by_role("button", name="연결 요청").click()

    vctx = b.new_context(viewport={"width": 1280, "height": 900})
    v = vctx.new_page()
    v.goto(VH + "/")
    v.get_by_label("접근 키").fill(key)
    v.get_by_role("button", name="로그인").click()
    v.locator(".rv-row", has_text="요청").first.get_by_role("button", name="수락").click()
    expect(v.get_by_text("연결된 보호자")).to_be_visible()

    a.reload()
    a.get_by_role("button", name="상담", exact=True).locator("visible=true").first.click()
    a.get_by_role("button", name="수의사에게 질문 보내기").first.click()
    dlg = a.get_by_role("dialog")
    dlg.get_by_label("수의사에게 덧붙일 설명 (선택)").fill("눈곱이 많고 눈물에서 냄새가 납니다.")
    dlg.get_by_label("서비스 품질 개선에 활용하는 데 동의해요 (선택, 언제든 철회)").check()
    dlg.get_by_role("button", name="보내기").click()
    a.get_by_role("button", name="확인").click()

    v.reload()
    v.locator(".rv-list button.rv-row").first.click()
    expect(v.get_by_text("비대면 진료가 되지 않도록 주의해 주세요.")).to_be_visible()
    expect(v.get_by_role("heading", name="AI 답변 검토")).to_be_visible()
    v.screenshot(path=str(SP / "m03_review_1_vet_before.png"), full_page=True)

    v.get_by_role("button", name="보완 필요").click()
    v.get_by_label("안전 문제가 있어요", exact=False).check()
    v.get_by_label("의견 (선택)").fill("노령견이라 각막 질환을 먼저 확인해야 하고, 냄새가 나면 바로 내원 안내가 필요해요.")
    v.get_by_role("button", name="TwinVet에 보내기").click()
    expect(v.get_by_text("TwinVet에 보냄")).to_be_visible()
    expect(v.get_by_text("AI 답변에 안전 문제가 있다고 표시하셨어요")).to_be_visible()
    v.screenshot(path=str(SP / "m03_review_2_vet_after.png"), full_page=True)

    a.reload()
    body = a.locator("body").inner_text()
    assert "각막 질환" not in body and "보완 필요" not in body, "guardian must not see the AI review"
    print("ok: review sent, guardian sees nothing")
    b.close()

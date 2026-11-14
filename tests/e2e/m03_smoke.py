"""Browser round trip on the local M03 demo server (fake LLM): guardian A picks the vet -> vet accepts -> A sends
one chosen question -> vet replies + corrects -> A sees the reply (and via the link); guardian B sees nothing;
guardian host cannot reach the vet API. Needs tests/e2e/m03_server.py running. Screenshots in data_output/e2e/m03_*.png."""
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
SP = Path(__file__).resolve().parents[2] / "data_output" / "e2e"
G = "http://localhost:8011"
VH = "http://vet.localhost:8011"
key = (SP / "m03_vet_key.txt").read_text(encoding="utf-8").strip()
NAME = (SP / "m03_vet_name.txt").read_text(encoding="utf-8").strip()


def hscroll(page, label):
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, f"{label}: horizontal overflow {over}px"


def status(url, host=None):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def start_guardian(b, name, question):
    ctx = b.new_context(viewport={"width": 390, "height": 844})
    p = ctx.new_page()
    p.goto(G + "/?frame")  # phone layout directly
    p.get_by_role("button", name="상담 시작").click()
    p.get_by_placeholder("반려견에 궁금한 것을 적어주세요").fill(question)
    p.get_by_placeholder("예: 두부").fill(name)
    p.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("말티즈")
    p.get_by_role("button", name="정확히 몰라요").click()
    p.get_by_label("대략적인 나이 년").fill("3")
    p.get_by_role("button", name="보내기").click()
    expect(p.get_by_role("article", name="AI 답변")).to_be_visible(timeout=30_000)
    return ctx, p


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--host-resolver-rules=MAP vet.localhost 127.0.0.1"])
    ctx_a, a = start_guardian(b, "두부", "어제부터 하루에 두 번 토했어요. 병원에 가야 할까요?")
    a.get_by_placeholder("반려견에 궁금한 것을 적어주세요").fill("산책은 해도 되나요?")
    a.get_by_role("button", name="보내기").click()
    expect(a.get_by_role("article", name="AI 답변")).to_have_count(2, timeout=30_000)
    expect(a.get_by_role("button", name="수의사에게 질문 보내기")).to_have_count(0)  # no accepted vet yet

    # ① settings: request a link
    a.get_by_role("button", name="설정", exact=True).locator("visible=true").first.click()
    expect(a.get_by_role("heading", name="수의사 연결")).to_be_visible()
    row = a.locator(".vet-row", has_text=NAME)
    row.get_by_role("button", name="연결 요청").click()
    expect(a.locator(".vet-row", has_text=NAME).get_by_text("요청 중")).to_be_visible()
    hscroll(a, "settings")
    a.screenshot(path=str(SP / "m03_1_guardian_request.png"), full_page=True)

    # ② vet: login on the vet host, accept
    vctx = b.new_context(viewport={"width": 1280, "height": 900})
    v = vctx.new_page()
    v.goto(VH + "/")
    v.get_by_label("접근 키").fill(key)
    v.get_by_role("button", name="로그인").click()
    expect(v.get_by_role("heading", name="연결 요청")).to_be_visible()
    req_row = v.locator(".rv-row", has_text="요청").first
    assert "말티즈" not in req_row.inner_text() and "두부" not in req_row.inner_text()  # nothing shared before accepting
    v.screenshot(path=str(SP / "m03_2_vet_request.png"), full_page=True)
    req_row.get_by_role("button", name="수락").click()
    expect(v.get_by_text("연결된 보호자")).to_be_visible()
    expect(v.get_by_text("받은 질문이 없습니다.")).to_be_visible()

    # ③ guardian: send only the first answer's question, with a note, without the name
    a.reload()
    a.get_by_role("button", name="상담", exact=True).locator("visible=true").first.click()
    btns = a.get_by_role("button", name="수의사에게 질문 보내기")
    expect(btns).to_have_count(2)
    btns.first.click()
    dlg = a.get_by_role("dialog")
    expect(dlg.get_by_text("어제부터 하루에 두 번 토했어요")).to_be_visible()
    dlg.get_by_label("수의사에게 덧붙일 설명 (선택)").fill("지금은 물은 마셔요")
    assert not dlg.get_by_label("이름").is_checked() and dlg.get_by_label("품종").is_checked()
    hscroll(a, "share sheet")
    a.screenshot(path=str(SP / "m03_3_share_sheet.png"), full_page=False)
    dlg.get_by_role("button", name="보내기").click()
    expect(a.get_by_role("dialog", name="질문을 보냈어요")).to_be_visible()
    link = a.locator("#vet-link-url").input_value()
    assert "/q/" in link
    a.get_by_role("button", name="확인").click()
    expect(a.get_by_text("주치수의사에게 질문이 전달됐어요")).to_be_visible()
    expect(btns).to_have_count(1)  # the second answer can still be sent; the first is done

    # ④ vet: sees that one question (not the walk question), replies, then corrects
    v.reload()
    items = v.locator(".rv-list button.rv-row")
    expect(items).to_have_count(1)
    assert "산책" not in v.locator("main").inner_text()
    items.first.click()
    expect(v.get_by_text("지금은 물은 마셔요")).to_be_visible()
    body = v.locator("main").inner_text()
    assert "두부" not in body and "말티즈" in body and "산책" not in body
    v.get_by_label("회신").fill("탈수 확인 후 오늘 중 진료를 권합니다.")
    v.get_by_role("button", name="회신 보내기").click()
    expect(v.get_by_text("회신을 보냈습니다.")).to_be_visible()
    v.get_by_role("button", name="정정하기").click()
    v.get_by_label("정정 내용").fill("탈수가 보이면 바로 진료받으세요. 없으면 오늘 중 진료를 권합니다.")
    v.get_by_role("button", name="정정 보내기").click()
    expect(v.get_by_text("정정됨")).to_be_visible()
    hscroll(v, "vet detail")
    v.screenshot(path=str(SP / "m03_4_vet_reply.png"), full_page=True)

    # ⑤ guardian: reply under the original answer, marked corrected, history kept
    a.reload()
    a.get_by_role("button", name="상담", exact=True).locator("visible=true").first.click()
    card = a.locator(".vet-card")
    expect(card.get_by_text("답변 완료")).to_be_visible()
    expect(card.get_by_text("탈수가 보이면 바로 진료받으세요")).to_be_visible()
    expect(card.get_by_text("정정됨")).to_be_visible()
    card.get_by_role("button", name="정정 이력 보기").click()
    expect(card.get_by_text("탈수 확인 후 오늘 중 진료를 권합니다.")).to_be_visible()
    hscroll(a, "reply")
    card.screenshot(path=str(SP / "m03_5_guardian_reply.png"))

    # the confirmation link works without the guardian's cookies
    other = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    other.goto(link)
    expect(other.get_by_text("탈수가 보이면 바로 진료받으세요")).to_be_visible()
    other.screenshot(path=str(SP / "m03_6_link_page.png"), full_page=True)

    # guardian B: no links, no questions, nothing of A visible
    ctx_b, gb = start_guardian(b, "콩이", "귀를 자꾸 긁어요")
    expect(gb.get_by_role("button", name="수의사에게 질문 보내기")).to_have_count(0)
    assert "탈수가 보이면" not in gb.locator("body").inner_text()
    gb.get_by_role("button", name="설정", exact=True).locator("visible=true").first.click()
    expect(gb.locator(".vet-row", has_text=NAME).get_by_role("button", name="연결 요청")).to_be_visible()
    b.close()

print("guardian host -> vet API:", status(G + "/api/v1/vet/me"))
print("vet host -> guardian API:", status(G + "/api/v1/vets", "vet.localhost:8011"))
print("vet host -> vet API (no login):", status(G + "/api/v1/vet/me", "vet.localhost:8011"))
print("m03 e2e ok")

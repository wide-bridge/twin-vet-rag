"""Browser check of the 관리자 상담 스위치 on the local demo server (python tests/e2e/m03_server.py --pilot; local DB,
fake LLM, no cost): a normal guardian sees no vet feature and the API refuses them; the operator opens /?pilot=<code>
and runs the whole flow with the test vet; the question shows '시험'. Screenshots in data_output/e2e/m03_pilot_*.png."""
import sys
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
SP = Path(__file__).resolve().parents[2] / "data_output" / "e2e"
G = "http://localhost:8011"
VH = "http://vet.localhost:8011"
VET_KEY = (SP / "m03_vet_key.txt").read_text(encoding="utf-8").strip()
NAME = (SP / "m03_vet_name.txt").read_text(encoding="utf-8").strip()
CODE = "local-pilot-code"


def start(b, name):
    p = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    p.goto(G + "/?frame")
    p.get_by_role("button", name="상담 시작").click()
    p.get_by_placeholder("반려견에 궁금한 것을 적어주세요").fill("어제부터 두 번 토했어요")
    p.get_by_placeholder("예: 두부").fill(name)
    p.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("말티즈")
    p.get_by_role("button", name="정확히 몰라요").click()
    p.get_by_label("대략적인 나이 년").fill("3")
    p.get_by_role("button", name="보내기").click()
    expect(p.get_by_role("article", name="AI 답변")).to_be_visible(timeout=30_000)
    return p


def tab(p, name):
    p.get_by_role("button", name=name, exact=True).locator("visible=true").first.click()


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--host-resolver-rules=MAP vet.localhost 127.0.0.1"])
    # a normal guardian: nothing visible, API closed
    other = start(b, "콩이")
    tab(other, "설정")
    expect(other.get_by_role("heading", name="주치동물병원 (선택)")).to_be_visible()
    assert other.get_by_role("heading", name="수의사 연결 (선택)").count() == 0
    assert other.get_by_role("button", name="수의사에게 질문 보내기").count() == 0
    st = other.evaluate("fetch('/api/v1/vets').then(r => r.status)")
    print("normal guardian /api/v1/vets:", st)
    assert st == 404

    # the operator: pilot link after the dog profile exists
    me = start(b, "두부")
    me.goto(G + f"/?frame&pilot={CODE}")
    me.wait_for_function("!location.search.includes('pilot=')", timeout=10_000)  # code removed from the address bar
    tab(me, "설정")
    expect(me.get_by_text("관리자 시험 중")).to_be_visible()
    me.locator(".vet-row", has_text=NAME).get_by_role("button", name="연결 요청").click()
    me.screenshot(path=str(SP / "m03_pilot_1_settings.png"), full_page=True)

    # user's order: request the vet, then send the question right away (held until the vet accepts)
    expect(me.locator(".vet-row", has_text=NAME).get_by_text("연결 요청함")).to_be_visible()
    tab(me, "상담")
    me.reload()
    me.get_by_role("button", name="수의사에게 질문 보내기").first.click()
    expect(me.get_by_role("dialog").get_by_text("아직 연결을 수락하지 않은 수의사예요")).to_be_visible()
    me.get_by_role("dialog").get_by_role("button", name="보내기").click()
    me.get_by_role("button", name="확인").click()
    card = me.locator(".vet-card")
    expect(card.get_by_text("연결 수락을 기다리고 있어요")).to_be_visible()
    expect(card.get_by_text("시험", exact=True)).to_be_visible()
    card.screenshot(path=str(SP / "m03_pilot_stage_1_held.png"))

    v = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
    v.goto(VH + "/")
    v.get_by_label("접근 키").fill(VET_KEY)
    v.get_by_role("button", name="로그인").click()
    expect(v.get_by_text("받은 질문이 없습니다.")).to_be_visible()  # held: the vet cannot see it yet
    v.locator(".rv-row", has_text="요청").first.get_by_role("button", name="수락").click()
    expect(v.get_by_text("연결된 보호자")).to_be_visible()

    me.reload()
    expect(card.get_by_text("주치수의사에게 질문이 전달됐어요")).to_be_visible()

    v.reload()
    expect(v.locator(".rv-list button.rv-row").first.get_by_text("시험", exact=True)).to_be_visible()
    v.locator(".rv-list button.rv-row").first.click()
    me.reload()
    expect(card.get_by_text("주치수의사가 질문을 확인했어요")).to_be_visible()
    v.get_by_label("회신").fill("시험 회신입니다.")
    v.get_by_role("button", name="초안 저장").click()
    expect(v.get_by_text("초안을 저장했습니다.")).to_be_visible()
    me.reload()
    expect(card.get_by_text("주치수의사가 답변을 준비 중이에요")).to_be_visible()
    v.get_by_role("button", name="회신 보내기").click()
    expect(v.get_by_text("회신을 보냈습니다.")).to_be_visible()

    me.reload()
    expect(card.get_by_text("답변 완료")).to_be_visible()
    expect(card.get_by_text("시험 회신입니다.")).to_be_visible()
    tab(me, "설정")
    expect(me.locator(".vet-row", has_text=NAME).get_by_text("주치수의사", exact=True)).to_be_visible()
    tab(me, "상담")
    me.locator(".vet-card").screenshot(path=str(SP / "m03_pilot_2_reply.png"))
    # still closed for the normal guardian
    other.reload()
    assert other.evaluate("fetch('/api/v1/vet-links').then(r => r.status)") == 404
    b.close()
print("m03 pilot smoke ok")

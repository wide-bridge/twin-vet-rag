"""Browser smoke test of S01-S03 against a running server (real LLM: a few cents of API calls).

    uvicorn backend.app.main:app --port 8000          # after `cd frontend; npm run build`
    python tests/e2e/ui_smoke.py [--base http://localhost:8000] [--out data_output/e2e]

Uses the installed Google Chrome through Playwright (pip install playwright; no browser download).
On PC widths (>= 1024) the page is the introduction + phone frame; the flow runs inside the frame.
Checks: no horizontal overflow at each width, start -> dog form -> question -> grounded answer with
source chips -> source detail -> feedback -> emergency sheet -> 주치동물병원 prompt -> settings (Kakao search,
public-data match, phone) -> care card + AI summary + show-to-hospital -> delete -> saved-profile start;
saves screenshots for review.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = [(320, 640), (390, 844), (768, 1024), (1280, 800)]


def _birth_and_age() -> tuple[tuple[int, int], str]:
    """A birth year/month about 10 years 3 months ago and the age text the screen should show."""
    t = date.today()
    total = t.year * 12 + (t.month - 1) - 123
    y, m = divmod(total, 12)
    return (y, m + 1), "10세 3개월"


BIRTH, AGE_TEXT = _birth_and_age()


def no_hscroll(page, label: str) -> None:
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, f"{label}: horizontal overflow {over}px"


def tab(ui, name: str) -> None:
    ui.get_by_role("button", name=name, exact=True).locator("visible=true").first.click()


def flow(page: Page, ui, base: str, out: Path, tag: str, w: int, framed: bool) -> None:
    """`ui` is the page itself, or on PC the app inside the phone frame (same-origin iframe)."""
    expect(ui.get_by_role("button", name="상담 시작")).to_be_visible()
    no_hscroll(ui, f"{tag} S01")
    page.screenshot(path=out / f"{tag}-s01.png", full_page=True)
    ui.get_by_role("button", name="상담 시작").click()

    # S02 first entry: send disabled until breed + age
    ui.get_by_placeholder("반려견에 대해 궁금한 점을 적어 주세요").fill("어제부터 하루에 두 번 토했어요. 밥은 조금 먹어요. 병원에 가야 할까요?")
    expect(ui.get_by_role("button", name="보내기")).to_be_disabled()
    expect(ui.get_by_text("품종을 입력해 주세요")).to_be_visible()
    no_hscroll(ui, f"{tag} S02 intake")
    page.screenshot(path=out / f"{tag}-s02-intake-errors.png", full_page=False)
    ui.get_by_label("이름 (선택)").fill("두부")
    ui.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("말티즈")
    # age: estimate mode first, then the birth date (year + month, day left as 모름)
    ui.get_by_role("button", name="정확히 몰라요").click()
    ui.get_by_label("대략적인 나이 년").fill("3")
    expect(ui.get_by_text("현재 약 3세")).to_be_visible()
    ui.get_by_role("button", name="생일을 알아요").click()
    expect(ui.get_by_label("태어난 날 (모르면 비워 두세요)")).to_be_disabled()
    ui.get_by_label("태어난 해").select_option(str(BIRTH[0]))
    ui.get_by_label("태어난 달 (모르면 비워 두세요)").select_option(str(BIRTH[1]))
    expect(ui.get_by_label("태어난 날 (모르면 비워 두세요)")).to_be_enabled()
    expect(ui.get_by_text(f"현재 {AGE_TEXT}")).to_be_visible()
    no_hscroll(ui, f"{tag} S02 birth date")
    ui.get_by_role("button", name="건강상태 (선택)").click()
    ui.get_by_placeholder("예: 3.2").fill("3.2")
    page.screenshot(path=out / f"{tag}-s02-intake-filled.png", full_page=False)
    ui.get_by_role("button", name="보내기").click()

    expect(ui.get_by_text("답변을 준비하고 있어요")).to_be_visible(timeout=10_000)
    page.screenshot(path=out / f"{tag}-s02-processing.png", full_page=False)
    expect(ui.get_by_text(f"두부(말티즈) | {AGE_TEXT} | 3.2kg")).to_be_visible()
    expect(ui.get_by_role("article", name="AI 답변")).to_be_visible(timeout=90_000)
    no_hscroll(ui, f"{tag} S02 answer")
    page.screenshot(path=out / f"{tag}-s02-answer.png", full_page=False)

    chips = ui.get_by_role("button", name="근거 1")
    expect(chips.first).to_be_visible()
    chips.first.click()
    expect(ui.get_by_text("원문 구절은 자료 이용 조건 확인 후 제공됩니다.")).to_be_visible(timeout=10_000)
    page.screenshot(path=out / f"{tag}-s03-source.png", full_page=False)
    ui.get_by_role("button", name="닫기").locator("visible=true").first.click()
    expect(ui.get_by_text("원문 구절은 자료 이용 조건 확인 후 제공됩니다.")).to_be_hidden()

    ui.get_by_role("button", name="도움 됐어요").click()
    expect(ui.get_by_text("저장됐어요")).to_be_visible()

    # out-of-scope follow-up renders as its own card type
    ui.get_by_placeholder("반려견에 대해 궁금한 점을 적어 주세요").fill("고양이가 토해요")
    ui.get_by_role("button", name="보내기").click()
    expect(ui.get_by_role("article", name="지원 범위 밖")).to_be_visible(timeout=60_000)
    page.screenshot(path=out / f"{tag}-s02-out-of-scope.png", full_page=False)

    # reload restores the conversation from the server
    ui.goto(ui.url) if framed else page.reload()
    expect(ui.get_by_role("article", name="AI 답변")).to_be_visible(timeout=10_000)

    # emergency sheet without a 주치동물병원: 24h search first, registration only as a link
    ui.get_by_role("button", name="응급하면 바로 동물병원에 연락하세요").click()
    expect(ui.get_by_role("button", name="📍 가까운 24시 동물병원 찾기")).to_be_visible()
    expect(ui.get_by_role("button", name="주치동물병원 등록하기")).to_be_visible()
    no_hscroll(ui, f"{tag} emergency sheet")
    page.screenshot(path=out / f"{tag}-s02-emergency.png", full_page=False)
    ui.get_by_role("button", name="닫기").click()

    # one-time prompt after the first answer -> settings
    expect(ui.get_by_text("주치동물병원을 등록해 두면 응급 시 바로 전화할 수 있어요.")).to_be_visible()
    ui.get_by_role("button", name="등록", exact=True).click()
    expect(ui.get_by_role("heading", name="설정")).to_be_visible()
    expect(ui.get_by_placeholder("예: 말티즈, 믹스견, 모름")).to_have_value("말티즈")  # saved with the first consultation
    ui.get_by_placeholder("병원 이름 또는 동네 (예: 망원동)").fill("망원동")
    ui.get_by_role("button", name="검색", exact=True).click()
    first = ui.locator(".place-pick").first
    expect(first).to_be_visible(timeout=15_000)
    first.click()
    expect(ui.get_by_label("전화번호")).to_be_visible(timeout=10_000)
    if ui.get_by_label("병원 이름").count():  # no public-data match: the name is typed
        ui.get_by_label("병원 이름").fill("테스트동물병원")
    ui.get_by_label("전화번호").fill("02-123-4567")
    no_hscroll(ui, f"{tag} S09 settings")
    page.screenshot(path=out / f"{tag}-s09-settings.png", full_page=True)
    ui.get_by_role("button", name="저장", exact=True).click()
    expect(ui.get_by_text("저장했어요. 다음 상담부터 적용돼요.")).to_be_visible(timeout=10_000)

    # care: the consultation as a card with an AI summary; show-to-hospital view
    tab(ui, "케어")
    expect(ui.get_by_role("heading", name="케어")).to_be_visible()
    expect(ui.get_by_text("어제부터 하루에 두 번 토했어요", exact=False).first).to_be_visible(timeout=10_000)
    expect(ui.get_by_text("AI 요약", exact=True).first).to_be_visible(timeout=60_000)
    no_hscroll(ui, f"{tag} S08 care")
    page.screenshot(path=out / f"{tag}-s08-care.png", full_page=True)
    ui.get_by_role("button", name="병원에 보여주기").first.click()
    expect(ui.get_by_role("dialog", name="병원에 보여줄 요약")).to_be_visible()
    page.screenshot(path=out / f"{tag}-s08-handoff.png", full_page=False)
    ui.get_by_role("dialog", name="병원에 보여줄 요약").get_by_role("button", name="닫기").click()
    ui.get_by_role("button", name="다시 열기").first.click()
    expect(ui.get_by_role("article", name="AI 답변")).to_be_visible(timeout=10_000)

    # emergency sheet now shows the registered clinic
    ui.get_by_role("button", name="응급하면 바로 동물병원에 연락하세요").click()
    expect(ui.get_by_text("주치동물병원", exact=True)).to_be_visible()
    expect(ui.get_by_role("link", name="📞 02-123-4567")).to_be_visible()
    page.screenshot(path=out / f"{tag}-s02-emergency-clinic.png", full_page=False)
    ui.get_by_role("button", name="닫기").click()

    # delete
    if w < 1024 or framed:
        ui.get_by_role("button", name="메뉴").click()
        ui.get_by_role("menuitem", name="대화 삭제").click()
    else:
        ui.get_by_role("button", name="대화 삭제").click()
    expect(ui.get_by_role("dialog", name="이 대화를 삭제할까요?")).to_be_visible()
    page.screenshot(path=out / f"{tag}-s02-delete-confirm.png", full_page=False)
    ui.get_by_role("button", name="삭제", exact=True).click()
    # the next consultation starts from the saved profile: summary + [변경], no form
    expect(ui.get_by_role("region", name="우리 아이 정보")).to_be_visible()
    expect(ui.get_by_text(f"두부(말티즈) | {AGE_TEXT} | 3.2kg")).to_be_visible()
    expect(ui.get_by_text("주치동물병원:", exact=False)).to_be_visible()
    page.screenshot(path=out / f"{tag}-s02-profile-start.png", full_page=False)

    # clean up the saved profile so the run leaves nothing behind
    tab(ui, "설정")
    ui.get_by_role("button", name="저장된 정보 삭제").click()
    ui.get_by_role("button", name="삭제", exact=True).click()
    expect(ui.get_by_text("저장된 정보를 삭제했어요.")).to_be_visible()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--out", default=str(ROOT / "data_output" / "e2e"))
    ap.add_argument("--widths", default="390,1280")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = {int(x) for x in args.widths.split(",")}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            for w, h in WIDTHS:
                if w not in wanted:
                    continue
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=2, locale="ko-KR",
                                          has_touch=w < 768, is_mobile=w < 768)
                page = ctx.new_page()
                page.goto(args.base + "/")
                ui, framed = page, w >= 1024
                if framed:  # PC: introduction + the app in a phone frame
                    expect(page.get_by_role("heading", name="이어지게")).to_be_visible()
                    expect(page.locator("iframe.phone-app")).to_be_visible()
                    no_hscroll(page, f"w{w} showcase")
                    page.wait_for_timeout(1500)
                    page.screenshot(path=out / f"w{w}-showcase.png", full_page=False)
                    ui = page.locator("iframe.phone-app").element_handle().content_frame()
                    no_hscroll(ui, f"w{w} framed app")
                flow(page, ui, args.base, out, f"w{w}", w, framed)
                ctx.close()
                print(f"ok {w}px")
        finally:
            browser.close()
    print(f"screenshots: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

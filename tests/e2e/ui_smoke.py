"""Browser smoke test of S01-S03 against a running server (real LLM: a few cents of API calls).

    uvicorn backend.app.main:app --port 8000          # after `cd frontend; npm run build`
    python tests/e2e/ui_smoke.py [--base http://localhost:8000] [--out data_output/e2e]

Uses the installed Google Chrome through Playwright (pip install playwright; no browser download).
Checks: no horizontal overflow at each width, start -> dog form -> question -> grounded answer with
source chips -> source detail -> feedback -> delete; saves screenshots for review.
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


def no_hscroll(page: Page, label: str) -> None:
    over = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert over <= 0, f"{label}: horizontal overflow {over}px"


def flow(page: Page, base: str, out: Path, tag: str, w: int) -> None:
    page.goto(base + "/")
    expect(page.get_by_role("button", name="상담 시작")).to_be_visible()
    no_hscroll(page, f"{tag} S01")
    page.screenshot(path=out / f"{tag}-s01.png", full_page=True)
    page.get_by_role("button", name="상담 시작").click()

    # S02 first entry: send disabled until breed + age
    page.get_by_placeholder("반려견에 대해 궁금한 점을 적어 주세요").fill("어제부터 하루에 두 번 토했어요. 밥은 조금 먹어요. 병원에 가야 할까요?")
    expect(page.get_by_role("button", name="보내기")).to_be_disabled()
    expect(page.get_by_text("품종을 입력해 주세요")).to_be_visible()
    no_hscroll(page, f"{tag} S02 intake")
    page.screenshot(path=out / f"{tag}-s02-intake-errors.png", full_page=False)
    page.get_by_placeholder("예: 말티즈, 믹스견, 모름").fill("말티즈")
    # age: estimate mode first, then the birth date (year + month, day left as 모름)
    page.get_by_role("button", name="정확히 몰라요").click()
    page.get_by_label("대략적인 나이 년").fill("3")
    expect(page.get_by_text("현재 약 3세")).to_be_visible()
    page.get_by_role("button", name="생일을 알아요").click()
    expect(page.get_by_label("태어난 날 (모르면 비워 두세요)")).to_be_disabled()
    page.get_by_label("태어난 해").select_option(str(BIRTH[0]))
    page.get_by_label("태어난 달 (모르면 비워 두세요)").select_option(str(BIRTH[1]))
    expect(page.get_by_label("태어난 날 (모르면 비워 두세요)")).to_be_enabled()
    expect(page.get_by_text(f"현재 {AGE_TEXT}")).to_be_visible()
    no_hscroll(page, f"{tag} S02 birth date")
    page.get_by_role("button", name="건강상태 (선택)").click()
    page.get_by_placeholder("예: 3.2").fill("3.2")
    page.screenshot(path=out / f"{tag}-s02-intake-filled.png", full_page=False)
    page.get_by_role("button", name="보내기").click()

    expect(page.get_by_text("답변을 준비하고 있어요")).to_be_visible(timeout=10_000)
    page.screenshot(path=out / f"{tag}-s02-processing.png", full_page=False)
    expect(page.get_by_text(f"말티즈 · {BIRTH[0]}년 {BIRTH[1]}월생 · {AGE_TEXT} · 3.2kg")).to_be_visible()
    expect(page.get_by_role("article", name="AI 답변")).to_be_visible(timeout=90_000)
    no_hscroll(page, f"{tag} S02 answer")
    page.screenshot(path=out / f"{tag}-s02-answer.png", full_page=False)

    chips = page.get_by_role("button", name="근거 1")
    expect(chips.first).to_be_visible()
    chips.first.click()
    expect(page.get_by_text("원문 구절은 자료 이용 조건 확인 후 제공됩니다.")).to_be_visible(timeout=10_000)
    page.screenshot(path=out / f"{tag}-s03-source.png", full_page=False)
    page.get_by_role("button", name="닫기").locator("visible=true").first.click()
    expect(page.get_by_text("원문 구절은 자료 이용 조건 확인 후 제공됩니다.")).to_be_hidden()

    page.get_by_role("button", name="도움 됐어요").click()
    expect(page.get_by_text("저장됐어요")).to_be_visible()

    # out-of-scope follow-up renders as its own card type
    page.get_by_placeholder("반려견에 대해 궁금한 점을 적어 주세요").fill("고양이가 토해요")
    page.get_by_role("button", name="보내기").click()
    expect(page.get_by_role("article", name="지원 범위 밖")).to_be_visible(timeout=60_000)
    page.screenshot(path=out / f"{tag}-s02-out-of-scope.png", full_page=False)

    # reload restores the conversation from the server
    page.reload()
    expect(page.get_by_role("article", name="AI 답변")).to_be_visible(timeout=10_000)

    # delete
    if w < 1024:
        page.get_by_role("button", name="메뉴").click()
        page.get_by_role("menuitem", name="대화 삭제").click()
    else:
        page.get_by_role("button", name="대화 삭제").click()
    expect(page.get_by_role("dialog", name="이 대화를 삭제할까요?")).to_be_visible()
    page.screenshot(path=out / f"{tag}-s02-delete-confirm.png", full_page=False)
    page.get_by_role("button", name="삭제", exact=True).click()
    expect(page.get_by_text("우리 아이 정보")).to_be_visible()
    expect(page.get_by_placeholder("예: 말티즈, 믹스견, 모름")).to_have_value("말티즈")  # prefilled in this browser only


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
                flow(page, args.base, out, f"w{w}", w)
                ctx.close()
                print(f"ok {w}px")
        finally:
            browser.close()
    print(f"screenshots: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

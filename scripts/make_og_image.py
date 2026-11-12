"""Link-preview image (Open Graph, 1200x630) for KakaoTalk/Slack/Facebook etc.

    python -m scripts.make_og_image

Renders a small HTML card with headless Chrome (Playwright, dev-only) and writes
frontend/public/brand/og-image.png. Uses frontend/public/brand/twinvet-icon.png (make_brand_assets).
"""
from __future__ import annotations

import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "frontend" / "public" / "brand" / "twinvet-icon.png"
OUT = ROOT / "frontend" / "public" / "brand" / "og-image.png"

HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@500;700;800&family=Nunito:wght@800&display=swap" rel="stylesheet">
<style>
  html, body { margin: 0; }
  body { width: 1200px; height: 630px; box-sizing: border-box; padding: 72px 88px; font-family: "Noto Sans KR", sans-serif;
         background: radial-gradient(circle at 10% 15%, #efe8fa 0%, transparent 45%),
                     radial-gradient(circle at 92% 88%, #e3effb 0%, transparent 50%),
                     linear-gradient(135deg, #f8f6fc 0%, #f3f7fc 55%, #f2f8ef 100%);
         display: flex; flex-direction: column; justify-content: space-between; }
  .lockup { display: flex; align-items: center; gap: 20px; }
  .lockup img { width: 96px; height: 96px; }
  .wm { font-family: "Nunito", sans-serif; font-weight: 800; font-size: 60px; letter-spacing: -0.01em; }
  .t { color: #4d1e7f; } .v { color: #006bc6; } .c { color: #4d1e7f; margin-left: 14px; }
  h1 { margin: 0; font-size: 64px; line-height: 1.25; font-weight: 800; color: #102858; letter-spacing: -0.02em; }
  h1 em { font-style: normal; color: #006bc6; }
  p { margin: 18px 0 0; font-size: 30px; font-weight: 500; color: #4a5872; }
  .foot { display: flex; justify-content: space-between; align-items: center; font-size: 26px; color: #4a5872; }
  .url { color: #006bc6; font-weight: 700; }
  .dots { display: flex; gap: 10px; } .dots i { width: 16px; height: 16px; border-radius: 50%; display: block; }
</style></head><body>
  <div class="lockup"><img src="data:image/png;base64,ICON"><span class="wm"><span class="t">Twin</span><span class="v">Vet</span><span class="c">Care</span></span></div>
  <div><h1>반려견 건강 상담,<br><em>주치동물병원</em>까지 이어지게</h1>
       <p>근거 있는 AI 상담 · 응급 시 우리 병원 바로 연결 · 케어 기록</p></div>
  <div class="foot"><span class="url">twinvet.pawcheck.ai</span>
       <span class="dots"><i style="background:#4d1e7f"></i><i style="background:#006bc6"></i><i style="background:#6ab434"></i></span></div>
</body></html>"""


def main() -> None:
    html = HTML.replace("ICON", base64.b64encode(ICON.read_bytes()).decode())
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True)
        page = b.new_page(viewport={"width": 1200, "height": 630})
        page.set_content(html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(OUT))
        b.close()
    print("wrote", OUT, OUT.stat().st_size, "bytes")


if __name__ == "__main__":
    main()

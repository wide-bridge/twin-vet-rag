"""Replace the header brand in Stitch screens with the real TwinVet logo image + "Care" text.

Stitch can't use our logo file, so it draws the brand as text ("TwinVet Care", often with a stand-in
icon). This swaps that header block for brand/twinvet-logo-original.png followed by live "Care" text,
sized by brand/brand-lockup.css, then re-renders screen.png with headless Chrome.

    python -m scripts.apply_brand_lockup                # all screens in design/stitch/
    python -m scripts.apply_brand_lockup --only s01-start-mobile --no-render
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STITCH = ROOT / "design" / "stitch"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "path"}
MARK = "brand-lockup"
LOGO_W = {"MOBILE": "108px", "DESKTOP": "124px"}  # header is 56px tall; 683x283 logo -> 45px / 51px high

HEAD = """<!-- TwinVet Care brand lockup (scripts/apply_brand_lockup.py) -->
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@700&amp;display=swap" rel="stylesheet"/>
<link href="../../../brand/brand-tokens.css" rel="stylesheet"/>
<link href="../../../brand/brand-lockup.css" rel="stylesheet"/>
<style>
  header .brand-lockup {{ --logo-w: {w}; text-decoration: none; }}
  header:has(.brand-lockup) {{ background-color: #FFFFFF; }} /* logo is made for a white background */
  /* the logo's speech bubbles reach the image top: give the header breathing room above the lockup */
  header:has(.brand-lockup) {{ height: auto; min-height: 64px; padding-top: 14px; padding-bottom: 8px; }}
</style>
"""
LOCKUP = ('<a class="brand-lockup" href="#" aria-label="TwinVet Care">'
          '<img src="../../../brand/twinvet-logo-original.png" alt="" width="683" height="283"/>'
          '<span class="care" aria-hidden="true">Care</span></a>')


class Node:
    def __init__(self, tag, attrs, start, parent):
        self.tag, self.attrs, self.start, self.end, self.parent = tag, dict(attrs), start, None, parent
        self.children: list[Node] = []
        self.text = ""  # own text, excluding icon-font ligatures


class Tree(HTMLParser):
    def __init__(self, src: str):
        super().__init__(convert_charrefs=True)
        self.src, self.lines = src, [0]
        for line in src.splitlines(keepends=True):
            self.lines.append(self.lines[-1] + len(line))
        self.root = Node("#root", [], 0, None)
        self.cur = self.root
        self.feed(src)
        self.close()

    def _off(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self._off(), self.cur)
        self.cur.children.append(n)
        if tag in VOID:
            n.end = self._off() + len(self.get_starttag_text())
        else:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        n = Node(tag, attrs, self._off(), self.cur)
        n.end = n.start + len(self.get_starttag_text())
        self.cur.children.append(n)

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is self.root:
            return
        end = self.src.index(">", self._off()) + 1
        c = self.cur
        while c is not n:  # implicitly closed
            c.end = end
            c = c.parent
        n.end = end
        self.cur = n.parent

    def handle_data(self, data):
        if "material-symbols" not in self.cur.attrs.get("class", ""):
            self.cur.text += data


def brand_text(n: Node) -> str:
    if n.tag in ("svg", "img"):
        return ""
    return re.sub(r"\s+", "", n.text + "".join(brand_text(c) for c in n.children))


def walk(n: Node):
    yield n
    for c in n.children:
        yield from walk(c)


def patch(src: str, device: str) -> str | None:
    if MARK in src:
        if "breathing room above the lockup" not in src:  # files patched by an earlier version
            src = re.sub(r"<!-- TwinVet Care brand lockup .*?</style>\n", "", src, count=1, flags=re.S)
            return src.replace("</head>", HEAD.format(w=LOGO_W[device]) + "</head>", 1)
        return None
    header = next((n for n in walk(Tree(src).root) if n.tag == "header"), None)
    if header is None:
        raise ValueError("no <header>")
    hits = [n for n in walk(header) if n is not header and brand_text(n) == "TwinVetCare"
            and not any(c.tag in ("button", "input", "nav") for c in walk(n))]
    if not hits:
        raise ValueError("no 'TwinVet Care' brand text in header")
    block = hits[0]  # outermost element whose text is only the brand (icons/svg ignored)
    src = src[:block.start] + LOCKUP + src[block.end:]
    return src.replace("</head>", HEAD.format(w=LOGO_W[device]) + "</head>", 1)


MOBILE_W = 390  # CSS px; Chrome on Windows won't make a window narrower than ~500px, so a plain
                # --window-size=390 lays the page out ~492px wide and the screenshot cuts off the right side.
                # Mobile screens are therefore rendered inside a 390px iframe and the shot is cropped.


def chrome(*args: str, timeout: int = 120) -> str:
    return subprocess.run([str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                           "--allow-file-access-from-files", "--virtual-time-budget=8000", *args],
                          check=True, capture_output=True, text=True, encoding="utf-8", timeout=timeout).stdout


def frame_page(folder: Path, width: int, height: int, measure: bool) -> Path:
    script = ("<script>f.onload=()=>setTimeout(()=>document.body.append('SH='+"
              "f.contentDocument.documentElement.scrollHeight+';'),3000)</script>") if measure else ""
    page = folder / "_render.html"
    page.write_text(f'<html><body style="margin:0;overflow:hidden"><iframe id="f" src="screen.html" '
                    f'style="border:0;display:block;width:{width}px;height:{height}px"></iframe>{script}</body></html>',
                    encoding="utf-8")
    return page


def render(folder: Path, device: str) -> None:
    from PIL import Image
    png = folder / "screen.png"
    width = MOBILE_W if device == "MOBILE" else 1280
    min_h = 884 if device == "MOBILE" else 1024
    try:
        out = chrome(f"--window-size={max(width, 500)},{min_h}", "--dump-dom",
                     frame_page(folder, width, min_h, True).as_uri())
        height = max(min_h, int(re.search(r"SH=(\d+);", out).group(1)))
        chrome("--force-device-scale-factor=2", f"--window-size={max(width, 500)},{height}",
               f"--screenshot={png}", frame_page(folder, width, height, False).as_uri())
    finally:
        (folder / "_render.html").unlink(missing_ok=True)
    img = Image.open(png)
    img.crop((0, 0, width * 2, height * 2)).save(png)


def main() -> int:
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()
    failed = 0
    for meta_path in sorted(STITCH.glob("*/meta.json")):
        folder = meta_path.parent
        if args.only and folder.name not in args.only:
            continue
        device = json.loads(meta_path.read_text(encoding="utf-8"))["device"]
        html = folder / "screen.html"
        try:
            new = patch(html.read_text(encoding="utf-8"), device)
        except ValueError as e:
            print(f"FAIL {folder.name}: {e}", file=sys.stderr)
            failed += 1
            continue
        if new is not None:
            html.write_text(new, encoding="utf-8")
        print(f"{'patched' if new else 'already'} {folder.name}", flush=True)
        if not args.no_render:
            render(folder, device)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

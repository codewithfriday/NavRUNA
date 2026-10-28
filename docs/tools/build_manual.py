"""Build docs/NavRUNA_User_Manual.pdf from USER_MANUAL.md with headless Chromium.

Usage: python3 docs/tools/build_manual.py [path-to-chromium]
Two passes: the first finds the page of each chapter, the second prints the contents with page numbers.
"""
import re
import subprocess
import sys
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

DOCS = Path(__file__).resolve().parents[1]
CHROME = sys.argv[1] if len(sys.argv) > 1 else None

CSS = """
@page { size: A4; margin: 20mm 18mm 20mm 18mm; }
@page :first { margin: 0; }
:root { --ink:#172235; --ink2:#3d4b61; --teal:#2457a6; --amber:#c98a12; --line:#d5dee3; --paper:#f5f8f9; }
html { font-family: Inter, "DejaVu Sans", sans-serif; font-size: 10pt; color: var(--ink); line-height: 1.5; }
body { margin: 0; }
.cover { height: 297mm; width: 210mm; background: var(--ink); color: #fff; position: relative; page-break-after: always; overflow: hidden; }
.cover .inner { position: absolute; left: 22mm; right: 22mm; top: 70mm; }
.cover .ribbon { display: flex; align-items: center; gap: 6mm; font-size: 30pt; font-weight: 800; letter-spacing: 1px; }
.cover .ribbon svg { flex: 1; height: 16mm; }
.cover h1 { page-break-before: auto; font-size: 26pt; line-height: 1.15; margin: 22mm 0 4mm; font-weight: 700; border: 0; padding: 0; color:#fff; }
.cover .sub { color: #b9c6d8; font-size: 12pt; }
.cover .foot { position: absolute; left: 22mm; bottom: 22mm; color: #b9c6d8; font-size: 9.5pt; line-height: 1.6; }
.toc { page-break-after: always; }
.toc h2 { font-size: 16pt; margin: 0 0 6mm; }
.toc ol { list-style: none; padding: 0; margin: 0; }
.toc li { display: flex; align-items: baseline; padding: 2.2mm 0; border-bottom: 1px solid var(--line); font-size: 10.5pt; }
.toc li .n { flex: 1; }
.toc li.sub { padding: 1.2mm 0 1.2mm 7mm; font-size: 9.5pt; color: var(--ink2); border-bottom: none; }
.toc li .p { font-variant-numeric: tabular-nums; color: var(--ink2); }
h1 { font-size: 17pt; margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 2px solid var(--amber); page-break-before: always; font-weight: 700; }
h2 { font-size: 12.5pt; margin: 7mm 0 2mm; color: var(--teal); page-break-after: avoid; }
h3 { font-size: 11pt; margin: 5mm 0 2mm; page-break-after: avoid; }
p, li { orphans: 3; widows: 3; }
ul, ol { padding-left: 5mm; }
li { margin: 0.6mm 0; }
img { max-width: 100%; max-height: 110mm; width: auto; border: 1px solid var(--line); border-radius: 2px; display: block; margin: 3mm auto 1mm; page-break-inside: avoid; }
p:has(> img) { page-break-inside: avoid; }
p:has(> img) + p > em:only-child { display:block; }
.cap { font-size: 8.5pt; color: var(--ink2); text-align: center; margin: 0 0 4mm; }
table { border-collapse: collapse; width: 100%; margin: 3mm 0 4mm; font-size: 8.8pt; page-break-inside: auto; }
tr { page-break-inside: avoid; }
th { text-align: left; background: var(--paper); border-bottom: 1.5px solid var(--ink2); padding: 1.6mm 2mm; font-weight: 600; }
td { border-bottom: 1px solid var(--line); padding: 1.4mm 2mm; vertical-align: top; }
td .help { color: var(--ink2); font-size: 8pt; }
code, pre { font-family: "DejaVu Sans Mono", monospace; font-size: 8.3pt; }
pre { background: var(--paper); border: 1px solid var(--line); padding: 2.5mm; white-space: pre-wrap; }
strong { font-weight: 650; }
.appendix table { font-size: 8pt; }
"""

RIBBON = """<svg viewBox="0 0 400 40" preserveAspectRatio="none"><rect x="0" y="0" width="400" height="40" fill="#2b3850"/>""" + "".join(
    f'<rect x="{8+i*14}" y="6" width="9" height="28" fill="#fff"/>' for i in range(8)) + \
    """<line x1="130" y1="20" x2="392" y2="20" stroke="#fff" stroke-width="2.5" stroke-dasharray="22 14"/></svg>"""


def md_to_html(text: str) -> str:
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    html = markdown.markdown(text, extensions=["tables", "fenced_code", "attr_list"])
    # captions from alt text
    html = re.sub(r'<p><img alt="([^"]*)" src="([^"]+)" ?/?></p>',
                  lambda m: f'<p><img alt="{m[1]}" src="{m[2]}"></p><p class="cap">{m[1]}</p>', html)
    return html


def headings(html: str):
    out = []
    for m in re.finditer(r"<h([12])>(.*?)</h\1>", html):
        out.append((int(m[1]), re.sub("<[^>]+>", "", m[2])))
    return out


def page_html(body: str, toc: str) -> str:
    cover = f"""<section class="cover"><div class="inner"><div class="ribbon"><span>07L</span>{RIBBON}<span>25R</span></div>
<h1>NavRUNA<br>User Manual</h1><div class="sub">AirNav Runway Utilization and Network Analytics · version 1.0</div></div>
<div class="foot">AirNav Indonesia · Safety management analytics<br>Implements the AirNav Safety Analytics Platform SRS (ISO/IEC/IEEE 29148)<br>October 2026</div></section>"""
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{cover}{toc}<main>{body}</main></body></html>"


def toc_html(hs, pages=None):
    items = []
    for lvl, t in hs:
        p = pages.get(t, "") if pages else ""
        if lvl == 1 or t[:1].isdigit():
            items.append(f"<li class='{'sub' if lvl == 2 else ''}'><span class='n'>{t}</span><span class='p'>{p}</span></li>")
    return f"<section class='toc'><h2>Contents</h2><ol>{''.join(items)}</ol></section>"


def render(html: str, out: Path):
    tmp = DOCS / "_manual.html"
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": CHROME} if CHROME else {}))
        pg = b.new_page()
        pg.goto(tmp.as_uri())
        pg.wait_for_timeout(800)
        pg.pdf(path=str(out), format="A4", print_background=True, display_header_footer=True,
               header_template="<span></span>",
               footer_template="<div style='width:100%;font-family:Inter,sans-serif;font-size:7.5pt;color:#3d5163;padding:0 18mm;display:flex;justify-content:space-between'>"
                               "<span>NavRUNA User Manual</span><span class='pageNumber'></span></div>", prefer_css_page_size=True)
        b.close()
    tmp.unlink()


def find_pages(pdf: Path, hs):
    txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout.split("\f")
    pages = {}
    for lvl, t in hs:
        key = re.sub(r"\s+", " ", t).strip()
        for i, pg in enumerate(txt[1:], start=2):  # last exact match wins: the contents page comes first
            if any(re.sub(r"\s+", " ", line).strip() == key for line in pg.splitlines()):
                pages[t] = i
    return pages


def main():
    body = md_to_html((DOCS / "USER_MANUAL.md").read_text(encoding="utf-8"))
    hs = headings(body)
    out = DOCS / "NavRUNA_User_Manual.pdf"
    render(page_html(body, toc_html(hs)), out)
    pages = find_pages(out, hs)
    render(page_html(body, toc_html(hs, pages)), out)
    print("written", out, "chapters with pages:", len(pages))


if __name__ == "__main__":
    main()

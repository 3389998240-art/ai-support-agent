"""把 docs/项目讲解.md 渲染成自包含的 HTML（可离线阅读、可直接打印成 PDF）。

流程：
1. 从 frontend 侧预渲染 mermaid（见 frontend/scripts/prerender-mermaid.mjs），
   结果存在 docs/.mermaid.json；
2. 本脚本把 SVG 内联进 HTML，因此成品不依赖任何 CDN。

用法：
    python scripts/build_docs.py                       # 默认渲染 docs/项目讲解.md
    python scripts/build_docs.py docs/面试拷打题库.md   # 也可渲染任意一篇
生成：
    与源文件同名的 .html
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else DOCS / "项目讲解.md"
if not SOURCE.is_absolute():
    SOURCE = ROOT / SOURCE
TARGET = SOURCE.with_suffix(".html")
TITLE = SOURCE.stem
DIAGRAMS = DOCS / ".mermaid.json"

CSS = """
:root {
  --ink: #16161a; --ink2: #3f3f46; --muted: #71717a; --line: #e6e6ea;
  --accent: #ff6900; --accent-ink: #b94b00; --soft: #fff6ef; --code-bg: #fafafa;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: #f5f5f7; color: var(--ink);
  font: 15px/1.85 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
        "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}
.wrap {
  display: grid; grid-template-columns: 228px minmax(0, 1fr);
  gap: 26px; max-width: 1400px; margin: 0 auto; padding: 28px 20px 80px;
}
nav {
  position: sticky; top: 32px; align-self: start; max-height: calc(100vh - 64px);
  overflow-y: auto; font-size: 12.5px; line-height: 1.65;
  border-right: 1px solid var(--line); padding-right: 14px;
}
nav strong { display: block; font-size: 11px; letter-spacing: .14em; color: var(--muted); margin-bottom: 10px; }
nav ul { list-style: none; margin: 0; padding: 0; }
nav li { margin: 0 0 1px; }
nav li ul { padding-left: 10px; }
nav a { color: var(--ink2); text-decoration: none; display: block; padding: 2px 6px; border-radius: 6px; }
nav a:hover { background: var(--soft); color: var(--accent-ink); }
main {
  background: #fff; border: 1px solid var(--line); border-radius: 16px;
  padding: 40px 44px 56px; min-width: 0;
  box-shadow: 0 1px 2px rgba(16,16,20,.04), 0 12px 40px rgba(16,16,20,.05);
}
h1 { font-size: 27px; margin: 0 0 8px; letter-spacing: -.02em; }
h2 {
  font-size: 20px; margin: 46px 0 14px; padding-top: 18px;
  border-top: 1px solid var(--line); letter-spacing: -.01em;
}
h3 { font-size: 16px; margin: 28px 0 10px; }
h4 { font-size: 14px; margin: 20px 0 8px; color: var(--ink2); }
p { margin: 10px 0; }
blockquote {
  margin: 14px 0; padding: 10px 16px; background: var(--soft);
  border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0; color: var(--ink2);
}
blockquote p { margin: 4px 0; }
ul, ol { padding-left: 22px; margin: 10px 0; }
li { margin: 4px 0; }
code {
  font-family: "SFMono-Regular", "JetBrains Mono", Consolas, monospace;
  font-size: 12.5px; background: #f4f4f6; padding: 1.5px 5px; border-radius: 5px;
}
pre {
  background: var(--code-bg); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; overflow-x: auto; line-height: 1.65;
}
pre code { background: none; padding: 0; font-size: 12.5px; }
table { border-collapse: collapse; margin: 14px 0; font-size: 13px; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--line); padding: 7px 10px; text-align: left; vertical-align: top; }
th { background: #fafafa; font-weight: 600; }
tr:nth-child(even) td { background: #fcfcfd; }
a { color: var(--accent-ink); }
hr { border: none; border-top: 1px solid var(--line); margin: 32px 0; }
.figure {
  margin: 18px 0; padding: 12px; border: 1px solid var(--line);
  border-radius: 12px; background: #fff; text-align: center; overflow-x: auto;
}
.figure svg { width: 100%; max-width: 100%; height: auto; }  /* 图宽已控制在版心内，等比缩放 ≤5%，不变形 */
@media (max-width: 980px) {
  .wrap { grid-template-columns: 1fr; }
  nav { display: none; }
  main { padding: 28px 22px 40px; }
}
@media print {
  body { background: #fff; }
  .wrap { display: block; padding: 0; max-width: none; }
  nav { display: none; }
  main { border: none; box-shadow: none; padding: 0; border-radius: 0; }
  h2 { page-break-after: avoid; }
  pre, table, .figure { page-break-inside: avoid; }
  .figure svg { max-width: 100%; }
}
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} · 售后支持 Agent</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <nav><strong>本文导航</strong>{toc}</nav>
  <main>{body}</main>
</div>
</body>
</html>
"""


def _inline_diagrams(body: str) -> tuple[str, int]:
    """把 <div class="mermaid">源码</div> 换成预渲染好的内联 SVG。"""
    if not DIAGRAMS.exists():
        return body, 0

    svgs: dict[str, str] = json.loads(DIAGRAMS.read_text(encoding="utf-8"))
    state = {"index": 0, "used": 0}

    def replace(match: re.Match) -> str:
        key = str(state["index"])
        state["index"] += 1
        svg = svgs.get(key)
        if not svg:
            return match.group(0)
        state["used"] += 1
        return f'<div class="figure">{svg}</div>'

    return re.sub(r'<div class="mermaid">.*?</div>', replace, body, flags=re.S), state["used"]


def convert() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    md = markdown.Markdown(
        extensions=["tables", "fenced_code", "sane_lists", "toc"],
        extension_configs={"toc": {"toc_depth": "2-3"}},
        output_format="html5",
    )
    body = md.convert(source)
    toc_html = md.toc

    # markdown 会把 mermaid 渲染成 <pre><code class="language-mermaid">，先标记成占位 div
    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        lambda match: f'<div class="mermaid">{match.group(1)}</div>',
        body,
        flags=re.S,
    )
    body, figures = _inline_diagrams(body)

    # 文档正文里已经有"目录"一节，导航里不用再列一次
    toc_html = re.sub(r'<li><a href="#[^"]*">目录</a></li>', "", toc_html)

    html = TEMPLATE.format(css=CSS, toc=toc_html, body=body, title=TITLE)
    TARGET.write_text(html, encoding="utf-8")

    print(f"已生成 {TARGET.relative_to(ROOT)}  ({len(html) / 1024:.0f} KB，内联图 {figures} 张)")


if __name__ == "__main__":
    convert()

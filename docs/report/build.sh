#!/usr/bin/env bash
# report.md -> report.html -> report.pdf. No pandoc/LaTeX: python-markdown (via uvx) + headless Chrome. Local only.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
uvx --python 3.12 --with markdown python -I - <<'PY'
import markdown, pathlib
md = pathlib.Path("report.md").read_text()
body = markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
css = """body{font:11pt/1.45 -apple-system,Helvetica,Arial,sans-serif;max-width:46em;margin:2em auto;padding:0 1em;color:#1a1a1a}
h1{font-size:22pt}h2{font-size:15pt;border-bottom:1px solid #ccc;padding-bottom:.2em;margin-top:1.6em}h3{font-size:12pt}
table{border-collapse:collapse;margin:1em 0;font-size:9pt;width:100%;page-break-inside:avoid}th,td{border:1px solid #bbb;padding:3px 6px;vertical-align:top}th{background:#f0f0f0}
code{font:9pt Menlo,monospace;background:#f4f4f4;padding:0 2px}pre{background:#f4f4f4;padding:.6em;overflow:auto;font-size:8.5pt;page-break-inside:avoid}pre code{background:none}
blockquote{border-left:4px solid #c90;margin:1em 0;padding:.2em 1em;background:#fff8e6}h2,h3{page-break-after:avoid}img{max-width:100%;max-height:17cm;display:block;margin:1em auto}em{color:#333}"""
pathlib.Path("report.html").write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>Guetteur technical report (Team Drift)</title><style>{css}</style></head><body>{body}</body></html>')
PY
"$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf=report.pdf "file://$PWD/report.html" 2>/dev/null
ls -l report.pdf

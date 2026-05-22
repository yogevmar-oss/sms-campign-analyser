"""
Build a self-contained HTML artifact from an extracted JSON file.

Usage:
    python build_artifact.py --json output/TERMINALX_v3.json --out output/TERMINALX_explorer.html

Requires: Node.js + npm (run `npm install` once in this directory first).

esbuild bundles React + Recharts directly into the HTML — no CDN, no internet
required to view the output file.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
TEMPLATE_PATH = HERE / "artifact_template_v3.jsx"

HTML_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{store} - SMS Discount Analysis</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,wght@0,400;0,500;0,600;1,400;1,500&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #FAF8F4; }}
    select {{ cursor: pointer; }}
  </style>
</head>
<body>
  <div id="root"></div>
  <script>
{js_body}
  </script>
</body>
</html>
"""


def _transpile_and_bundle(jsx_source: str) -> str:
    """Bundle JSX + React + Recharts into a single IIFE using esbuild."""
    with tempfile.NamedTemporaryFile(
        suffix=".jsx", mode="w", encoding="utf-8", delete=False, dir=HERE
    ) as f:
        f.write(jsx_source)
        tmp = f.name

    try:
        cmd = (
            f'npx --yes esbuild "{tmp}"'
            f" --bundle --format=iife --target=es2017"
            f" --platform=browser"
        )
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=str(HERE))
        if result.returncode != 0:
            print("esbuild stderr:", result.stderr[:1200])
            sys.exit(f"esbuild failed (exit {result.returncode})")
        return result.stdout
    finally:
        os.unlink(tmp)


def build(json_path: Path, out_path: Path) -> None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    store = data.get("store", json_path.stem.replace("_v3", ""))

    raw = TEMPLATE_PATH.read_text(encoding="utf-8")

    # 1. Keep imports as-is — esbuild resolves them from node_modules.
    #    Just add ReactDOM import for the render call.
    jsx = 'import ReactDOM from "react-dom/client";\n' + raw

    # 2. Remove `export default` — App becomes a module-level function
    jsx = jsx.replace("export default function App()", "function App()")

    # 3. Inject store data
    jsx = jsx.replace("__DATA_PLACEHOLDER__", json.dumps(data, ensure_ascii=False))

    # 4. Append render call
    jsx += "\nReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));\n"

    # 5. Bundle everything into a single IIFE
    print(f"  bundling {store}...")
    js = _transpile_and_bundle(jsx)

    # 6. Assemble HTML
    indented = "\n".join("    " + line for line in js.splitlines())
    html = HTML_SHELL.format(store=store, js_body=indented)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"  -> {out_path}  ({size_kb} KB)")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--json", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    build(Path(args.json), Path(args.out))


if __name__ == "__main__":
    main()

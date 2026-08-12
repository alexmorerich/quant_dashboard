from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "cloudflare" / "site"


def main() -> None:
    result = ROOT / "research_result.json"
    if not result.exists():
        raise SystemExit("research_result.json is missing. Run: python3 run_research.py")
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "frontend", SITE)
    shutil.copy2(result, SITE / "research_result.json")
    (SITE / "_headers").write_text(
        """/research_result.json\n  Cache-Control: public, max-age=60, s-maxage=300\n  X-Content-Type-Options: nosniff\n\n/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: strict-origin-when-cross-origin\n"""
    )
    print(f"Cloudflare site built at {SITE}")


if __name__ == "__main__":
    main()

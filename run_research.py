from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.data_pipeline import download_dataset
from backend.research_engine import DEFAULT_CONFIG, METHODS, run_research


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the reproducible four-asset research result")
    parser.add_argument("--download", action="store_true", help="download/refresh FRED and Shiller inputs")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output", type=Path, default=ROOT / "research_result.json")
    parser.add_argument("--optimizer", choices=list(METHODS), default=None)
    args = parser.parse_args()
    if args.download or not (args.data_dir / "returns_monthly.csv").exists():
        download_dataset(args.data_dir, force=args.download)
    config = {"optimizer": args.optimizer} if args.optimizer else {}
    result = run_research(args.data_dir, config)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False))
    print(json.dumps({"output": str(args.output), "research_window": result["research_window"], "optimizer": result["optimizer"], "sample_size": result["sample_size"], "oos_sharpe": result["out_of_sample"].get("sharpe"), "robustness_score": result["robustness"]["score"]}, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .data_pipeline import download_dataset, load_metadata
from .research_engine import DEFAULT_CONFIG, cache_key, run_research


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
DATA = ROOT / "data"
CACHE = ROOT / "cache"


def _query_config(query: dict[str, list[str]]) -> dict[str, object]:
    config = {}
    for key in ["research_window", "optimizer", "frequency", "rebalance_frequency", "start_date", "end_date"]:
        if query.get(key):
            config[key] = query[key][-1]
    for key in ["training_years", "test_months", "transaction_cost_bps", "monte_carlo_simulations"]:
        if query.get(key):
            config[key] = int(query[key][-1])
    if query.get("constraints"):
        config["constraints"] = json.loads(query["constraints"][-1])
    if query.get("robust_objective"):
        config["robust_objective"] = json.loads(query["robust_objective"][-1])
    return config


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "QuantAllocationDashboard/1.0"

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/health":
                self._send_json({"status": "ok", "data_ready": (DATA / "returns_monthly.csv").exists()})
                return
            if parsed.path == "/api/config":
                self._send_json(DEFAULT_CONFIG)
                return
            if parsed.path == "/api/research":
                if not (DATA / "returns_monthly.csv").exists():
                    self._send_json({"error": "Research data is not present. Run python3 run_research.py --download first."}, 503)
                    return
                config = _query_config(query)
                metadata = load_metadata(DATA)
                key = cache_key({"engine_version": "2026-08-12-v3", "config": config, "retrieved_at": metadata.get("retrieved_at")})
                cache_file = CACHE / f"{key}.json"
                if cache_file.exists():
                    self._send_json(json.loads(cache_file.read_text()))
                    return
                result = run_research(DATA, config)
                CACHE.mkdir(exist_ok=True)
                cache_file.write_text(json.dumps(result, allow_nan=False))
                self._send_json(result)
                return
            if parsed.path in {"/", "/index.html"}:
                self._send_file(FRONTEND / "index.html")
                return
            relative = parsed.path.lstrip("/")
            if relative.startswith("frontend/"):
                relative = relative.removeprefix("frontend/")
            self._send_file(FRONTEND / relative)
        except Exception as exc:  # keep the dashboard error visible and actionable
            self._send_json({"error": str(exc), "type": type(exc).__name__}, 500)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the quant allocation dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--download", action="store_true", help="download source data before serving")
    args = parser.parse_args()
    if args.download:
        download_dataset(DATA)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Quant dashboard: http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

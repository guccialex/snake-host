#!/usr/bin/env python3
import argparse
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List


def list_all_files(base_dir: Path) -> List[str]:
    # Only include .txt files (excluding index.txt) and return names WITHOUT the .txt extension
    names = [
        p.stem
        for p in base_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".txt" and p.name != "index.txt"
    ]
    return sorted(names)


def write_all_scene_names(base_dir: Path) -> None:
    names = list_all_files(base_dir)
    content = "\n".join(names) + ("\n" if names else "")
    (base_dir / "index.txt").write_text(content, encoding="utf-8")


class SceneRequestHandler(BaseHTTPRequestHandler):
    server_version = "SceneFileServer/0.1"

    def _set_headers(self, code=200, content_type="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        # Basic CORS to allow browser-based clients
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        # Handle CORS preflight
        self._set_headers(204, "text/plain")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0

        try:
            raw = self.rfile.read(length) if length > 0 else b""
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            self._set_headers(400)
            self.wfile.write(json.dumps({"ok": False, "error": "Invalid JSON body"}).encode("utf-8"))
            return

        if not isinstance(payload, dict):
            self._set_headers(400)
            self.wfile.write(json.dumps({"ok": False, "error": "JSON body must be an object"}).encode("utf-8"))
            return

        if "FileName" not in payload or "Contents" not in payload:
            self._set_headers(400)
            self.wfile.write(json.dumps({"ok": False, "error": "Missing 'FileName' or 'Contents'"}).encode("utf-8"))
            return

        name = str(payload["FileName"])
        contents = str(payload["Contents"])

        # Sanitize filename: allow alnum, underscore, hyphen, dot, space; replace others with underscore
        safe_name = re.sub(r"[^A-Za-z0-9_\-\. ]", "_", name).strip()
        # Disallow path separators
        safe_name = safe_name.replace("/", "_").replace("\\", "_")

        if not safe_name:
            self._set_headers(400)
            self.wfile.write(json.dumps({"ok": False, "error": "Empty or invalid FileName"}).encode("utf-8"))
            return

        # Ensure .txt extension (avoid double .txt)
        if not safe_name.endswith(".txt"):
            safe_name += ".txt"

        target_path = self.server.base_dir / safe_name  # type: ignore[attr-defined]

        try:
            # Write scene file (overwrite if exists)
            target_path.write_text(contents, encoding="utf-8")

            # Update invariant file
            write_all_scene_names(self.server.base_dir)  # type: ignore[attr-defined]
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
            return

        self._set_headers(200)
        self.wfile.write(json.dumps({"ok": True, "file": target_path.name}).encode("utf-8"))

    def log_message(self, fmt, *args):
        # Print concise logs to stdout
        print(f"[{self.address_string()}] {fmt % args}")


def run(host: str, port: int, base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    # Initialize invariant file on startup
    try:
        write_all_scene_names(base_dir)
    except Exception as e:
        print(f"Warning: failed to initialize index.txt: {e}")

    # Start background invariant maintainer (updates every 1 second)
    stop_event = threading.Event()

    def _invariant_loop():
        while not stop_event.is_set():
            try:
                write_all_scene_names(base_dir)
            except Exception as e:
                print(f"Warning: invariant update failed: {e}")
            # Wait up to 1 second, exit early if stopped
            stop_event.wait(1.0)

    invariant_thread = threading.Thread(
        target=_invariant_loop, name="InvariantMaintainer", daemon=True
    )
    invariant_thread.start()

    httpd = ThreadingHTTPServer((host, port), SceneRequestHandler)
    # Attach base_dir to server instance for handler access
    httpd.base_dir = base_dir  # type: ignore[attr-defined]

    print(f"Scene File Server running at http://{host}:{port}")
    print(f"Writing files in: {base_dir}")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        # Stop background invariant thread
        try:
            stop_event.set()
            invariant_thread.join(timeout=2.0)
        except Exception:
            pass
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="Simple local JSON POST scene file server.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="Host interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")),
                        help="Port to listen on (default: 8765)")
    parser.add_argument("--dir", default=os.environ.get("SCENE_DIR", "."),
                        help="Directory to write files into (default: current directory)")
    args = parser.parse_args()

    base_dir = Path(args.dir).resolve()
    run(args.host, args.port, base_dir)


if __name__ == "__main__":
    main()

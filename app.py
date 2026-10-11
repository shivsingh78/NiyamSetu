from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json, mimetypes, os, signal, sys, threading
from niyamsetu import (
    ROOT, DATA, load_rules, read_manifest, search, health, _load_jsonl, score_rules, snippet_for,
    LOCAL_PDF_FILE, LOCAL_PDF_URL, RULES_LISTING_URL, OFFICIAL_PDF_URL
)
from llm_rag import answer_with_rag, llm_public_status

HOST = os.environ.get("NIYAMSETU_HOST", "0.0.0.0")
try:
    PORT = int(os.environ.get("PORT", "8000"))
except ValueError:
    PORT = 8000
# Public deployments must not let anonymous visitors overwrite the dataset.
IMPORT_ENABLED = os.environ.get("NIYAMSETU_ENABLE_IMPORT", "0") == "1"
INDEX = ROOT / "static" / "index.html"
MAX_IMPORT_BYTES = 12 * 1024 * 1024

class Handler(BaseHTTPRequestHandler):
    server_version = "NiyamSetu/1.0"

    def _json(self, obj, status=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        path_clean = self.path.split("?")[0].split("#")[0]
        if path_clean in ("/", "/index.html"):
            try: b = INDEX.read_bytes()
            except OSError: return self._json({"ok": False, "message": "UI file missing"}, 500)
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers(); self.wfile.write(b); return
        if path_clean in ("/api/pdf", "/pdf", "/api/pdf/"):
            if not LOCAL_PDF_FILE.exists():
                return self._json({
                    "ok": False,
                    "message": "Local verified PDF not available on server.",
                    "official_pdf_url": OFFICIAL_PDF_URL,
                    "rules_listing_url": RULES_LISTING_URL,
                }, 404)
            try:
                b = LOCAL_PDF_FILE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Content-Disposition", 'inline; filename="bvn06032025E.pdf"')
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(b)
                return
            except OSError:
                return self._json({"ok": False, "message": "Failed to read local PDF file"}, 500)
        if path_clean == "/healthz":
            return self._json({"ok": True})
        if path_clean == "/api/health":
            status = health()
            status["llm"] = llm_public_status()
            return self._json(status)
        if path_clean == "/api/legal-sources": return self._json(read_manifest())
        if path_clean == "/favicon.ico":
            self.send_response(204); self.end_headers(); return
        return self._json({"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_IMPORT_BYTES:
                return self._json({"ok": False, "message": "Request too large"}, 413)
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            if self.path == "/api/search":
                q = str(payload.get("query", ""))
                rules, mode = load_rules()
                return self._json(answer_with_rag(q, rules, mode, search, score_rules, snippet_for))
            if self.path == "/api/import":
                if not IMPORT_ENABLED:
                    return self._json({"ok": False, "message": "Dataset import is disabled on this server."}, 403)
                content = payload.get("content", "")
                if not isinstance(content, str) or not content.strip():
                    return self._json({"ok": False, "message": "File content is empty"}, 400)
                temp = DATA / "_upload_validation.jsonl"
                try:
                    temp.write_text(content, encoding="utf-8")
                    rows = _load_jsonl(temp)
                except Exception as exc:
                    return self._json({"ok": False, "message": f"Dataset validation failed: {exc}"}, 400)
                finally:
                    try: temp.unlink()
                    except OSError: pass
                # Only accept the expected JSONL structure. Keep a backup if a prior import exists.
                target = DATA / "bhumi_vikas_rules.jsonl"
                if target.exists():
                    backup = DATA / "bhumi_vikas_rules.previous.jsonl"
                    if not backup.exists(): backup.write_bytes(target.read_bytes())
                target.write_text(content, encoding="utf-8")
                return self._json({"ok": True, "message": f"Loaded {len(rows)} rule records. The imported text still needs legal/source verification.", "records_loaded": len(rows), "dataset_mode": "full_dataset"})
            return self._json({"ok": False, "message": "Not found"}, 404)
        except json.JSONDecodeError:
            return self._json({"ok": False, "message": "Invalid JSON request"}, 400)
        except Exception as exc:
            return self._json({"ok": False, "message": f"Request failed safely: {type(exc).__name__}"}, 500)

    def log_message(self, fmt, *args):
        # Keep terminal useful and avoid logging question text, which may contain property details.
        sys.stderr.write("NiyamSetu HTTP: " + (fmt % args) + "\n")

def main() -> int:
    try:
        h = health()
        llm_on = llm_public_status()["enabled"]
    except Exception as exc:
        print(f"NiyamSetu startup failed while loading data ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print("\n  NiyamSetu — Every Answer Backed by a Rule")
    print(f"  Listening on {HOST}:{PORT}")
    print(f"  Dataset: {h['dataset_mode']} ({h['records_loaded']} records)")
    print(f"  LLM RAG: {'enabled' if llm_on else 'off (retrieval fallback)'}")
    print("  Legal status: not fully verified; the UI will label the supplied rules historical.\n", flush=True)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"NiyamSetu could not bind {HOST}:{PORT} ({type(exc).__name__}).", file=sys.stderr)
        return 1
    server.daemon_threads = True
    # shutdown() must run off the serving thread, so signal handlers spawn a helper.
    def _stop(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

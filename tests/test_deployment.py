import json, os, socket, subprocess, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_server_under_port_env_and_sigterm(tmp_path):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("NIYAMSETU_", "DEEPSEEK_"))}
    env.update(PORT=str(port), NIYAMSETU_API_KEY="")
    # Run from an unrelated cwd to prove paths are cwd-independent.
    p = subprocess.Popen([sys.executable, str(ROOT / "app.py")], cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                if _get(port, "/healthz")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        assert _get(port, "/healthz")[0] == 200
        st, body = _get(port, "/")
        assert st == 200 and b"NiyamSetu" in body
        st, body = _get(port, "/api/health")
        h = json.loads(body)
        assert h["records_loaded"] == 106 and h["dataset_mode"] == "full_dataset"
        st, d = _post(port, "/api/search", {"query": "front open space for residential building"})
        assert st == 200 and d.get("sources") or d.get("rule")
        assert d.get("llm_used") is not True
        st, d = _post(port, "/api/search", {"query": "what is the best pizza in Bhopal"})
        assert st == 200 and d.get("ok") is False
        st, d = _post(port, "/api/import", {"content": "{}"})
        assert st == 403
        assert _get(port, "/data/../app.py")[0] == 404
    finally:
        p.terminate()
    assert p.wait(timeout=10) == 0


def test_dataset_check_script():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_dataset.py")], capture_output=True, text=True)
    assert r.returncode == 0 and "106" in r.stdout

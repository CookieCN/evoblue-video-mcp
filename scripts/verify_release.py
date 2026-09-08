"""Clean-machine release verification for a packaged onedir bundle.

Runs the acceptance checks ASR_PLAN section 5 (ASR-4) requires against a
bundle produced by ``scripts/build_package.py``, in an isolated data directory
so the host machine's real EvoBlue state cannot mask a packaging bug:

  1. fresh-profile boot: engine boots, /api/health answers, /api/models ships
     the approval flags from the bundled approvals registry;
  2. production token: the token file is created on first run, data endpoints
     reject unauthenticated requests, /api/health stays exempt;
  3. provider-failure isolation: with a corrupt model file present, the engine
     still boots, the failed tier is skipped with a stable error code in the
     log, and nothing crashes — provider failure must not corrupt engine state.

Usage:
    uv run python scripts/verify_release.py dist/evoblue-video-mcp-full \
        [--models-dir DIR] [--port 8899]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http_get(url, token=None, timeout=5.0):
    """GET a JSON endpoint; returns (status_code, parsed-or-text body)."""
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_for_health(port, deadline_s=90.0):
    deadline = time.monotonic() + deadline_s
    url = f"http://127.0.0.1:{port}/api/health"
    last_error = ""
    while time.monotonic() < deadline:
        try:
            status, body = http_get(url)
        except (urllib.error.URLError, OSError) as exc:
            last_error = str(exc)
            status, body = 0, None
        if status == HTTPStatus.OK and isinstance(body, dict) and body.get("status") == "ok":
            return
        time.sleep(0.5)
    raise SystemExit(f"engine did not become healthy within {deadline_s}s: {url} ({last_error})")


class Engine:
    """One packaged engine process bound to an isolated data directory."""

    def __init__(self, bundle, port, workdir):
        suffix = ".exe" if sys.platform == "win32" else ""
        self.exe = None
        for variant in ("full", "base"):
            candidate = bundle / f"evoblue-engine-{variant}{suffix}"
            if candidate.exists():
                self.exe = candidate
                break
        if self.exe is None:
            raise SystemExit(f"no evoblue-engine executable in {bundle}")
        self.port = port
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.proc = None
        self.log_handle = None
        self.log_path = self.workdir / "engine.log"

    def start(self, production=False, models_dir=None):
        env = dict(os.environ)
        env["EVOBLUE_DATA_DIRECTORY"] = str(self.workdir / "data")
        env["EVOBLUE_ENGINE_PORT"] = str(self.port)
        if production:
            env["EVOBLUE_ENVIRONMENT"] = "production"
        if models_dir is not None:
            env["EVOBLUE_ASR_MODEL_DIR"] = str(models_dir)
        self.log_handle = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            [str(self.exe)],
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(self.workdir),
        )
        wait_for_health(self.port)

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.proc = None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

    def log_contains(self, needle):
        return needle in self.log_path.read_text(encoding="utf-8", errors="replace")


def check(name, ok, detail="", engine_log=None):
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line = f"{line} ({detail})"
    print(line)
    if not ok:
        if engine_log is not None and engine_log.is_file():
            tail = engine_log.read_text(encoding="utf-8", errors="replace")[-1500:]
            print("  --- engine.log tail ---")
            print(tail)
        raise SystemExit(f"verification failed at: {name}")


def _bridge_handshake(engine):
    """MCP initialize+list_tools against the bundle exe's "bridge" subcommand."""
    import asyncio

    sys.path.insert(0, str(ROOT / "src"))
    from evoblue_video_mcp.application.handshake import verify_bridge_handshake

    env = dict(
        os.environ,
        EVOBLUE_ENGINE_PORT=str(engine.port),
        EVOBLUE_DATA_DIRECTORY=str(engine.workdir / "data"),
    )
    exe = engine.exe

    async def run():
        return await verify_bridge_handshake(str(exe), ["bridge"], env)

    result = asyncio.run(run())
    return result.ok, (result.error or "initialize + list_tools")


def verify(bundle, port, models_dir):
    from platformdirs import user_data_path

    if models_dir is not None:
        real_models = models_dir
    else:
        real_models = user_data_path("EvoBlue Video MCP", "EvoBlue") / "models"
    # The corrupt-model case needs a real provider installation to corrupt:
    # a fabricated stub is (correctly) treated as not-installed by the model
    # registry, so no load is attempted and the gate cannot be exercised.
    # Skip explicitly instead of crashing where no models exist (CI, fresh
    # machines); run with --models-dir on a real installation to enforce it.
    if not Path(real_models).is_dir():
        print("3. provider-failure isolation: SKIP (no models dir; pass --models-dir)")
        real_models = None

    work = Path(tempfile.mkdtemp(prefix="evoblue-verify-"))
    try:
        print("1. fresh-profile boot + shipped approval flags")
        engine = Engine(bundle, port, work / "fresh")
        engine.start()
        status, health = http_get(f"http://127.0.0.1:{port}/api/health")
        check(
            "health endpoint",
            status == 200 and health.get("status") == "ok",
            engine_log=engine.log_path,
        )
        # P7: a frozen exe boots production even without an explicit env, so
        # the data endpoints are token-gated; read the persisted token.
        fresh_token = (engine.workdir / "data" / "local_token")
        check(
            "token file created for frozen production boot",
            fresh_token.is_file() and fresh_token.read_text(encoding="utf-8").strip(),
        )
        status, models = http_get(
            f"http://127.0.0.1:{port}/api/models",
            token=fresh_token.read_text(encoding="utf-8").strip(),
        )
        items = models.get("items", []) if isinstance(models, dict) else []
        check("models list has 3 built-ins", status == 200 and len(items) == 3)
        by_id = {item["model_id"]: item for item in items}
        flags_ok = (
            by_id.get("sensevoice-small-int8", {}).get("formal_default") is True
            and by_id.get("zipformer-ctc-small-zh-int8", {}).get("formal_default") is False
        )
        check("approval flags ship in the bundle", flags_ok)
        engine.stop()

        print("2. production token behavior")
        engine2 = Engine(bundle, port, work / "prod")
        engine2.start(production=True)
        token_file = engine2.workdir / "data" / "local_token"
        token = token_file.read_text(encoding="utf-8").strip()
        check("token file created on first run", bool(token))
        status, _body = http_get(f"http://127.0.0.1:{port}/api/models")
        check("data endpoint rejects missing token", status == 401)
        status, _body = http_get(f"http://127.0.0.1:{port}/api/models", token=token)
        check("data endpoint accepts valid token", status == 200)
        engine2.stop()

        if real_models is not None:
            print("3. provider-failure isolation")
            corrupt_root = work / "corrupt-models"
            corrupt_root.mkdir(parents=True)
            copytree_ignore = shutil.ignore_patterns("*.tar.bz2", "*.wav", ".trash", ".downloads")
            shutil.copytree(real_models, corrupt_root / "models", ignore=copytree_ignore)
            bad_model = (
                corrupt_root
                / "models"
                / "sensevoice-small-int8"
                / "2024-07-17"
                / "model.int8.onnx"
            )
            bad_model.write_bytes(b"corrupt")
            engine3 = Engine(bundle, port, work / "isolated")
            engine3.start(models_dir=corrupt_root / "models", production=True)
            status, _health = http_get(f"http://127.0.0.1:{port}/api/health")
            check("engine stays healthy with a corrupt model", status == 200)
            check(
                "load failure logged with stable error code",
                engine3.log_contains("ASR_PROVIDER_LOAD_FAILED"),
            )
            engine3.stop()

        print("4. frozen bridge subcommand handshake")
        engine4 = Engine(bundle, port, work / "bridge")
        engine4.start(production=True)
        bridge_ok, bridge_detail = _bridge_handshake(engine4)
        check("bridge handshake ok", bridge_ok, detail=bridge_detail)
        engine4.stop()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Release verification for packaged bundles")
    parser.add_argument("bundle", help="onedir bundle dir from scripts/build_package.py")
    parser.add_argument(
        "--models-dir", default=None, help="models root to copy the corrupt case from"
    )
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args(argv)
    bundle = Path(args.bundle)
    models_dir = Path(args.models_dir) if args.models_dir else None
    verify(bundle, args.port, models_dir)
    print("release verification PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

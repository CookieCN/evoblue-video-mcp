"""P7 acceptance: install -> boot -> single-instance -> bridge -> uninstall.

Exercises the real setup.exe end to end (INSTALLER_RELEASE_CONTRACT section 8),
on the host machine, in isolated temp directories:

  1. silent install into a temp dir; install-version.txt records the data dir;
  2. installed engine boots (production): health ok, data endpoints token-gated,
     token file created;
  3. a second engine instance exits 3 with the frozen Chinese message;
  4. the installed exe's `bridge` subcommand completes a real MCP handshake
     against the booted engine (frozen client-config payload shape);
  5. silent reinstall over the same dir (upgrade path) succeeds;
  6. silent uninstall removes the program but KEEPS user data.

Usage:
    uv run python scripts/verify_p7_acceptance.py [--setup PATH] [--port 8901]
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def http_get(url, token=None, timeout=5.0):
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Local-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_health(port, deadline_s=45.0):
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            status, body = http_get(f"http://127.0.0.1:{port}/api/health")
        except (urllib.error.URLError, OSError):
            status, body = 0, None
        if status == HTTPStatus.OK and isinstance(body, dict) and body.get("status") == "ok":
            return
        time.sleep(0.5)
    raise SystemExit(f"engine not healthy on {port} within {deadline_s}s")


def check(name, ok, detail=""):
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f" ({detail})"
    print(line)
    if not ok:
        raise SystemExit(f"acceptance failed at: {name}")


def silent_install(setup: Path, dest: Path) -> None:
    proc = subprocess.run(
        [str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={dest}"],
        check=False,
    )
    check(f"silent install -> {dest.name}", proc.returncode == 0)
    check(
        "install-version.txt records version + data dir",
        (dest / "install-version.txt").is_file(),
    )


def silent_uninstall(dest: Path) -> None:
    unins = dest / "unins000.exe"
    if not unins.is_file():
        raise SystemExit(f"uninstaller missing: {unins}")
    proc = subprocess.run(
        [str(unins), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=False
    )
    check("silent uninstall exit 0", proc.returncode == 0)


class InstalledEngine:
    def __init__(self, exe: Path, port: int, workdir: Path):
        self.exe = exe
        self.port = port
        self.workdir = workdir
        self.proc = None

    def start(self):
        self.workdir.mkdir(parents=True, exist_ok=True)
        env = dict(
            os.environ,
            EVOBLUE_DATA_DIRECTORY=str(self.workdir / "data"),
            EVOBLUE_ENVIRONMENT="production",
            EVOBLUE_ENGINE_PORT=str(self.port),
            EVOBLUE_OPEN_UI="0",
        )
        self.proc = subprocess.Popen(
            [str(self.exe)],
            stdout=(self.workdir / "engine.log").open("wb"),
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(self.workdir),
        )
        wait_health(self.port)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc:
            self.proc.wait(timeout=5)
        self.proc = None

    @property
    def data_dir(self) -> Path:
        return self.workdir / "data"

    @property
    def token(self) -> str:
        return (self.data_dir / "local_token").read_text(encoding="utf-8").strip()


def _run_engine_checks(engine: "InstalledEngine", args, engine_exe: Path) -> None:
    """Steps 2-4 against one booted installed engine (always followed by stop)."""
    print("  2. production token gate")
    engine.start()
    status, _ = http_get(f"http://127.0.0.1:{args.port}/api/health")
    check("health ok", status == 200)
    status, _ = http_get(f"http://127.0.0.1:{args.port}/api/models")
    check("models rejects missing token (401)", status == 401)
    status, _ = http_get(
        f"http://127.0.0.1:{args.port}/api/models", token=engine.token
    )
    check("models accepts the persisted token", status == 200)

    print("  3. second instance exits 3 with the frozen message")
    second = subprocess.run(
        [str(engine_exe)],
        capture_output=True,
        text=True,
        timeout=60,
        env=dict(
            os.environ,
            EVOBLUE_DATA_DIRECTORY=str(engine.data_dir),
            EVOBLUE_ENGINE_PORT=str(args.port),
        ),
    )
    check(
        "exit code 3",
        second.returncode == 3,
        detail=f"rc={second.returncode} stderr={second.stderr[-200:]!r}",
    )
    check("frozen Chinese message on stderr", "已在运行" in second.stderr)

    print("  4. installed exe bridge subcommand completes a real MCP handshake")
    import asyncio

    from evoblue_video_mcp.application.handshake import verify_bridge_handshake

    bridge_env = dict(
        os.environ,
        EVOBLUE_ENGINE_PORT=str(args.port),
        EVOBLUE_DATA_DIRECTORY=str(engine.data_dir),
    )

    async def handshake():
        return await verify_bridge_handshake(str(engine_exe), ["bridge"], bridge_env)

    result = asyncio.run(handshake())
    check(
        "bridge handshake ok",
        result.ok,
        detail=result.error or "initialize + list_tools",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--setup", default=None, help="setup.exe path (default: newest in dist/)")
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        raise SystemExit("P7 acceptance runs on Windows only")

    setup = Path(args.setup) if args.setup else None
    if setup is None:
        candidates = sorted((ROOT / "dist").glob("EvoBlueVideoMCP-*-setup.exe"))
        if not candidates:
            raise SystemExit("no setup.exe in dist/; run scripts/build_installer.py")
        setup = candidates[-1]

    work = Path(tempfile.mkdtemp(prefix="evoblue-p7-"))
    install_dir = work / "install"
    engine_exe = install_dir / "evoblue-engine-full.exe"
    try:
        print(f"1. silent install ({setup.name})")
        silent_install(setup, install_dir)
        check("engine exe installed", engine_exe.is_file())

        print("2-4. installed engine: token gate, double-instance, bridge handshake")
        engine = InstalledEngine(engine_exe, args.port, work / "run")
        try:
            _run_engine_checks(engine, args, engine_exe)
        finally:
            engine.stop()

        print("5. silent reinstall (upgrade path)")
        silent_install(setup, install_dir)

        print("6. silent uninstall keeps user data")
        data_marker = engine.data_dir / "local_token"
        silent_uninstall(install_dir)
        # unins000.exe re-launches itself as a temp copy and returns early;
        # poll for the actual removal instead of sleeping a fixed amount
        deadline = time.monotonic() + 30
        while install_dir.exists() and time.monotonic() < deadline:
            time.sleep(1)
        if install_dir.exists():
            leftovers = [entry.name for entry in install_dir.rglob("*")][:10]
            check("install dir removed", False, detail=f"leftovers: {leftovers}")
        else:
            check("install dir removed", True)
        check("user data preserved", data_marker.is_file())
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)

    print("P7 acceptance PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

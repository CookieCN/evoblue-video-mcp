"""F0: engine file logging — contract drift, rotation, degrade, redaction, wiring.

Contract: docs/ENGINE_LOGGING.md (frozen 2026-09-10). The bridge-purity guard
here covers import-time side effects only; runtime stdout purity stays with
tests/integration/test_bridge_stdio.py.
"""

import json
import logging
import logging.config
import logging.handlers
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from evoblue_video_mcp.engine_logging import (
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    LOG_DIRNAME,
    LOG_FILENAME,
    engine_log_config,
    scrub_sensitive_text,
    setup_engine_file_logging,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOGGER_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Snapshot logger handlers/levels; restore (closing anything we added)."""
    snapshot = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).level)
        for name in _LOGGER_NAMES
    }
    yield
    for name, (handlers, level) in snapshot.items():
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            if handler not in handlers:
                handler.close()
        logger.handlers = handlers
        logger.setLevel(level)


def test_frozen_constants_match_contract() -> None:
    doc = (_REPO_ROOT / "docs" / "ENGINE_LOGGING.md").read_text(encoding="utf-8")
    block = re.search(r"```python\n(.*?)```", doc, re.DOTALL)
    assert block is not None, "contract must carry the frozen-constant block"
    namespace: dict[str, object] = {}
    # exec is safe here: the block is the project's own frozen contract text.
    exec(block.group(1), namespace)
    assert namespace["LOG_DIRNAME"] == LOG_DIRNAME
    assert namespace["LOG_FILENAME"] == LOG_FILENAME
    assert namespace["DEFAULT_MAX_BYTES"] == DEFAULT_MAX_BYTES
    assert namespace["DEFAULT_BACKUP_COUNT"] == DEFAULT_BACKUP_COUNT


def test_setup_creates_log_and_sink_writes(restore_logging: None, tmp_path: Path) -> None:
    log = setup_engine_file_logging(tmp_path)
    assert log is not None
    assert log.path == tmp_path / "logs" / "engine.log"
    assert log.path.is_file()
    logging.getLogger("evoblue_video_mcp.test").info("hello-log-line")
    log.handler.flush()
    assert "hello-log-line" in log.path.read_text(encoding="utf-8")


def test_rotation_keeps_bounded_file_set(restore_logging: None, tmp_path: Path) -> None:
    log = setup_engine_file_logging(tmp_path, max_bytes=200, backup_count=2)
    assert log is not None
    logger = logging.getLogger("evoblue_video_mcp.test")
    for i in range(60):
        logger.info("line-%03d-%s", i, "x" * 30)
    log.handler.flush()
    files = sorted(p.name for p in (tmp_path / "logs").iterdir())
    assert "engine.log" in files
    assert "engine.log.1" in files, "rotation must have happened past the first file"
    # 1 active + 2 backups is the whole allowed set.
    assert len(files) <= 3
    assert all(p.stat().st_size <= 400 for p in (tmp_path / "logs").iterdir())


def test_unwritable_logs_dir_degrades_to_none(tmp_path: Path) -> None:
    # A file where the logs directory should be: mkdir must fail -> no file
    # logging, and crucially no exception (engine must still start).
    (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
    assert setup_engine_file_logging(tmp_path) is None


def test_engine_log_path_being_directory_degrades_to_none(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "engine.log").mkdir()
    assert setup_engine_file_logging(tmp_path) is None


def test_scrub_sensitive_text_shapes() -> None:
    token = re.compile(re.escape("tok-" + "9" * 28))
    text = (
        "Authorization: Bearer abcdefghijklmnop sk-abcdef0123456789 "
        "tok-9999999999999999999999999999"
    )
    out = scrub_sensitive_text(text, [token])
    assert "abcdefghijklmnop" not in out
    assert "sk-abcdef0123456789" not in out
    assert "tok-9999" not in out
    assert "[REDACTED]" in out
    # Without a credential shape, nothing is touched.
    clean = scrub_sensitive_text("stage=downloading model=sensevoice-small http=200", [])
    assert clean == "stage=downloading model=sensevoice-small http=200"


def test_scrub_replaces_auth_header_to_end_of_line() -> None:
    # Contract section 5: the header value runs to end of line — unknown token
    # formats (e.g. JWTs) must go, even at the cost of same-line context.
    out = scrub_sensitive_text("authorization: Basic dXNlcjpwYXNz tail", [])
    assert out == "authorization: [REDACTED]"


def test_file_log_redacts_injected_secrets_incl_traceback(
    restore_logging: None, tmp_path: Path
) -> None:
    secret_token = "tok-" + "9" * 28
    log = setup_engine_file_logging(tmp_path, known_secrets=[secret_token])
    assert log is not None
    try:
        raise ValueError(
            "boom Authorization: Bearer abcdefghijklmnop sk-abcdef0123456789 "
            f"{secret_token}"
        )
    except ValueError:
        logging.getLogger("evoblue_video_mcp.test").exception("install failed")
    log.handler.flush()
    text = log.path.read_text(encoding="utf-8")
    assert "abcdefghijklmnop" not in text
    assert "sk-abcdef0123456789" not in text
    assert secret_token not in text
    assert "boom" in text, "the non-secret part of the message survives"
    assert "[REDACTED]" in text


def test_engine_log_config_keeps_stock_handlers_and_shares_one_instance(
    restore_logging: None, tmp_path: Path
) -> None:
    log = setup_engine_file_logging(tmp_path)
    assert log is not None
    config = engine_log_config(log)
    logging.config.dictConfig(config)
    root = logging.getLogger()
    assert log.handler in root.handlers
    assert any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ), "a stderr handler must stay on root"
    for name in ("uvicorn", "uvicorn.access"):
        handlers = logging.getLogger(name).handlers
        assert log.handler in handlers, f"{name} must share the same file handler"
        assert len([h for h in handlers if isinstance(h, logging.FileHandler)]) == 1
    # A uvicorn-logger record reaches the file through the shared instance.
    logging.getLogger("uvicorn.error").info("probe-uvicorn-record")
    log.handler.flush()
    assert "probe-uvicorn-record" in log.path.read_text(encoding="utf-8")


def test_uvicorn_config_applies_dict_log_config(restore_logging: None, tmp_path: Path) -> None:
    import uvicorn

    log = setup_engine_file_logging(tmp_path)
    assert log is not None
    uvicorn.Config(app=None, log_config=engine_log_config(log)).configure_logging()
    assert log.handler in logging.getLogger("uvicorn").handlers
    assert log.handler in logging.getLogger("uvicorn.access").handlers


def test_bridge_import_never_activates_file_logging(tmp_path: Path) -> None:
    code = (
        "import json, logging, logging.handlers, os, sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT / 'src')!r})\n"
        f"os.environ['EVOBLUE_DATA_DIRECTORY'] = {str(tmp_path)!r}\n"
        "import evoblue_video_mcp.mcp.__main__  # noqa: F401\n"
        "file_handlers = [\n"
        "    h for h in logging.getLogger().handlers\n"
        "    if isinstance(h, logging.handlers.FileHandler)\n"
        "]\n"
        "print(json.dumps({\n"
        f"    'logs_dir': os.path.isdir(os.path.join({str(tmp_path)!r}, {LOG_DIRNAME!r})),\n"
        "    'file_handlers': len(file_handlers),\n"
        "}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == {"logs_dir": False, "file_handlers": 0}

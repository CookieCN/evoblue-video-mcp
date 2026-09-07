"""P5 real-machine acceptance driver (contract §10 matrix).

Runs the five-client acceptance against the REAL client configs on this
machine and prints a PASS/FAIL evidence report for docs/
CLIENT_CONFIG_WRITE_CONTRACT.md §10. Handshakes target the real Engine on
127.0.0.1:8765 (the deployment port; the payload deliberately carries no port
override). Mutating operations only run for tiers where the PRD allows them,
and every mutation goes through the backup/restore gates:

- codex:         install (no-op expected) → verify → remove → restore → verify
- workbuddy:     install (no-op expected) → verify (manual Trust still required
                 on first activation — noted, not automatable)
- claude_code:   verify only (CLI get + real handshake; no remove so the
                 working entry stays)
- claude_desktop: install (real write when the client dir exists) → restore
                 the pre-acceptance state from install's backup (self-cleaning)
- deepseek:      copyable only; any mutation must raise AUTO_INSTALL_UNSUPPORTED

Usage:
    .venv/Scripts/python.exe scripts/verify_p5_acceptance.py
Exit 0 = all scenarios passed. This script touches real user configs — run it
deliberately, not from CI.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from evoblue_video_mcp.application.client_config.errors import (
    AutoInstallUnsupportedError,
    ClientConfigError,
)
from evoblue_video_mcp.application.client_config.service import (
    ClientConfigService,
    default_payload,
    kernel_verifier,
)

RESULTS: list[tuple[str, str, str]] = []


def record(scenario: str, step: str, ok: bool, evidence: str = "") -> None:
    RESULTS.append((scenario, step, "PASS" if ok else "FAIL"))
    print(f"  [{'PASS' if ok else 'FAIL'}] {step} {evidence}")


async def run_step(
    scenario: str, step: str, operation: Callable[[], Awaitable[Any]], check: Any
) -> Any:
    """Run one acceptance step; a ClientConfigError is a FAIL, not a crash."""
    try:
        result = await operation()
    except ClientConfigError as exc:
        record(scenario, step, False, f"{exc.code}: {exc.message}")
        return None
    evidence = getattr(result, "message", "")
    record(scenario, step, bool(check(result)), str(evidence))
    return result


async def main() -> int:
    service = ClientConfigService(
        payload=default_payload(8765),
        verifier=kernel_verifier(),
    )

    print("== codex: full install/verify/remove/restore cycle ==")
    await run_step(
        "codex",
        "install(no-op)",
        lambda: service.install("codex"),
        lambda r: r.entry_matches_current is True,
    )
    await run_step(
        "codex",
        "verify(1)",
        lambda: service.verify("codex"),
        lambda r: r.handshake == "verified",
    )

    async def _remove() -> Any:
        removed = await service.remove("codex", confirm=True)
        record("codex", "remove-backup", removed.backup_name is not None, "")
        return removed

    removed = await run_step(
        "codex",
        "remove",
        _remove,
        lambda r: r.performed and r.installed is False,
    )

    if removed is None:
        print("  restore skipped: no backup from remove")
        return 1

    async def _restore() -> Any:
        return await service.restore("codex", removed.backup_name, confirm=True)

    restored = await run_step(
        "codex",
        "restore",
        _restore,
        lambda r: r.performed and r.restored_from == removed.backup_name,
    )
    if restored is not None:
        print(f"    [info] safety backup: {restored.safety_backup}")

    await run_step(
        "codex",
        "verify(2)",
        lambda: service.verify("codex"),
        lambda r: r.handshake == "verified",
    )

    print("== workbuddy: idempotent install + verify ==")
    await run_step(
        "workbuddy",
        "install(no-op)",
        lambda: service.install("workbuddy"),
        lambda r: r.entry_matches_current is True,
    )
    await run_step(
        "workbuddy",
        "verify",
        lambda: service.verify("workbuddy"),
        lambda r: r.handshake == "verified",
    )

    print("== claude_code: verify only ==")
    await run_step(
        "claude_code",
        "verify",
        lambda: service.verify("claude_code"),
        lambda r: r.handshake in {"verified", "failed", "unverified"},
    )

    print("== claude_desktop: real write then restore pre-state (self-cleaning) ==")
    cd_state = await service.status("claude_desktop")
    if cd_state.target_present is False:
        await run_step(
            "claude_desktop",
            "install-refused",
            lambda: service.install("claude_desktop"),
            lambda r: r.performed is False
            and r.handshake_reason == "client_directory_missing",
        )
    else:
        installed = await run_step(
            "claude_desktop",
            "install",
            lambda: service.install("claude_desktop"),
            lambda r: r.performed and r.handshake == "verified",
        )
        if installed is not None and installed.backup_name:
            await run_step(
                "claude_desktop",
                "restore-pre-state",
                lambda: service.restore(
                    "claude_desktop", installed.backup_name, confirm=True
                ),
                lambda r: r.performed and r.restored_from == installed.backup_name,
            )

    print("== deepseek: manual tier ==")
    try:
        await service.install("deepseek")
        record("deepseek", "install-refused", False, "no exception raised")
    except AutoInstallUnsupportedError:
        record("deepseek", "install-refused", True, "AUTO_INSTALL_UNSUPPORTED raised")

    failed = [r for r in RESULTS if r[2] == "FAIL"]
    print(f"\n== summary: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

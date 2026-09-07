"""P5 structural gate: the client-config write surface has one write channel.

Mirrors ``test_repository_transaction_guards.py``: the gate audits the AST of
``application/client_config/`` for *structural* properties that make
「UI 不虚报成功」 enforceable rather than aspirational —

A. every filesystem write primitive is confined to ``writes.py``;
B. the file_auto adapter's install/remove/restore (and backup's create/restore)
   can only mutate state through the audited channel functions;
C. ``commit_atomic`` verifies what it wrote by reading the file back *after*
   the ``os.replace`` (AGENTS.md gotcha #7: 写入后必须回读实际字节验证).

Behavioral semantics live in the unit tests; this file only stops someone
from quietly adding a second write path. Anti-vacuity asserts keep the audit
honest if the module moves or renames.
"""

import ast
from pathlib import Path

_PACKAGE = Path(__file__).parents[2] / "src" / "evoblue_video_mcp" / "application" / "client_config"

#: Attribute names that mutate the filesystem no matter what the receiver is.
_PATH_MUTATORS = {"write_bytes", "write_text", "mkdir", "rmdir"}
#: ``os.<name>`` calls that mutate the filesystem.
_OS_MUTATORS = {"replace", "remove", "unlink", "rename"}


def _py_files() -> dict[str, Path]:
    return {path.name: path for path in sorted(_PACKAGE.rglob("*.py"))}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _call_name(call: ast.Call) -> str:
    """``module.func`` / ``func`` shape of the callee, best effort."""
    func = call.func
    if isinstance(func, ast.Attribute):
        base = getattr(func.value, "id", "")
        return f"{base}.{func.attr}" if base else func.attr
    return getattr(func, "id", "")


def _is_write_mode_open(call: ast.Call) -> bool:
    return any(
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value[0:1] in {"w", "a", "x", "+"}
        for arg in call.args
    )


def _primitive_calls(tree: ast.Module) -> list[ast.Call]:
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        base, _, attr = name.rpartition(".")
        is_primitive = (
            attr in _PATH_MUTATORS
            or (base == "os" and attr in _OS_MUTATORS)
            or (name == "open" and _is_write_mode_open(node))
        )
        if is_primitive:
            found.append(node)
    return found


def _top_level_functions(path: Path) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _called_names(node: ast.AST) -> set[str]:
    return {
        _call_name(sub).rpartition(".")[2]
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
    }


def test_write_primitives_are_confined_to_writes_module() -> None:
    files = _py_files()
    assert "writes.py" in files, "audit found no writes.py — did the module move?"
    offenders: list[str] = []
    channel_calls = 0
    for name, path in files.items():
        calls = _primitive_calls(_tree(path))
        if name == "writes.py":
            channel_calls += len(calls)
            continue
        if calls:
            offenders.append(f"{name}: {len(calls)} call(s)")
    assert channel_calls >= 1, "audit found no write primitives — did writes.py move?"
    assert not offenders, (
        "filesystem write primitives outside writes.py (the single write "
        f"channel, contract CLIENT_CONFIG_WRITE_CONTRACT §4/ADR 0005): {offenders}"
    )


def test_file_auto_operations_route_through_the_write_channel() -> None:
    files = _py_files()
    adapter = files.get("file_auto.py")
    writes = files.get("writes.py")
    backup = files.get("backup.py")
    assert adapter is not None and writes is not None and backup is not None, (
        "audit found no adapter/writes/backup modules — did the package move?"
    )
    channel_functions = set(_top_level_functions(writes)) | set(_top_level_functions(backup))
    module_functions = {
        node.name: node
        for node in ast.walk(_tree(adapter))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    methods = {
        name: node
        for name, node in module_functions.items()
        if name in {"install", "remove", "restore"}
    }
    assert set(methods) == {"install", "remove", "restore"}, (
        f"expected install/remove/restore on the file_auto adapter, found {sorted(methods)}"
    )

    def _transitive_names(node: ast.AST, visiting: set[str]) -> set[str]:
        """Call names reachable from ``node`` through module-local functions.

        Also follows ``self._helper`` references passed as values (the
        ``asyncio.to_thread(self._install_sync, ...)`` delegation shape).
        """
        names = _called_names(node)
        refs = {
            sub.attr
            for sub in ast.walk(node)
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "self"
        }
        for called in list(names | refs):
            target = module_functions.get(called)
            if target is not None and called not in visiting:
                visiting.add(called)
                names |= _transitive_names(target, visiting)
        return names

    unrouted = []
    for name in ("install", "remove", "restore"):
        reachable = _transitive_names(methods[name], {name})
        if not (reachable & channel_functions):
            unrouted.append(name)
    assert not unrouted, (
        "file_auto adapter operations bypassing the write channel "
        f"(no call path into writes.py/backup.py functions "
        f"{sorted(channel_functions)}): {unrouted}"
    )


def test_backup_module_routes_through_the_write_channel() -> None:
    files = _py_files()
    backup = files.get("backup.py")
    writes = files.get("writes.py")
    assert backup is not None and writes is not None, "audit found no backup/writes modules"
    channel_functions = set(_top_level_functions(writes))
    unbacked = [
        name
        for name in ("create_backup", "restore")
        if name in _top_level_functions(backup)
        and not (_called_names(_top_level_functions(backup)[name]) & channel_functions)
    ]
    # Anti-vacuity: both entry points must exist for the audit to mean anything.
    assert {"create_backup", "restore"} <= set(_top_level_functions(backup)), (
        "audit found no create_backup/restore in backup.py — did the module move?"
    )
    assert not unbacked, f"backup operations bypassing writes.py: {unbacked}"


def test_commit_atomic_verifies_by_reading_back_after_replace() -> None:
    files = _py_files()
    writes = files.get("writes.py")
    assert writes is not None, "audit found no writes.py — did the module move?"
    functions = _top_level_functions(writes)
    assert "commit_atomic" in functions, "audit found no commit_atomic — did writes.py move?"
    body = functions["commit_atomic"]
    replace_lines = [
        node.lineno
        for node in ast.walk(body)
        if isinstance(node, ast.Call) and _call_name(node) == "os.replace"
    ]
    read_lines = [
        node.lineno
        for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and _call_name(node).rpartition(".")[2] in {"read_bytes", "read_text", "read"}
    ]
    assert replace_lines, "commit_atomic does not commit via os.replace"
    # The verification read must happen textually after the replace — that is
    # the whole point of reading the written bytes back (AGENTS.md #7).
    readback = [line for line in read_lines if line > min(replace_lines)]
    assert readback, (
        "commit_atomic never reads the file back after os.replace — "
        "post-write verification (AGENTS.md gotcha #7) is missing"
    )


def test_remove_atomic_verifies_absence_after_removal() -> None:
    files = _py_files()
    writes = files.get("writes.py")
    assert writes is not None, "audit found no writes.py"
    functions = _top_level_functions(writes)
    assert "remove_atomic" in functions, "audit found no remove_atomic — did writes.py move?"
    body = functions["remove_atomic"]
    names = _called_names(body)
    assert {"unlink", "remove"} & names, "remove_atomic does not unlink the target"
    assert names & {"exists", "is_file"}, (
        "remove_atomic never verifies absence after unlink — post-write verification missing"
    )

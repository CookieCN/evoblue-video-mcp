"""P3-007 review: structural gate for the §6 BEGIN IMMEDIATE discipline.

Every self-committing (transaction-owning) async writer in
``storage/repository.py`` MUST call ``_begin_immediate`` BEFORE its first
database operation — a deferred read snapshot followed by a write dies with
an unretryable BUSY_SNAPSHOT when another writer commits in between. The
gate checks the AST CALL NODE and its POSITION (first statement after the
docstring), not merely "somewhere in the body" and never docstring text.
The guard's runtime semantics are covered behaviorally in
``tests/unit/test_db_immediate_transaction.py``.
"""

import ast
from pathlib import Path

REPOSITORY = (
    Path(__file__).parents[2]
    / "src"
    / "evoblue_video_mcp"
    / "storage"
    / "repository.py"
)

EXEMPT = {"_begin_immediate"}


def _module_functions() -> dict[str, ast.AsyncFunctionDef]:
    tree = ast.parse(REPOSITORY.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
    }


def _called(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name:
                names.add(name)
    return names


def _first_statement(fn: ast.AsyncFunctionDef) -> ast.stmt:
    """First statement after the (optional) docstring."""
    body = fn.body
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1]
    return body[0]


def test_every_self_committing_writer_guards_first() -> None:
    functions = _module_functions()
    self_committing = {
        name
        for name, node in functions.items()
        if "commit" in _called(node) and name not in EXEMPT
    }
    assert self_committing, "audit found no writers — did the module move?"
    offenders = []
    for name in sorted(self_committing):
        first = _first_statement(functions[name])
        is_guard_call = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Await)
            and isinstance(first.value.value, ast.Call)
            and getattr(first.value.value.func, "id", "") == "_begin_immediate"
        )
        if not is_guard_call:
            offenders.append(name)
    assert not offenders, (
        "self-committing repository writers whose FIRST statement is not "
        f"`await _begin_immediate(session)` (the guard must precede every "
        f"read/write — BUSY_SNAPSHOT risk, docs/INDEX_REBUILD.md §6): "
        f"{offenders}"
    )


def test_guard_delegates_to_the_shared_semantics() -> None:
    """The guard must delegate to ``db.ensure_immediate_transaction`` (single
    source of truth for the transaction-state semantics); its own body must
    contain no transaction logic that could drift."""
    fn = _module_functions()["_begin_immediate"]
    first = _first_statement(fn)
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Await)
    call = first.value.value
    assert isinstance(call, ast.Call)
    assert getattr(call.func, "id", "") == "ensure_immediate_transaction"
    assert fn.body[-1] is first  # the delegation is the ENTIRE body

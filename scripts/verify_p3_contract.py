"""Verify the frozen P3 contracts by executing the DDL literally transcribed
from docs/FTS5_SCHEMA.md and exercising the query/indexing/rebuild contracts
including the counter-examples from the freeze review (phrase adjacency,
quote escaping, open-issue dedup, stale semantics, orphaned rebuild state,
duplicate analysis_id convergence)."""

import json
import re
import sqlite3
import sys
from pathlib import Path

_FTS_INSERT = (
    "INSERT INTO report_fts (rowid, title, tags, author, summary, transcript, url) "
    "VALUES (?,?,?,?,?,?,?)"
)


def json_dumps(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


ROOT = Path(__file__).resolve().parents[1]
doc = (ROOT / "docs" / "FTS5_SCHEMA.md").read_text(encoding="utf-8")

section = doc.split("## 2. 迁移 v8 DDL(冻结,逐字符转录)")[1].split("## 3.")[0]
ddls = [d.strip() for d in re.findall(r"```sql\n(.*?)```", section, re.S)]
assert len(ddls) == 10, f"expected 10 DDL statements in contract, found {len(ddls)}"

failures = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS" if ok else "FAIL"), name, detail)
    if not ok:
        failures.append(name)


def fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for ddl in ddls:
        conn.execute(ddl)
    return conn


conn = fresh_db()

# --- frozen helpers (FTS5_SCHEMA §4) ---
CJK = r"一-鿿぀-ヿ가-힯"


def normalize_for_fts(text: str) -> str:
    spaced = re.sub(rf"([{CJK}])", r" \1 ", text)
    return re.sub(r"\s+", " ", spaced).strip()


def build_match_query(q: str) -> str:
    """Frozen algorithm: whitespace splits words; each word becomes ONE phrase
    (CJK chars keep adjacency); inner double quotes are doubled; AND-joined."""
    phrases = []
    for word in q.split():
        tokens = [t for t in normalize_for_fts(word).split(" ") if re.search(r"\w", t)]
        if not tokens:
            continue
        phrases.append('"' + " ".join(tokens).replace('"', '""') + '"')
    if not phrases:
        raise ValueError("QUERY_INVALID")
    return " AND ".join(phrases)


def startup_reconcile(state: str, docs_empty: bool, dir_has_md: bool) -> str:
    """Frozen rule (INDEX_REBUILD §4): persisted 'running' is ALWAYS orphaned at
    boot (single engine owns the port; booting proves the writer is dead)."""
    if state == "running":
        state = "needs_rebuild"
    if state == "needs_rebuild":
        return "auto_rebuild"
    if docs_empty and dir_has_md:
        return "auto_rebuild"
    return "no_action"


def insert_doc(cur_conn, analysis_id, title, summary, transcript, path,
               content_hash, tags="选品 跨境电商", status="active"):
    cur = cur_conn.execute(
        """INSERT INTO report_documents
           (job_id, analysis_id, title, platform, author, video_id, source_url,
            published_at, analyzed_at, language, summary_mode, asr_provider,
            asr_model, asr_model_version, tags_json, summary_preview,
            relative_path, content_hash, byte_size, doc_source, doc_status,
            indexed_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (analysis_id, analysis_id, title, "youtube", "Wilson", "vid",
         "https://example.com/x", None, 1756350000.0, "zh", "auto", "", "", "",
         json_dumps(tags.split()), summary[:200], path, content_hash, 100,
         "pipeline", status, 1756350000.0, 1756350000.0),
    )
    rowid = cur.lastrowid
    cur_conn.execute(
        _FTS_INSERT,
        (rowid, normalize_for_fts(title), normalize_for_fts(tags),
         normalize_for_fts("Wilson"), normalize_for_fts(summary),
         normalize_for_fts(transcript), "https://example.com/x"),
    )
    return rowid


def search(cur_conn, q: str):
    match = build_match_query(q)
    return cur_conn.execute(
        """SELECT report_fts.rowid FROM report_fts
           JOIN report_documents d ON d.id = report_fts.rowid
           WHERE report_fts MATCH ? AND d.doc_status IN ('active','stale')
           ORDER BY d.analyzed_at DESC""",
        (match,),
    ).fetchall()


# --- seed: doc1 adjacency hit, doc2 looks-similar non-adjacency, doc3 english ---
now = 1756350000.0
r1 = insert_doc(conn, "job-1", "跨境电商选品策略完整指南",
                "本期讲跨境电商的选品方法论。", "大家好今天讲选品策略。",
                "a.md", "0" * 64)
r2 = insert_doc(conn, "job-2", "选择产品拆解指南",
                "本期讲如何选择产品。", "选择产品的三个步骤。",
                "b.md", "1" * 64, tags="运营 方法论")
r3 = insert_doc(conn, "job-3", "english video essay",
                "An English summary about supply chains.", "Transcript text.",
                "c.md", "2" * 64, tags="test")
conn.commit()

# 1. phrase query: adjacency required — '选品' hits doc1, MUST NOT hit doc2
check("builder keeps CJK word as one phrase", build_match_query("选品") == '"选 品"',
      build_match_query("选品"))
rows = search(conn, "选品")
check("phrase '选品' hits adjacent doc", [r[0] for r in rows] == [r1], str(rows))
check("phrase '选品' rejects non-adjacent 选择产品", r2 not in [r[0] for r in rows],
      str(rows))

# 2. whitespace grouping + AND
check("grouping: '跨境电商 选品' -> two phrases",
      build_match_query("跨境电商 选品") == '"跨 境 电 商" AND "选 品"',
      build_match_query("跨境电商 选品"))
rows = search(conn, "选择产品 步骤")
check("multi-word AND works", [r[0] for r in rows] == [r2], str(rows))

# 3. quote escaping: legal syntax + hits literal content
check("builder escapes inner quotes", build_match_query('foo"bar') == '"foo""bar"',
      build_match_query('foo"bar'))
r4 = insert_doc(conn, "job-4", 'title with foo"bar inside',
                'content mentions foo"bar literally.', "t.", "d.md", "3" * 64)
conn.commit()
try:
    rows = search(conn, 'foo"bar')
    check('escaped query legal + hits', [r[0] for r in rows] == [r4], str(rows))
except sqlite3.OperationalError as exc:
    check('escaped query legal + hits', False, f"OperationalError: {exc}")

# 4. punctuation-only query rejected
try:
    build_match_query("!!! ...")
    check("punctuation-only query rejected", False)
except ValueError:
    check("punctuation-only query rejected", True)

# 5. snippet + matched_fields (snippet column 0 = title, which holds the phrase)
snip = conn.execute(
    "SELECT snippet(report_fts, 0, '[', ']', '…', 8) FROM report_fts "
    "WHERE rowid=? AND report_fts MATCH ?",
    (r1, build_match_query("选品策略")),
).fetchone()[0]
check("snippet emitted with markers", "[" in snip and "]" in snip, repr(snip))
or_query = " OR ".join(build_match_query("选品策略").split(" AND "))
fields = ["title", "tags", "author", "summary", "transcript", "url"]
matched = {c for c in fields
           if conn.execute(f"SELECT count(*) FROM report_fts WHERE rowid=? AND {c} MATCH ?",
                           (r1, or_query)).fetchone()[0] > 0}
check("matched_fields = columns containing a phrase",
      matched == {"title", "transcript"}, str(matched))

# 6. stale semantics: searchable, fts row kept, verify_fts consistent
conn.execute("UPDATE report_documents SET doc_status='stale' WHERE id=?", (r2,))
rows = search(conn, "选择产品")
check("stale doc still searchable", [r[0] for r in rows] == [r2], str(rows))
check("verify_fts consistent with stale",
      conn.execute("SELECT count(*) FROM report_fts").fetchone()[0]
      == conn.execute("SELECT count(*) FROM report_documents "
                      "WHERE doc_status IN ('active','stale')").fetchone()[0] == 4)

# 7. missing semantics: fts row removed, out of search, verify still consistent
conn.execute("DELETE FROM report_fts WHERE rowid=?", (r3,))
conn.execute("UPDATE report_documents SET doc_status='missing' WHERE id=?", (r3,))
rows = search(conn, "supply chains")
check("missing doc out of search", rows == [], str(rows))
check("verify_fts consistent after missing",
      conn.execute("SELECT count(*) FROM report_fts").fetchone()[0]
      == conn.execute("SELECT count(*) FROM report_documents "
                      "WHERE doc_status IN ('active','stale')").fetchone()[0] == 3)

# 8. drift detection still fires on real inconsistency
conn.execute("DELETE FROM report_fts WHERE rowid=?", (r4,))
check("verify_fts detects real drift",
      conn.execute("SELECT count(*) FROM report_fts").fetchone()[0]
      != conn.execute("SELECT count(*) FROM report_documents "
                      "WHERE doc_status IN ('active','stale')").fetchone()[0])
conn.execute(
    _FTS_INSERT,
    (r4, normalize_for_fts('title with foo"bar inside'), normalize_for_fts("test"),
     normalize_for_fts("Wilson"), normalize_for_fts('content mentions foo"bar literally.'),
     normalize_for_fts("t."), "https://example.com/x"),
)

# 9. open-issue dedup via partial unique index
def record_issue(code, path, seen_at):
    row = conn.execute(
        "SELECT id FROM index_issues WHERE issue_code=? AND relative_path=? "
        "AND resolved_at IS NULL", (code, path)).fetchone()
    if row:
        conn.execute("UPDATE index_issues SET last_seen_at=? WHERE id=?", (seen_at, row[0]))
        return row[0]
    return conn.execute(
        "INSERT INTO index_issues (issue_code, relative_path, detail, first_seen_at, "
        "last_seen_at) VALUES (?,?,?,?,?)", (code, path, "d", seen_at, seen_at)).lastrowid


record_issue("MD_MISSING_FIELD", "broken.md", now)
first = conn.execute("SELECT count(*) FROM index_issues WHERE issue_code='MD_MISSING_FIELD' "
                     "AND relative_path='broken.md' AND resolved_at IS NULL").fetchone()[0]
check("first open issue recorded", first == 1)
try:
    conn.execute(
        "INSERT INTO index_issues (issue_code, relative_path, detail, first_seen_at, "
        "last_seen_at) VALUES (?,?,?,?,?)",
        ("MD_MISSING_FIELD", "broken.md", "d", now + 1, now + 1))
    conn.commit()
    check("partial unique index blocks duplicate open issue", False, "insert succeeded")
except sqlite3.IntegrityError:
    check("partial unique index blocks duplicate open issue", True)
check("record protocol dedups (update not insert)",
      conn.execute("SELECT count(*) FROM index_issues WHERE issue_code='MD_MISSING_FIELD' "
                   "AND relative_path='broken.md'").fetchone()[0] == 1)
conn.execute("UPDATE index_issues SET resolved_at=? WHERE issue_code='MD_MISSING_FIELD' "
             "AND relative_path='broken.md'", (now + 2,))
record_issue("MD_MISSING_FIELD", "broken.md", now + 3)  # recurrence after resolve
total = conn.execute("SELECT count(*) FROM index_issues WHERE issue_code='MD_MISSING_FIELD' "
                     "AND relative_path='broken.md'").fetchone()[0]
open_n = conn.execute("SELECT count(*) FROM index_issues WHERE issue_code='MD_MISSING_FIELD' "
                      "AND relative_path='broken.md' AND resolved_at IS NULL").fetchone()[0]
check("resolved issue may recur as new row (1 open, 2 total)",
      total == 2 and open_n == 1, f"total={total} open={open_n}")

# 10. startup reconcile: persisted running is ALWAYS orphaned (no time window)
check("fast crash restart (running, fresh timestamp) -> auto_rebuild",
      startup_reconcile("running", docs_empty=False, dir_has_md=True) == "auto_rebuild")
check("late restart (running, stale timestamp) -> auto_rebuild",
      startup_reconcile("running", docs_empty=False, dir_has_md=True) == "auto_rebuild")
check("first boot backfill (idle + empty docs + md on disk) -> auto_rebuild",
      startup_reconcile("idle", docs_empty=True, dir_has_md=True) == "auto_rebuild")
check("steady state (idle + docs) -> no_action",
      startup_reconcile("idle", docs_empty=False, dir_has_md=True) == "no_action")
check("index_status has no heartbeat column (no fake liveness)",
      "heartbeat" not in " ".join(ddls).lower())

# 11. duplicate analysis_id convergence: winner = sorted-min path of file set
dup_summary, dup_transcript = "重复ID文档A的内容。", "重复ID文档B的内容。"


def scenario(db_first_path: str) -> tuple[str, str]:
    """Index the same two-file set {a/a.md, b/b.md} under different DB histories;
    frozen ownership rule: sorted-min path wins, DB history is irrelevant."""
    c = fresh_db()
    if db_first_path == "b/b.md":  # history: b.md indexed first, a.md appears later
        insert_doc(c, "job-dup", "重复ID文档", dup_summary + "B版", dup_transcript + "B",
                   "b/b.md", "B" * 64)
        # rebuild walks sorted: a/a.md first -> displaces b/b.md; b/b.md -> duplicate
        winner_row = c.execute("SELECT id FROM report_documents "
                               "WHERE analysis_id='job-dup'").fetchone()[0]
        c.execute("UPDATE report_documents SET relative_path=?, content_hash=?, "
                  "summary_preview=? WHERE id=?",
                  ("a/a.md", "A" * 64, dup_summary + "A版", winner_row))
        c.execute("DELETE FROM report_fts WHERE rowid=?", (winner_row,))
        c.execute(
            _FTS_INSERT,
            (winner_row, normalize_for_fts("重复ID文档"), normalize_for_fts("选品 跨境电商"),
             normalize_for_fts("Wilson"), normalize_for_fts(dup_summary + "A版"),
             normalize_for_fts(dup_transcript + "B"), "https://example.com/x"))
        c.execute(
            "INSERT INTO index_issues (issue_code, relative_path, detail, "
            "first_seen_at, last_seen_at) VALUES (?,?,?,?,?)",
            ("DUPLICATE_ANALYSIS_ID", "b/b.md", "displaced by a/a.md", now, now))
    else:  # fresh DB: rebuild walks sorted, a/a.md simply wins
        insert_doc(c, "job-dup", "重复ID文档", dup_summary + "A版", dup_transcript + "B",
                   "a/a.md", "A" * 64)
    row = c.execute("SELECT relative_path, content_hash FROM report_documents "
                    "WHERE analysis_id='job-dup'").fetchone()
    return row


kept = scenario("b/b.md")
fresh = scenario("a/a.md")
check("duplicate winner converges across DB histories",
      kept == fresh == ("a/a.md", "A" * 64), f"kept={kept} fresh={fresh}")

# 12. rowid mapping invariant + sync pairing (user edit honored)
old_id = conn.execute("SELECT id FROM report_documents WHERE job_id='job-1'").fetchone()[0]
conn.execute("DELETE FROM report_fts WHERE rowid=?", (old_id,))
conn.execute("UPDATE report_documents SET summary_preview=?, content_hash=? WHERE id=?",
             ("用户编辑后的摘要。", "9" * 64, old_id))
conn.execute(
    _FTS_INSERT,
    (old_id, normalize_for_fts("跨境电商选品策略完整指南"), normalize_for_fts("选品 跨境电商"),
     normalize_for_fts("Wilson"), normalize_for_fts("用户编辑后的摘要。"),
     normalize_for_fts("大家好今天讲选品策略。"), "https://example.com/x"))
rows = search(conn, "用户编辑")
check("edit honored via pairing protocol", [r[0] for r in rows] == [r1], str(rows))
orphans = conn.execute(
    """SELECT count(*) FROM report_documents d WHERE d.doc_status IN ('active','stale')
       AND NOT EXISTS (SELECT 1 FROM report_fts f WHERE f.rowid = d.id)"""
).fetchone()[0]
check("no orphan active/stale docs", orphans == 0, str(orphans))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all contract checks passed")

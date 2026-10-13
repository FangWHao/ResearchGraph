"""整项目清除的内存归属清单；只保存行号，不复制正文。"""

from __future__ import annotations

import sqlite3

from rg.store.ownership import relation
from rg.store.ownership_schema import OWNED_TABLES

TABLES = frozenset(
    """
artifact_discoveries artifact_discovery_attempts artifact_job_events artifact_jobs
artifact_observations artifact_versions candidate_locations claim_evidence claims coverage
claude_chain_records daily_usage decision_requests decision_resolutions dedupe_links edit_records
edit_request_checks claim_source_checks rule_reference_checks
entities fragment_assignments fragment_partitions fragment_record_maps
event_search evidence_spans explicit_records note_requests extraction_plans extraction_queue
extraction_queue_events extraction_runs file_hash_cache graph_clock hook_error_checks
hook_error_reports hook_error_sources ingest_sources jobs
l1_derivations l1_path_checks l1_repair_versions link_progress message_fingerprints
model_attempts parser_records pipeline_queue
pipeline_queue_events project_context_observations project_privacy projects raw_events
remote_previews review_actions run_io
run_manifests run_observations runs segment_coverage session_parent_observations session_results
sessions slim_events
source_files source_roots source_root_observations source_record_skips source_continuations
science_migrations science_ownership source_project_history project_tombstones
spool_receipts token_cache view_states workspace_snapshots
worktree_follow_actions worktree_discovery_observations
worktree_discovery_progress worktree_discovery_cursors
project_assignment_actions automatic_assignments
""".split()
)
POINTERS = {
    ("sessions", "parent_session_pk"),
    ("sessions", "physical_session_pk"),
    ("raw_events", "alias_of"),
    ("artifact_observations", "cached_from"),
}
OBJECTS = {
    "raw_events": "object_sha256",
    "spool_receipts": "object_sha256",
    "workspace_snapshots": "record_sha256",
    "artifact_versions": "content_sha256",
    "edit_records": "patch_sha256",
}


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class Selection:
    def __init__(self, db: sqlite3.Connection, project: str):
        self.db, self.project = db, project
        actual = {
            r[1]
            for r in db.execute("PRAGMA main.table_list")
            if r[2] == "table" and not r[1].startswith("sqlite_")
        }
        if actual != TABLES:
            raise ValueError("数据库表与清除合同不同；请升级清除实现后重试")
        self.columns = {
            t: list(db.execute("SELECT * FROM pragma_table_info(?)", (t,))) for t in TABLES
        }
        self.keys = {
            t: next(r[1] for r in rows if r[5] == 1)
            for t, rows in self.columns.items()
            if any(r[5] == 1 for r in rows)
        }
        self.relations = {t: relation(db, t) for t in TABLES}
        self.foreign = [
            (t, r[3], r[2], r[4] or self.keys[r[2]])
            for t in TABLES
            for r in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (t,))
        ]
        if db.execute("PRAGMA user_version").fetchone()[0] >= 34:
            self.foreign.append(("raw_events", "alias_of", "raw_events", "event_id"))
        db.execute("DROP TABLE IF EXISTS temp.clear_rows")
        db.execute("CREATE TEMP TABLE clear_rows(t TEXT,n INTEGER,PRIMARY KEY(t,n))")
        for table, cols in self.columns.items():
            if table in {
                "science_migrations",
                "science_ownership",
                "source_project_history",
                "project_tombstones",
            }:
                continue
            if any(c[1] == "project_id" for c in cols):
                self.add(
                    table,
                    f"SELECT rowid FROM {self.relations[table]} WHERE project_id=?",
                    (project,),
                )
        self.add(
            "raw_events",
            "SELECT r.rowid AS n FROM raw_events r JOIN effective_raw_events e USING(event_id) "
            "JOIN sessions s ON s.session_pk=e.session_pk WHERE s.project_id=?",
            (project,),
        )
        # 全局计数缓存没有原文或项目归属，清除其摘要，避免留下项目输入指纹。
        self.add("token_cache", "SELECT rowid FROM token_cache")

    def add(self, table: str, query: str, args: tuple = ()) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO temp.clear_rows SELECT ?,n FROM ("
            + query.replace("SELECT rowid", "SELECT rowid AS n", 1)
            + ")",
            (table, *args),
        )

    def selected(self, table: str, alias: str = "c") -> str:
        return f"{alias}.rowid IN (SELECT n FROM temp.clear_rows WHERE t='{table}')"

    def join(self, child: str, column: str, parent: str, key: str) -> None:
        if child in OWNED_TABLES and parent in {"projects", "source_roots"}:
            return
        self.add(
            child,
            f"SELECT c.rowid AS n FROM {self.relations[child]} c "
            f"JOIN {self.relations[parent]} p ON c.{identifier(column)}=p.{identifier(key)} "
            f"JOIN temp.clear_rows s ON s.t=? AND s.n=p.rowid",
            (parent,),
        )

    def expand(self) -> None:
        while True:
            before = self.db.total_changes
            for child, column, parent, key in self.foreign:
                if (child, column) not in POINTERS:
                    self.join(child, column, parent, key)
            for child, col, parent, key in [
                ("runs", "session_pk", "sessions", "session_pk"),
                ("workspace_snapshots", "session_pk", "sessions", "session_pk"),
                ("workspace_snapshots", "root_id", "source_roots", "root_id"),
                ("run_io", "run_id", "runs", "run_id"),
                ("run_io", "version_id", "artifact_versions", "version_id"),
                ("run_io", "requested_version_id", "artifact_versions", "version_id"),
                ("claims", "claim_id", "claim_evidence", "claim_id"),
                ("extraction_runs", "extraction_run_id", "claims", "extraction_run_id"),
                ("extraction_runs", "extraction_run_id", "model_attempts", "extraction_run_id"),
                (
                    "extraction_runs",
                    "extraction_run_id",
                    "candidate_locations",
                    "extraction_run_id",
                ),
                ("extraction_runs", "extraction_run_id", "link_progress", "extraction_run_id"),
                ("jobs", "job_id", "link_progress", "review_job_id"),
                ("jobs", "job_id", "spool_receipts", "job_id"),
            ]:
                self.join(child, col, parent, key)
            self.add(
                "extraction_runs",
                "SELECT c.rowid AS n FROM extraction_runs c "
                "JOIN json_each(CASE WHEN json_valid(c.input_event_ids) "
                "THEN c.input_event_ids ELSE '[]' END) j "
                "JOIN raw_events p ON p.event_id=j.value "
                "JOIN temp.clear_rows s ON s.t='raw_events' AND s.n=p.rowid",
            )
            self.add(
                "extraction_runs",
                "SELECT c.rowid AS n FROM extraction_runs c "
                "JOIN json_each(CASE WHEN json_valid(c.working_set_ids) "
                "THEN c.working_set_ids ELSE '[]' END) j "
                "JOIN entities p ON p.entity_id=j.value "
                "JOIN temp.clear_rows s ON s.t='entities' AND s.n=p.rowid",
            )
            self.add(
                "jobs",
                f"SELECT c.rowid AS n FROM {self.relations['jobs']} c WHERE "
                "json_valid(c.payload) AND (json_extract(c.payload,'$.project_id')=? OR "
                "json_extract(c.payload,'$.session_pk') IN (SELECT session_pk FROM sessions p "
                "JOIN temp.clear_rows s ON s.t='sessions' AND s.n=p.rowid) OR "
                "json_extract(c.payload,'$.segment_id') IN (SELECT segment_id "
                "FROM extraction_plans p JOIN temp.clear_rows s "
                "ON s.t='extraction_plans' AND s.n=p.rowid))",
                (self.project,),
            )
            for column in ("event_ids", "context_event_ids", "entity_ids"):
                parent, key = (
                    ("entities", "entity_id")
                    if column == "entity_ids"
                    else ("raw_events", "event_id")
                )
                self.add(
                    "jobs",
                    "SELECT c.rowid AS n FROM jobs c "
                    "JOIN json_each(CASE WHEN json_valid(c.payload) "
                    "THEN json_extract(c.payload,?) ELSE '[]' END) j "
                    f"JOIN {parent} p ON p.{key}=j.value "
                    "JOIN temp.clear_rows s ON s.t=? AND s.n=p.rowid",
                    ("$." + column, parent),
                )
            # 最后一部分也已选中时，清除空的物理容器与游标；共享来源保留。
            self.add(
                "sessions",
                "SELECT c.rowid AS n FROM sessions c WHERE c.project_id IS NULL "
                "AND c.fragment_id LIKE 'privacy:%' AND c.physical_session_pk IN ("
                "SELECT r.session_pk FROM raw_events r JOIN temp.clear_rows x "
                "ON x.t='raw_events' AND x.n=r.rowid) AND NOT EXISTS ("
                "SELECT 1 FROM effective_raw_events r WHERE r.session_pk=c.session_pk "
                "AND r.event_id NOT IN (SELECT n FROM temp.clear_rows WHERE t='raw_events'))",
            )
            self.add(
                "sessions",
                "SELECT c.rowid AS n FROM sessions c WHERE c.project_basis='fragment_container' "
                "AND NOT EXISTS (SELECT 1 FROM raw_events r WHERE r.session_pk=c.session_pk "
                "AND r.event_id NOT IN (SELECT n FROM temp.clear_rows WHERE t='raw_events')) "
                "AND NOT EXISTS (SELECT 1 FROM sessions s WHERE s.physical_session_pk=c.session_pk "
                "AND s.session_pk NOT IN (SELECT n FROM temp.clear_rows WHERE t='sessions'))",
            )
            if self.db.total_changes == before:
                break
        self._ownership()
        self._retain_context()

    def _ownership(self) -> None:
        for table in (*OWNED_TABLES, "raw_events"):
            self.add(
                "science_ownership",
                "SELECT o.rowid AS n FROM science_ownership o "
                "JOIN temp.clear_rows s ON s.t=o.table_name AND "
                "s.n=o.record_rowid WHERE o.table_name=?",
                (table,),
            )
        self.add(
            "science_migrations",
            "SELECT c.rowid AS n FROM science_migrations c WHERE NOT EXISTS "
            "(SELECT 1 FROM science_ownership o WHERE o.migration_id=c.migration_id AND "
            "o.rowid NOT IN (SELECT n FROM temp.clear_rows WHERE t='science_ownership'))",
        )

    def _retain_context(self) -> None:
        # 科学行的原外键仍证明存储来源；外项目正在使用的来源身份不能删。
        for child, column, parent, key in self.foreign + [
            ("workspace_snapshots", "root_id", "source_roots", "root_id"),
        ]:
            if parent not in {"sessions", "source_roots", "projects"}:
                continue
            self.db.execute(
                "DELETE FROM temp.clear_rows WHERE t=? AND n IN ("
                f"SELECT p.rowid FROM {identifier(parent)} p JOIN {identifier(child)} c "
                f"ON c.{identifier(column)}=p.{identifier(key)} WHERE NOT "
                f"({self.selected(child)}))",
                (parent,),
            )
        # 借用的科学事实仍需完整根身份历史；随保留根一起保留，不留半份证明。
        self.db.execute(
            "DELETE FROM temp.clear_rows WHERE t='source_root_observations' AND n IN ("
            "SELECT o.rowid FROM source_root_observations o JOIN source_roots r USING(root_id) "
            "WHERE NOT (" + self.selected("source_roots", "r") + "))"
        )
        # 保留根或会话可能刚移除；再检查项目的物理来源外键。
        for child, column, parent, key in self.foreign:
            if parent == "projects":
                self.db.execute(
                    "DELETE FROM temp.clear_rows WHERE t='projects' AND n IN ("
                    f"SELECT p.rowid FROM projects p JOIN {identifier(child)} c ON "
                    f"c.{identifier(column)}=p.{identifier(key)} WHERE NOT "
                    f"({self.selected(child)}))"
                )

    def blockers(self) -> list[str]:
        issues = []
        for table, cols in self.columns.items():
            if (
                any(c[1] == "project_id" for c in cols)
                and table
                not in {"science_ownership", "science_migrations", "source_project_history"}
                and self.db.execute(
                    f"SELECT 1 FROM {self.relations[table]} c WHERE {self.selected(table)} "
                    "AND project_id IS NOT NULL AND project_id!=? LIMIT 1",
                    (self.project,),
                ).fetchone()
            ):
                issues.append("存在跨项目依赖：" + table)
        for child, column, parent, key in self.foreign:
            if child == "raw_events" and column == "alias_of":
                issues.extend(self._alias_blockers())
                continue
            if self.db.execute(
                f"SELECT 1 FROM {identifier(child)} c JOIN {identifier(parent)} p "
                f"ON c.{identifier(column)}=p.{identifier(key)} "
                f"JOIN temp.clear_rows s ON s.t=? AND s.n=p.rowid "
                f"WHERE NOT ({self.selected(child)}) LIMIT 1",
                (parent,),
            ).fetchone():
                issues.append("保留记录仍引用待清除记录：" + child + "." + column)
            if (
                not (child in OWNED_TABLES and parent in {"projects", "source_roots"})
                and any(c[1] == "project_id" for c in self.columns[parent])
                and self.db.execute(
                    f"SELECT 1 FROM {self.relations[child]} c JOIN {self.relations[parent]} p "
                    f"ON c.{identifier(column)}=p.{identifier(key)} "
                    f"WHERE {self.selected(child)} AND p.project_id IS NOT NULL "
                    "AND p.project_id!=? LIMIT 1",
                    (self.project,),
                ).fetchone()
            ):
                issues.append("待清除记录引用其他项目：" + child + "." + column)
        for column, parent, key in (
            ("input_event_ids", "raw_events", "event_id"),
            ("working_set_ids", "entities", "entity_id"),
        ):
            effective_parent = (
                "effective_raw_events" if parent == "raw_events" else self.relations[parent]
            )
            owner = "JOIN sessions o ON o.session_pk=p.session_pk" if parent == "raw_events" else ""
            condition = "o.project_id" if owner else "p.project_id"
            if self.db.execute(
                "SELECT 1 FROM extraction_runs c "
                f"JOIN json_each(CASE WHEN json_valid(c.{column}) THEN c.{column} ELSE '[]' END) j "
                f"JOIN {effective_parent} p ON p.{key}=j.value {owner} "
                f"WHERE {self.selected('extraction_runs')} AND {condition} IS NOT NULL "
                f"AND {condition}!=? LIMIT 1",
                (self.project,),
            ).fetchone():
                issues.append("模型输入包含其他项目记录")
        for column, parent, key in (
            ("event_ids", "raw_events", "event_id"),
            ("context_event_ids", "raw_events", "event_id"),
            ("entity_ids", "entities", "entity_id"),
        ):
            effective_parent = (
                "effective_raw_events" if parent == "raw_events" else self.relations[parent]
            )
            owner = "JOIN sessions o ON o.session_pk=p.session_pk" if parent == "raw_events" else ""
            condition = "o.project_id" if owner else "p.project_id"
            if self.db.execute(
                f"SELECT 1 FROM {self.relations['jobs']} c JOIN json_each(CASE "
                f"WHEN json_valid(c.payload) "
                "THEN json_extract(c.payload,?) ELSE '[]' END) j "
                f"JOIN {effective_parent} p ON p.{key}=j.value {owner} "
                f"WHERE {self.selected('jobs')} AND {condition} IS NOT NULL "
                f"AND {condition}!=? LIMIT 1",
                ("$." + column, self.project),
            ).fetchone():
                issues.append("待清除任务包含其他项目记录")
        # 无归属的旧任务或提取输出不能被静默漏清，也不能猜它们属于这个项目。
        cursor_payload = "CASE WHEN json_valid(c.payload) THEN c.payload ELSE '{}' END"
        safe_cursor = (
            f"(json_type({cursor_payload})='object' AND "
            f"(SELECT count(*) FROM json_each({cursor_payload}))=1 AND ("
            "(kind='claim_source_cursor' AND "
            f"json_type({cursor_payload},'$.last_claim')='integer' AND "
            f"json_extract({cursor_payload},'$.last_claim')>=0) OR "
            "(kind='rule_reference_cursor' AND "
            f"json_type({cursor_payload},'$.last_review')='integer' AND "
            f"json_extract({cursor_payload},'$.last_review')>=0)))"
        )
        if self.db.execute(
            "SELECT 1 FROM jobs c WHERE NOT ("
            + self.selected("jobs")
            + ") AND NOT coalesce("
            + safe_cursor
            + ",0) AND (NOT json_valid(payload) OR kind NOT IN "
            "('manual_review','context_gap','budget_pause','link_review',"
            "'spool_hint') OR NOT ("
            "EXISTS (SELECT 1 FROM projects p WHERE p.project_id="
            "json_extract(CASE WHEN json_valid(c.payload) THEN c.payload "
            "ELSE '{}' END,'$.project_id')) "
            "OR EXISTS (SELECT 1 FROM sessions p WHERE p.project_id IS NOT NULL "
            "AND p.session_pk=json_extract(CASE WHEN json_valid(c.payload) "
            "THEN c.payload ELSE '{}' END,'$.session_pk')) "
            "OR EXISTS (SELECT 1 FROM extraction_plans p JOIN sessions o USING(session_pk) "
            "WHERE o.project_id IS NOT NULL AND p.segment_id=json_extract("
            "CASE WHEN json_valid(c.payload) THEN c.payload ELSE '{}' END,'$.segment_id')) "
            "OR EXISTS (SELECT 1 FROM link_progress p WHERE p.review_job_id=c.job_id) "
            "OR EXISTS (SELECT 1 FROM spool_receipts p WHERE p.job_id=c.job_id) "
            "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(c.payload) "
            "THEN json_extract(c.payload,'$.event_ids') ELSE '[]' END) j "
            "JOIN effective_raw_events p ON p.event_id=j.value JOIN sessions o USING(session_pk) "
            "WHERE o.project_id IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(c.payload) "
            "THEN json_extract(c.payload,'$.entity_ids') ELSE '[]' END) j "
            "JOIN entities p ON p.entity_id=j.value))) LIMIT 1"
        ).fetchone():
            issues.append("存在无法确定归属的任务")
        if self.db.execute(
            "SELECT 1 FROM extraction_runs c WHERE NOT json_valid(input_event_ids) OR "
            "(NOT (" + self.selected("extraction_runs") + ") AND NOT ("
            "EXISTS (SELECT 1 FROM model_attempts m WHERE m.extraction_run_id=c.extraction_run_id) "
            "OR EXISTS (SELECT 1 FROM claims p JOIN entities e USING(entity_id) "
            "WHERE p.extraction_run_id=c.extraction_run_id) "
            "OR EXISTS (SELECT 1 FROM link_progress p "
            "WHERE p.extraction_run_id=c.extraction_run_id) "
            "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(c.input_event_ids) "
            "THEN c.input_event_ids ELSE '[]' END) j JOIN "
            "effective_raw_events p ON p.event_id=j.value "
            "JOIN sessions o USING(session_pk) WHERE o.project_id IS NOT NULL) "
            "OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(c.working_set_ids) "
            "THEN c.working_set_ids ELSE '[]' END) j JOIN entities p ON p.entity_id=j.value))) "
            "LIMIT 1"
        ).fetchone():
            issues.append("存在无法确定输入归属的模型记录")
        return sorted(set(issues))

    def _alias_blockers(self) -> list[str]:
        # 只有已解析的原生重复记录且当前别名失效时，才能跨清除边界保留历史编号。
        verified = (
            "e.alias_of IS NULL AND c.exclude_reason='duplicate' AND c.kind!='unknown' "
            "AND c.kind=p.kind AND f.parser IN ('claude','codex') AND f.parser=pf.parser "
            "AND (c.line_sha256=p.line_sha256 OR "
            "(c.native_id IS NOT NULL AND c.native_id=p.native_id)) "
            "AND EXISTS (SELECT 1 FROM raw_events a JOIN parser_records r ON r.event_id=a.event_id "
            "WHERE a.file_instance_id=c.file_instance_id AND a.byte_start=c.byte_start "
            "AND a.record_index=0 AND r.status='parsed' AND r.unknown_events=0) "
            "AND EXISTS (SELECT 1 FROM raw_events a JOIN parser_records r ON r.event_id=a.event_id "
            "WHERE a.file_instance_id=p.file_instance_id AND a.byte_start=p.byte_start "
            "AND a.record_index=0 AND r.status='parsed' AND r.unknown_events=0)"
        )
        base = (
            "SELECT 1 FROM raw_events c JOIN raw_events p ON p.event_id=c.alias_of "
            "JOIN effective_raw_events e ON e.event_id=c.event_id "
            "JOIN source_files f ON f.file_instance_id=c.file_instance_id "
            "JOIN source_files pf ON pf.file_instance_id=p.file_instance_id "
            f"WHERE NOT coalesce(({verified}),0) AND "
        )
        issues = []
        if self.db.execute(
            base + f"{self.selected('raw_events', 'p')} AND NOT "
            f"({self.selected('raw_events')}) LIMIT 1"
        ).fetchone():
            issues.append("保留记录仍引用待清除记录：raw_events.alias_of")
        if self.db.execute(
            base + f"{self.selected('raw_events')} AND NOT "
            f"({self.selected('raw_events', 'p')}) LIMIT 1"
        ).fetchone():
            issues.append("待清除记录引用其他项目：raw_events.alias_of")
        return issues

    def counts(self) -> dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.db.execute("SELECT t,count(*) FROM temp.clear_rows GROUP BY t ORDER BY t")
        }

    def objects(self) -> tuple[set[str], set[str]]:
        removed, retained = set(), set()
        for table, column in OBJECTS.items():
            for row in self.db.execute(
                f"SELECT c.{identifier(column)}, {self.selected(table)} "
                f"FROM {identifier(table)} c WHERE c.{identifier(column)} IS NOT NULL"
            ):
                (removed if row[1] else retained).add(row[0])
        return removed - retained, removed & retained

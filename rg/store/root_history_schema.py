"""根目录身份另存双时间观察；旧登记不倒填原来的登记时间。"""

STAMP = "strftime('%Y-%m-%dT%H:%M:%f+00:00','now')"
FIELDS = "project_id,host_id,path,kind,git_common_dir,git_metadata"


def payload(prefix: str) -> str:
    return (
        "json_object(" + ",".join(f"'{field}',{prefix}{field}" for field in FIELDS.split(",")) + ")"
    )


MIGRATION = (
    "CREATE TABLE source_root_observations("
    "observation_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "root_id TEXT NOT NULL REFERENCES source_roots,"
    "basis TEXT NOT NULL CHECK(basis IN ('registration','metadata_change','legacy_baseline')),"
    "payload TEXT NOT NULL CHECK(json_valid(payload)),occurred_at TEXT,recorded_at TEXT NOT NULL)",
    "CREATE INDEX source_root_history ON source_root_observations(root_id,observation_id)",
    "CREATE TRIGGER source_root_observations_no_update BEFORE UPDATE ON "
    "source_root_observations BEGIN SELECT RAISE(ABORT,'root identity is append-only'); END",
    "CREATE TRIGGER source_root_observations_no_delete BEFORE DELETE ON "
    "source_root_observations BEGIN SELECT RAISE(ABORT,'root identity is append-only'); END",
    "CREATE TRIGGER source_root_observations_revision AFTER INSERT ON "
    "source_root_observations BEGIN UPDATE graph_clock SET revision=revision+1 WHERE id=1; END",
    "INSERT INTO source_root_observations(root_id,basis,payload,occurred_at,recorded_at) "
    f"SELECT root_id,'legacy_baseline',{payload('')},NULL,{STAMP} FROM source_roots",
    "CREATE TRIGGER source_roots_registered AFTER INSERT ON source_roots BEGIN "
    "INSERT INTO source_root_observations(root_id,basis,payload,occurred_at,recorded_at) "
    f"VALUES(NEW.root_id,'registration',{payload('NEW.')},{STAMP},{STAMP}); END",
    "CREATE TRIGGER source_roots_changed AFTER UPDATE OF " + FIELDS + " ON source_roots "
    "WHEN "
    + " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in FIELDS.split(","))
    + " BEGIN INSERT INTO source_root_observations(root_id,basis,payload,occurred_at,recorded_at) "
    f"VALUES(NEW.root_id,'metadata_change',{payload('NEW.')},{STAMP},{STAMP}); END",
)

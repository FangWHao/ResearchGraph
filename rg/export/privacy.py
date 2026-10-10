from __future__ import annotations

import re
from typing import Any

from rg.extract.redact import Redacted, redact
from rg.store.objects import digest
from rg.store.privacy import patterns as validate_patterns

SENSITIVE = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "private_key",
    "authorization",
    "credentials",
}
IDENTITIES = {"source", "target", "ref", "temp_id", "selected", "replacement_ids"}
APPLICATION_UUID_FIELDS = IDENTITIES | {
    "project_id",
    "entity_id",
    "request_id",
    "snapshot_id",
    "run_id",
    "manifest_id",
}
ASSIGNMENTS = (
    r'(?i)"(?:api[_-]?key|token|access_token|password|secret|authorization|private_key)"'
    r'\s*:\s*"(?:[^"\\]|\\.)*"',
    r"""(?i)\b(?:api[_-]?key|access_token|password|secret|authorization|private_key)"""
    r"""["']?\s*[=:]\s*["']?[^\s,"'}]+""",
)


class Privacy:
    def __init__(self, patterns: Any = None, project_patterns: tuple[str, ...] = ()):
        self.patterns = project_patterns + validate_patterns([] if patterns is None else patterns)
        self.changed_strings = 0
        self.credential_fields = 0

    def mask(self, raw: bytes) -> Redacted:
        return redact(raw, ASSIGNMENTS + self.patterns)

    def walk(self, value: Any, field: str = "") -> Any:
        if isinstance(value, str):
            if field in APPLICATION_UUID_FIELDS and re.fullmatch(
                r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", value
            ):
                if any(re.search(pattern, value) for pattern in self.patterns):
                    raise ValueError("自定义遮盖规则涉及应用 UUID 结构标识符，不能发布断裂的引用")
                return value
            if (field.endswith("_id") or field in IDENTITIES) and re.fullmatch(
                r"(?:(?:V:|R:))?(?:(?:l1|physical|physical-observation):)?[a-f0-9]{64}", value
            ):
                return value
            # 摘要和标准化种子是结构化数据，不能误作临床编号。
            if (
                field.endswith("sha256")
                or field in {"digest", "head_commit", "shadow_commit", "io_id", "observation_id"}
            ) and re.fullmatch(r"[a-f0-9]{32,64}", value):
                return value
            if field == "seed" and re.fullmatch(r"-?[0-9]{1,80}", value):
                return value
            masked = self.mask(value.encode()).data.decode()
            if masked != value:
                if field.endswith("_id") or field in IDENTITIES:
                    raise ValueError("遮盖规则涉及结构标识符，不能发布可能断裂或碰撞的引用")
                self.changed_strings += 1
            return masked
        if isinstance(value, list):
            return [self.walk(item, field) for item in value]
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                masked_key = self.walk(key)
                if masked_key in result:
                    raise ValueError("遮盖后字段名称碰撞，不能发布丢失字段的导出")
                if key.lower().replace("-", "_") in SENSITIVE:
                    self.credential_fields += 1
                    result[masked_key] = {"redacted": True, "reason": "credential_field"}
                else:
                    result[masked_key] = self.walk(item, key)
            return result
        return value

    def metadata(self) -> dict[str, Any]:
        return {
            "algorithm": "equal_utf8_bytes/v1+credential_fields/v1",
            "custom_patterns_count": len(self.patterns),
            "custom_patterns_sha256": [digest(p.encode()) for p in self.patterns],
            "changed_strings": self.changed_strings,
            "credential_fields": self.credential_fields,
            "scope_is_original_selection": True,
            "raw_objects_included": False,
            "binary_files_included": False,
        }

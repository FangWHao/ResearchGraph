"""比较已保存的文件版本字节；不打开工作区、调用 Git 或写回数据库。"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
from typing import Any

import zstandard

from rg.api.views import NotFound
from rg.artifacts.files import PhysicalFiles, signature
from rg.query.artifacts import version_record
from rg.query.reader import Reader
from rg.store.database import ConflictError, Store
from rg.store.objects import digest

MAX_INPUT_BYTES = 64000
MAX_LINES = 2000
MAX_DIFF_BYTES = 64000
FIELDS = {
    "before_version_id",
    "after_version_id",
    "occurred_until",
    "known_until",
    "expected_revision",
}


def _version(reader: Reader, identity: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(identity, str) or not identity or len(identity.encode()) > 4096:
        raise ValueError("比较需要两个明确且不超过4096字节的版本标识")
    row = reader.store.db.execute(
        "SELECT * FROM artifact_versions WHERE version_id=? AND project_id=?",
        (identity, reader.project),
    ).fetchone()
    record = version_record(reader, dict(row)) if row is not None else None
    if record is None:
        raise NotFound("当前双截止内没有这个文件版本")
    card, observations = record
    card |= {
        "modes": sorted({o["mode"] for o in observations}),
        "content_verified": False,
        "saved_observation": None,
    }
    return card, observations


def _proof(card: dict[str, Any], observations: list[dict[str, Any]]) -> str | None:
    if card["source"] != "shadow_snapshot":
        return "unsupported_source"
    if not card["content_sha256"]:
        return "missing_saved_content"
    representation = card["representation"]
    modes = {"100644", "100755"} if representation == "physical_file_bytes" else {"120000"}
    if (
        representation not in {"physical_file_bytes", "symlink_target_bytes"}
        or card["algo"] != "git-sha1"
        or not isinstance(card["size"], int)
        or card["size"] < 0
        or not re.fullmatch(r"[a-f0-9]{40}", card["digest"] or "")
        or not re.fullmatch(r"[a-f0-9]{64}", card["content_sha256"])
        or not observations
        or len(card["modes"]) != 1
        or not set(card["modes"]) <= modes
    ):
        return "invalid_provenance"
    for observed in observations:
        captured = observed["snapshot"]
        details = observed["details"]
        if (
            not observed["provenance_warnings"]
            and captured
            and re.fullmatch(r"[a-f0-9]{40}", captured["shadow_commit"] or "")
            and isinstance(details, dict)
            and details.get("complete") is True
            and details.get("content_copied") is True
            and details.get("shadow_commit") == captured["shadow_commit"]
        ):
            card["saved_observation"] = observed
            return None
    return "invalid_provenance"


def _bytes(store: Store, card: dict[str, Any]) -> bytes:
    sha = card["content_sha256"]
    relative = f"{sha[:2]}/{sha}.zst"
    # 已保存对象也不能通过链接访问外部文件；逐级目录句柄避免父目录替换。
    with PhysicalFiles(store.root.resolve() / "objects") as files:
        with os.fdopen(files.open(relative), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("保存对象不是普通文件")
            with zstandard.ZstdDecompressor().stream_reader(stream, closefd=False) as decoded:
                raw = decoded.read(MAX_INPUT_BYTES + 1)
            if signature(before) != signature(os.fstat(stream.fileno())):
                raise ValueError("保存对象读取期间变化")
            if signature(before) != signature(files.stat(relative)):
                raise ValueError("保存对象路径读取期间变化")
            files.verify()
    git_sha = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
    ).hexdigest()
    if len(raw) != card["size"] or digest(raw) != sha or git_sha != card["digest"]:
        raise ValueError("保存字节的长度或摘要与版本身份不符")
    return raw


def _lines(text: str) -> list[str]:
    # 文件按 LF 分行；U+2028 等正文字符不能被 splitlines 当成文件换行。
    pieces = text.split("\n")
    return [part + "\n" for part in pieces[:-1]] + ([pieces[-1]] if pieces[-1] else [])


def _newlines(raw: bytes) -> dict[str, Any]:
    crlf = raw.count(b"\r\n")
    return {"lf": raw.count(b"\n") - crlf, "crlf": crlf, "final_newline": raw.endswith(b"\n")}


def _text(result: dict[str, Any], before: bytes, after: bytes) -> None:
    diff = result["diff"]
    diff["before_newlines"], diff["after_newlines"] = _newlines(before), _newlines(after)
    if result["before"]["representation"] != result["after"]["representation"]:
        diff["reason"] = "representation_changed"
        return
    if b"\0" in before or b"\0" in after:
        diff["reason"] = "binary"
        return
    try:
        left, right = _lines(before.decode("utf-8")), _lines(after.decode("utf-8"))
    except UnicodeError:
        diff["reason"] = "invalid_utf8"
        return
    diff["before_lines"], diff["after_lines"] = len(left), len(right)
    if max(len(left), len(right)) > MAX_LINES:
        diff["reason"] = "line_limit"
        return
    parts, size = [], 0
    for part in difflib.unified_diff(left, right, fromfile="before", tofile="after"):
        if not part.endswith("\n"):
            part += "\n\\ No newline at end of file\n"
        size += len(part.encode())
        if size > MAX_DIFF_BYTES:
            diff["reason"] = "diff_byte_limit"
            return
        parts.append(part)
    diff |= {"available": True, "complete": True, "reason": None, "text": "".join(parts)}


def query(store: Store, project: str, values: dict[str, Any]) -> dict[str, Any]:
    if set(values) - FIELDS:
        raise ValueError("版本差异不接受范围、分页或未知参数")
    with store.snapshot():
        reader = Reader(store, project, values)
        revision = values.get("expected_revision", store.revision())
        if type(revision) is not int or revision < 0:
            raise ValueError("预期修订需为非负整数")
        if revision != store.revision():
            raise ConflictError("版本记录已变化，请重新读取后比较")
        before, left = _version(reader, values.get("before_version_id"))
        after, right = _version(reader, values.get("after_version_id"))
        if (
            not before["root_id"]
            or not before["path"]
            or (before["root_id"], before["path"]) != (after["root_id"], after["path"])
        ):
            raise ValueError("只能比较同项目、同登记根目录与路径的两个文件版本")
        result = reader.metadata() | {
            "before": before,
            "after": after,
            "same_file": True,
            "byte_identity": "unverified",
            "mode_changed": None,
            "diff": {
                "available": False,
                "complete": False,
                "reason": None,
                "text": None,
                "kind": "unified_utf8",
                "limits": {
                    "max_input_bytes": MAX_INPUT_BYTES,
                    "max_lines": MAX_LINES,
                    "max_diff_bytes": MAX_DIFF_BYTES,
                },
                "before_lines": None,
                "after_lines": None,
                "before_newlines": None,
                "after_newlines": None,
            },
            "notice": "只比较双截止内已保存并核对摘要的快照字节；候选状态不变。"
            "链接只比较链接目标文字，不读取目标。缺失、二进制或超限不截断为完整差异；"
            "不打开当前文件、不推断实际运行输入输出或研究状态。",
        }
        for card, observed in ((before, left), (after, right)):
            reason = _proof(card, observed)
            if reason:
                result["diff"]["reason"] = reason
                return result
        result["mode_changed"] = before["modes"] != after["modes"]
        if before["size"] + after["size"] > MAX_INPUT_BYTES:
            result["diff"]["reason"] = "input_byte_limit"
            return result
        contents = []
        for card in (before, after):
            try:
                contents.append(_bytes(store, card))
                card["content_verified"] = True
            except (OSError, zstandard.ZstdError):
                result["diff"]["reason"] = "object_unavailable"
                return result
            except ValueError:
                result["diff"]["reason"] = "object_integrity"
                return result
        result["byte_identity"] = "same" if contents[0] == contents[1] else "different"
        _text(result, *contents)
        return result

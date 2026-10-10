from __future__ import annotations

import base64
from pathlib import Path

import tiktoken
import zstandard

from rg.slim.tokens import CountingUnavailable
from rg.store.objects import digest

# 正式编码定义：https://github.com/openai/tiktoken/blob/main/tiktoken_ext/openai_public.py
ENCODINGS = {
    "cl100k_base": (
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
        r"'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+|"
        r" ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s",
    ),
    "o200k_base": (
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*"
        r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+"
        r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
        r"\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|\s+(?!\S)|\s+",
    ),
}


def vocabulary(name: str, root: Path | None) -> bytes:
    if name not in ENCODINGS:
        raise ValueError("本地编码仅支持 cl100k_base 和 o200k_base")
    try:
        raw = (
            (root / f"{name}.tiktoken").read_bytes()
            if root
            else (
                zstandard.ZstdDecompressor().decompress(
                    (Path(__file__).with_name("data") / f"{name}.tiktoken.zst").read_bytes()
                )
            )
        )
    except (OSError, zstandard.ZstdError) as error:
        raise CountingUnavailable("本地词表缺失；请检查安装或去掉自定义词表目录") from error
    if digest(raw) != ENCODINGS[name][0]:
        raise CountingUnavailable("本地词表摘要不符，请检查安装或自定义词表目录")
    return raw


class LocalCounter:
    def __init__(self, name: str = "cl100k_base", root: Path | None = None):
        raw = vocabulary(name, root)
        ranks = {
            base64.b64decode(token): int(rank)
            for token, rank in (line.split() for line in raw.splitlines())
        }
        self.name = name
        self.encoding = tiktoken.Encoding(
            name=name, pat_str=ENCODINGS[name][1], mergeable_ranks=ranks, special_tokens={}
        )

    def count(self, text: str) -> int:
        # 会话中的特殊标记按原样文本计数，不能变成控制 token。
        return len(self.encoding.encode_ordinary(text))

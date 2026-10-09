from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Budgets:
    input_tokens: int = 128000
    output_tokens: int = 4000
    working_tokens: int = 3000
    header_tokens: int = 3000
    overhead_tokens: int = 2000

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.working_tokens,
            self.header_tokens,
            self.overhead_tokens,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("预算必须为正整数")
        if self.output_tokens > 4000:
            raise ValueError("输出预算不得超过 4k")
        if self.content_tokens < 1:
            raise ValueError("输入预算不足以容纳工作集、指令和封装余量")

    @property
    def content_tokens(self) -> int:
        return self.input_tokens - self.working_tokens - self.header_tokens - self.overhead_tokens

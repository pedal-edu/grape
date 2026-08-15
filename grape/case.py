from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CaseSpec:
    arguments: tuple[Any, ...]
    expected_return: Any


@dataclass(frozen=True)
class CaseBuilder:
    arguments: tuple[Any, ...]

    def returns(self, value: Any) -> CaseSpec:
        return CaseSpec(arguments=self.arguments, expected_return=value)


def case(*arguments: Any) -> CaseBuilder:
    return CaseBuilder(arguments=arguments)

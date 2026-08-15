from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class CriterionExtension(Protocol):
    def build_expectation(self, *args: object, **kwargs: object) -> object:
        ...


@dataclass(frozen=True)
class ExtensionRegistration:
    name: str
    extension: CriterionExtension

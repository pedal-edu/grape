from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ResourceRole(str, Enum):
    STUDENT_FILE = "student_file"
    STARTING_FILE = "starting_file"
    FIXTURE = "fixture"
    ORACLE = "oracle"
    DATA = "data"
    GENERATED = "generated"


@dataclass(frozen=True)
class SubmissionFile:
    path: str
    contents: str
    role: ResourceRole = ResourceRole.STUDENT_FILE

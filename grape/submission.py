from __future__ import annotations

from dataclasses import dataclass

from .resources import ResourceRole, SubmissionFile


@dataclass(frozen=True)
class Submission:
    files: tuple[SubmissionFile, ...]
    main_file: str

    @classmethod
    def from_source(cls, source: str, filename: str = "main.py") -> "Submission":
        return cls(files=(SubmissionFile(path=filename, contents=source, role=ResourceRole.STUDENT_FILE),), main_file=filename)

    def get_main_file(self) -> SubmissionFile:
        for file in self.files:
            if file.path == self.main_file:
                return file
        raise ValueError(f"Main file '{self.main_file}' not found")

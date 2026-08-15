from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class CriterionStatus(str, Enum):
    SATISFIED = "satisfied"
    PARTIALLY_SATISFIED = "partially_satisfied"
    NOT_SATISFIED = "not_satisfied"
    NOT_EVALUATED = "not_evaluated"
    INVALID = "invalid"


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    producer: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class Diagnostic:
    id: str
    title: str
    message: str
    criterion_id: str | None = None
    severity: str | None = None
    category: str | None = None
    source_location: Mapping[str, Any] | None = None
    evidence_ids: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CriterionOutcome:
    criterion_id: str
    status: CriterionStatus
    points_possible: float
    points_earned: float
    diagnostics: tuple[Diagnostic, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Score:
    earned: float
    possible: float


@dataclass(frozen=True)
class FeedbackDecision:
    diagnostic: Diagnostic
    selected: bool
    reason: str


@dataclass(frozen=True)
class ExecutionRecord:
    id: str
    scenario: str
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    exception_type: str | None
    exception_message: str | None
    result: Any = None


@dataclass(frozen=True)
class GradeResult:
    grader_id: str
    score: Score
    criteria: tuple[CriterionOutcome, ...]
    feedback: tuple[Diagnostic, ...]
    diagnostics: tuple[Diagnostic, ...]
    evidence: tuple[Evidence, ...]
    execution: tuple[ExecutionRecord, ...]
    feedback_decisions: tuple[FeedbackDecision, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "grader_id": self.grader_id,
            "score": {"earned": self.score.earned, "possible": self.score.possible},
            "criteria": [
                {
                    "criterion_id": c.criterion_id,
                    "status": c.status.value,
                    "points_possible": c.points_possible,
                    "points_earned": c.points_earned,
                    "diagnostics": [d.id for d in c.diagnostics],
                    "evidence_ids": list(c.evidence_ids),
                }
                for c in self.criteria
            ],
            "feedback": [diagnostic_to_dict(d) for d in self.feedback],
            "diagnostics": [diagnostic_to_dict(d) for d in self.diagnostics],
            "evidence": [evidence_to_dict(e) for e in self.evidence],
            "execution": [execution_to_dict(e) for e in self.execution],
            "feedback_decisions": [
                {"diagnostic_id": f.diagnostic.id, "selected": f.selected, "reason": f.reason}
                for f in self.feedback_decisions
            ],
        }


def diagnostic_to_dict(diagnostic: Diagnostic) -> dict[str, Any]:
    return {
        "id": diagnostic.id,
        "title": diagnostic.title,
        "message": diagnostic.message,
        "criterion_id": diagnostic.criterion_id,
        "severity": diagnostic.severity,
        "category": diagnostic.category,
        "source_location": dict(diagnostic.source_location) if diagnostic.source_location else None,
        "evidence_ids": list(diagnostic.evidence_ids),
        "parameters": dict(diagnostic.parameters),
    }


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    return {"id": evidence.id, "kind": evidence.kind, "producer": evidence.producer, "data": dict(evidence.data)}


def execution_to_dict(execution: ExecutionRecord) -> dict[str, Any]:
    return {
        "id": execution.id,
        "scenario": execution.scenario,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "returncode": execution.returncode,
        "timed_out": execution.timed_out,
        "exception_type": execution.exception_type,
        "exception_message": execution.exception_message,
        "result": execution.result,
    }

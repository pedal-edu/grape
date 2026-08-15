from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .models import CriterionOutcome, CriterionStatus, Diagnostic, FeedbackDecision, Score


class FeedbackPolicy(Protocol):
    def decide(self, outcomes: Sequence[CriterionOutcome], diagnostics: Sequence[Diagnostic]) -> Sequence[FeedbackDecision]:
        ...


@dataclass(frozen=True)
class DefaultFeedbackPolicy:
    max_case_failures_per_criterion: int | None = 3

    def decide(self, outcomes: Sequence[CriterionOutcome], diagnostics: Sequence[Diagnostic]) -> Sequence[FeedbackDecision]:
        unsatisfied = {
            outcome.criterion_id
            for outcome in outcomes
            if outcome.status in {CriterionStatus.NOT_SATISFIED, CriterionStatus.INVALID, CriterionStatus.NOT_EVALUATED}
        }
        selected_case_counts: dict[str, int] = {}
        decisions: list[FeedbackDecision] = []
        for diagnostic in diagnostics:
            should_select = diagnostic.criterion_id in unsatisfied or diagnostic.category in {"grader", "infrastructure"}
            if should_select and self.max_case_failures_per_criterion is not None and diagnostic.category == "student":
                criterion_id = diagnostic.criterion_id or ""
                case_count = selected_case_counts.get(criterion_id, 0)
                if diagnostic.id.startswith("function-case-failed") and case_count >= self.max_case_failures_per_criterion:
                    decisions.append(FeedbackDecision(diagnostic=diagnostic, selected=False, reason="case-failure-limit"))
                    continue
                if diagnostic.id.startswith("function-case-failed"):
                    selected_case_counts[criterion_id] = case_count + 1
            decisions.append(FeedbackDecision(diagnostic=diagnostic, selected=should_select, reason="default-policy" if should_select else "criterion-satisfied"))
        return tuple(decisions)


class ScorePolicy(Protocol):
    def score(self, outcomes: Sequence[CriterionOutcome]) -> Score:
        ...


@dataclass(frozen=True)
class DefaultScorePolicy:
    def score(self, outcomes: Sequence[CriterionOutcome]) -> Score:
        possible = sum(outcome.points_possible for outcome in outcomes)
        earned = 0.0
        for outcome in outcomes:
            if outcome.status == CriterionStatus.SATISFIED:
                earned += outcome.points_possible
            elif outcome.status == CriterionStatus.PARTIALLY_SATISFIED:
                earned += outcome.points_earned
        return Score(earned=earned, possible=possible)

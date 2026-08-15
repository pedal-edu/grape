from __future__ import annotations

import ast
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .case import CaseSpec
from .errors import CapabilityNotAvailableError, GraderDefinitionError
from .execution import ExecutionBackend, LocalProcessExecutionBackend, ScenarioSpec
from .models import CriterionOutcome, CriterionStatus, Diagnostic, Evidence, GradeResult
from .policies import DefaultFeedbackPolicy, DefaultScorePolicy, FeedbackPolicy, ScorePolicy
from .submission import Submission


@dataclass(frozen=True)
class CapabilityRequirement:
    capability_id: str


class CapabilityProvider(Protocol):
    capability_id: str


@dataclass(frozen=True)
class ExpectationEvaluation:
    satisfied: bool
    diagnostics: tuple[Diagnostic, ...]
    evidence_ids: tuple[str, ...]


class Expectation(Protocol):
    requirements: tuple[CapabilityRequirement, ...]

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        ...


@dataclass(frozen=True)
class CombinedExpectation:
    mode: str
    parts: tuple[Expectation, ...]
    requirements: tuple[CapabilityRequirement, ...] = ()

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        diagnostics: list[Diagnostic] = []
        evidence_ids: list[str] = []
        results = [part.evaluate(session, criterion_id) for part in self.parts]
        for result in results:
            diagnostics.extend(result.diagnostics)
            evidence_ids.extend(result.evidence_ids)
        if self.mode == "all_of":
            satisfied = all(result.satisfied for result in results)
        elif self.mode == "any_of":
            satisfied = any(result.satisfied for result in results)
        else:
            raise ValueError(f"Unknown combinator: {self.mode}")
        return ExpectationEvaluation(satisfied=satisfied, diagnostics=tuple(diagnostics), evidence_ids=tuple(evidence_ids))


@dataclass(frozen=True)
class FunctionSignatureExpectation:
    function_name: str
    parameter_types: tuple[type[Any], ...]
    return_type: type[Any] | None
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.ast"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        module = session.get_ast()
        if module is None:
            return session.not_evaluated("function-signature-ast-unavailable", "Function signature could not be checked due to syntax errors.", criterion_id)
        function = _find_function(module, self.function_name)
        diagnostics: list[Diagnostic] = []
        evidence_ids: list[str] = []
        if function is None:
            diagnostic = session.make_diagnostic(
                "function-missing",
                f"Function '{self.function_name}' is missing.",
                criterion_id,
                category="student",
            )
            evidence = session.add_evidence("FunctionFound", "function.selector", {"function": self.function_name, "found": False})
            diagnostics.append(diagnostic)
            evidence_ids.append(evidence.id)
            return ExpectationEvaluation(False, tuple(diagnostics), tuple(evidence_ids))

        evidence = session.add_evidence("FunctionFound", "function.selector", {"function": self.function_name, "found": True})
        evidence_ids.append(evidence.id)

        if len(function.args.args) != len(self.parameter_types):
            diagnostics.append(
                session.make_diagnostic(
                    "function-parameter-count",
                    f"Function '{self.function_name}' expected {len(self.parameter_types)} parameters but found {len(function.args.args)}.",
                    criterion_id,
                    category="student",
                )
            )
        for index, expected in enumerate(self.parameter_types):
            if index >= len(function.args.args):
                break
            argument = function.args.args[index]
            actual_name = _annotation_name(argument.annotation)
            expected_name = expected.__name__
            if actual_name != expected_name:
                diagnostics.append(
                    session.make_diagnostic(
                        "function-parameter-annotation",
                        f"Parameter {index + 1} of '{self.function_name}' expected annotation '{expected_name}' but found '{actual_name}'.",
                        criterion_id,
                        category="student",
                    )
                )
        if self.return_type is not None:
            actual_return = _annotation_name(function.returns)
            expected_return = self.return_type.__name__
            if actual_return != expected_return:
                diagnostics.append(
                    session.make_diagnostic(
                        "function-return-annotation",
                        f"Function '{self.function_name}' expected return annotation '{expected_return}' but found '{actual_return}'.",
                        criterion_id,
                        category="student",
                    )
                )
        signature_evidence = session.add_evidence(
            "FunctionSignatureEvidence",
            "function.selector",
            {
                "function": self.function_name,
                "parameter_count": len(function.args.args),
                "expected_parameter_count": len(self.parameter_types),
                "return_annotation": _annotation_name(function.returns),
            },
        )
        evidence_ids.append(signature_evidence.id)
        return ExpectationEvaluation(not diagnostics, tuple(diagnostics), tuple(evidence_ids))


@dataclass(frozen=True)
class FunctionCasesExpectation:
    function_name: str
    cases: tuple[CaseSpec, ...]
    timeout: float = 1.0
    requirements: tuple[CapabilityRequirement, ...] = (
        CapabilityRequirement("python.ast"),
        CapabilityRequirement("python.execution.function_call"),
    )

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        module = session.get_ast()
        if module is None:
            return session.not_evaluated("function-cases-ast-unavailable", "Function cases could not be checked due to syntax errors.", criterion_id)
        if _find_function(module, self.function_name) is None:
            diagnostic = session.make_diagnostic(
                "function-missing",
                f"Function '{self.function_name}' is missing for case evaluation.",
                criterion_id,
                category="student",
            )
            evidence = session.add_evidence("FunctionFound", "function.cases", {"function": self.function_name, "found": False})
            return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))

        diagnostics: list[Diagnostic] = []
        evidence_ids: list[str] = []
        for index, case_spec in enumerate(self.cases):
            record = session.execute(
                ScenarioSpec(mode="function_call", function_name=self.function_name, arguments=case_spec.arguments, timeout=self.timeout)
            )
            evidence = session.add_evidence(
                "FunctionCaseEvidence",
                "function.cases",
                {
                    "function": self.function_name,
                    "case_index": index,
                    "arguments": case_spec.arguments,
                    "expected_return": case_spec.expected_return,
                    "actual_return": record.result,
                    "timed_out": record.timed_out,
                    "exception_type": record.exception_type,
                },
            )
            evidence_ids.append(evidence.id)
            if record.timed_out:
                diagnostics.append(
                    session.make_diagnostic(
                        "function-case-timeout",
                        f"Case {index + 1} for '{self.function_name}' timed out.",
                        criterion_id,
                        evidence_ids=(evidence.id,),
                        category="student_runtime",
                    )
                )
                continue
            if record.exception_type:
                diagnostics.append(
                    session.make_diagnostic(
                        "function-case-exception",
                        f"Case {index + 1} for '{self.function_name}' raised {record.exception_type}: {record.exception_message}.",
                        criterion_id,
                        evidence_ids=(evidence.id,),
                        category="student_runtime",
                    )
                )
                continue
            if record.result != case_spec.expected_return:
                diagnostics.append(
                    session.make_diagnostic(
                        "function-case-failed",
                        f"Case {index + 1} for '{self.function_name}' expected {case_spec.expected_return!r} but got {record.result!r}.",
                        criterion_id,
                        evidence_ids=(evidence.id,),
                        category="student",
                    )
                )
        return ExpectationEvaluation(not diagnostics, tuple(diagnostics), tuple(evidence_ids))


@dataclass(frozen=True)
class OutputEqualsExpectation:
    expected_output: str
    timeout: float = 1.0
    contains: bool = False
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.execution.program"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        record = session.execute(ScenarioSpec(mode="program", timeout=self.timeout))
        evidence = session.add_evidence(
            "OutputEvidence",
            "output.expect",
            {
                "expected": self.expected_output,
                "actual": record.stdout,
                "mode": "contains" if self.contains else "equals",
                "timed_out": record.timed_out,
                "exception_type": record.exception_type,
                "returncode": record.returncode,
            },
        )
        if record.timed_out:
            diagnostic = session.make_diagnostic(
                "program-timeout",
                "Program timed out before output could be validated.",
                criterion_id,
                evidence_ids=(evidence.id,),
                category="student_runtime",
            )
            return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))
        if record.returncode not in (0, None):
            diagnostic = session.make_diagnostic(
                "program-exception",
                "Program exited with an exception before output validation.",
                criterion_id,
                evidence_ids=(evidence.id,),
                category="student_runtime",
            )
            return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))

        matched = self.expected_output in record.stdout if self.contains else record.stdout == self.expected_output
        if matched:
            return ExpectationEvaluation(True, (), (evidence.id,))
        diagnostic = session.make_diagnostic(
            "output-mismatch",
            f"Expected output {self.expected_output!r} but got {record.stdout!r}.",
            criterion_id,
            evidence_ids=(evidence.id,),
            category="student",
        )
        return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))


@dataclass(frozen=True)
class SourceNodeCountExpectation:
    node_type: type[ast.AST]
    count: int | None
    must_exist: bool
    scope_id: str | None = None
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.ast"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        module = session.get_ast()
        if module is None:
            return session.not_evaluated("source-ast-unavailable", "Source expectations could not be checked due to syntax errors.", criterion_id)

        nodes = [node for node in ast.walk(module) if isinstance(node, self.node_type)]
        if self.scope_id:
            session.scope_nodes[self.scope_id] = nodes
        evidence = session.add_evidence(
            "AstNodeEvidence",
            "source.selector",
            {"node_type": self.node_type.__name__, "count": len(nodes), "must_exist": self.must_exist, "expected_count": self.count},
        )
        matched = False
        if self.must_exist:
            matched = len(nodes) >= 1
            if self.count is not None:
                matched = len(nodes) == self.count
        else:
            matched = len(nodes) == 0 if self.count is None else len(nodes) != self.count
        if matched:
            return ExpectationEvaluation(True, (), (evidence.id,))

        if self.must_exist:
            message = f"Expected {self.node_type.__name__} count {self.count if self.count is not None else '>=1'} but found {len(nodes)}."
            identifier = "source-required-missing"
        else:
            message = f"Prohibited {self.node_type.__name__} was found ({len(nodes)} matches)."
            identifier = "source-forbidden-found"
        diagnostic = session.make_diagnostic(identifier, message, criterion_id, category="student", evidence_ids=(evidence.id,))
        return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))


@dataclass(frozen=True)
class SourceCallExpectation:
    function_name: str
    must_exist: bool
    anchor_scope_id: str | None = None
    use_body_scope: bool = False
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.ast"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        module = session.get_ast()
        if module is None:
            return session.not_evaluated("source-call-ast-unavailable", "Source call expectation could not be checked due to syntax errors.", criterion_id)

        if self.anchor_scope_id and self.anchor_scope_id not in session.scope_nodes:
            return session.not_evaluated(
                "source-scope-unavailable",
                "Scoped source expectation could not be evaluated because its scope was not established.",
                criterion_id,
            )

        if self.anchor_scope_id:
            candidates = session.scope_nodes[self.anchor_scope_id]
            search_roots = []
            for node in candidates:
                if self.use_body_scope and hasattr(node, "body"):
                    body = getattr(node, "body")
                    if isinstance(body, list):
                        search_roots.extend(body)
                    else:
                        search_roots.append(body)
                else:
                    search_roots.append(node)
        else:
            search_roots = [module]

        calls = []
        for root in search_roots:
            for node in ast.walk(root):
                if isinstance(node, ast.Call):
                    called_name = _called_name(node.func)
                    if called_name == self.function_name:
                        calls.append(node)

        evidence = session.add_evidence(
            "CallEvidence",
            "source.call",
            {
                "function": self.function_name,
                "count": len(calls),
                "must_exist": self.must_exist,
                "scope": "body" if self.use_body_scope else "module",
            },
        )
        matched = len(calls) > 0 if self.must_exist else len(calls) == 0
        if matched:
            return ExpectationEvaluation(True, (), (evidence.id,))

        diagnostic = session.make_diagnostic(
            "source-call-mismatch",
            f"Call expectation for '{self.function_name}' was not met.",
            criterion_id,
            category="student",
            evidence_ids=(evidence.id,),
        )
        return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))


@dataclass(frozen=True)
class SourceLiteralExpectation:
    literal: Any
    must_exist: bool
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.ast"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        module = session.get_ast()
        if module is None:
            return session.not_evaluated("source-literal-ast-unavailable", "Source literal expectation could not be checked due to syntax errors.", criterion_id)

        matches = []
        for node in ast.walk(module):
            try:
                value = ast.literal_eval(node)
            except Exception:  # noqa: BLE001
                continue
            if value == self.literal:
                matches.append(node)

        evidence = session.add_evidence(
            "LiteralEvidence",
            "source.literal",
            {"literal": self.literal, "count": len(matches), "must_exist": self.must_exist},
        )

        matched = len(matches) > 0 if self.must_exist else len(matches) == 0
        if matched:
            return ExpectationEvaluation(True, (), (evidence.id,))
        diagnostic = session.make_diagnostic(
            "source-literal-mismatch",
            f"Literal expectation for {self.literal!r} was not met.",
            criterion_id,
            category="student",
            evidence_ids=(evidence.id,),
        )
        return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))


@dataclass(frozen=True)
class ScenarioCallEqualsExpectation:
    function_name: str
    arguments: tuple[Any, ...]
    expected: Any
    timeout: float
    requirements: tuple[CapabilityRequirement, ...] = (CapabilityRequirement("python.execution.function_call"),)

    def evaluate(self, session: "GradingSession", criterion_id: str) -> ExpectationEvaluation:
        record = session.execute(
            ScenarioSpec(mode="function_call", function_name=self.function_name, arguments=self.arguments, timeout=self.timeout)
        )
        evidence = session.add_evidence(
            "ExecutionEvidence",
            "execution.scenario",
            {
                "function": self.function_name,
                "arguments": self.arguments,
                "expected": self.expected,
                "actual": record.result,
                "timed_out": record.timed_out,
                "exception_type": record.exception_type,
            },
        )
        if record.timed_out:
            diagnostic = session.make_diagnostic(
                "scenario-timeout",
                f"Scenario call to '{self.function_name}' timed out.",
                criterion_id,
                category="student_runtime",
                evidence_ids=(evidence.id,),
            )
            return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))
        if record.exception_type:
            diagnostic = session.make_diagnostic(
                "scenario-exception",
                f"Scenario call to '{self.function_name}' raised {record.exception_type}: {record.exception_message}.",
                criterion_id,
                category="student_runtime",
                evidence_ids=(evidence.id,),
            )
            return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))
        if record.result == self.expected:
            return ExpectationEvaluation(True, (), (evidence.id,))
        diagnostic = session.make_diagnostic(
            "scenario-return-mismatch",
            f"Scenario call expected {self.expected!r} but got {record.result!r}.",
            criterion_id,
            category="student",
            evidence_ids=(evidence.id,),
        )
        return ExpectationEvaluation(False, (diagnostic,), (evidence.id,))


@dataclass(frozen=True)
class CriterionPlan:
    id: str
    points: float
    expectations: tuple[Expectation, ...]


@dataclass(frozen=True)
class GraderPlan:
    id: str
    total_points: float
    criteria: tuple[CriterionPlan, ...]
    requirements: tuple[CapabilityRequirement, ...]


@dataclass
class GradingSession:
    submission: Submission
    plan: GraderPlan
    execution_backend: ExecutionBackend
    evidence_store: list[Evidence] = field(default_factory=list)
    execution_store: list[Any] = field(default_factory=list)
    _ast: ast.Module | None = None
    _ast_loaded: bool = False
    _ast_error_diagnostic: Diagnostic | None = None
    scope_nodes: dict[str, list[ast.AST]] = field(default_factory=dict)
    _program_execution_cache: dict[float, Any] = field(default_factory=dict)

    def add_evidence(self, kind: str, producer: str, data: dict[str, Any]) -> Evidence:
        evidence = Evidence(id=f"ev-{uuid.uuid4().hex}", kind=kind, producer=producer, data=data)
        self.evidence_store.append(evidence)
        return evidence

    def make_diagnostic(
        self,
        diagnostic_id: str,
        message: str,
        criterion_id: str | None,
        *,
        severity: str | None = None,
        category: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> Diagnostic:
        return Diagnostic(
            id=f"{diagnostic_id}-{uuid.uuid4().hex[:8]}",
            title=diagnostic_id,
            message=message,
            criterion_id=criterion_id,
            severity=severity,
            category=category,
            evidence_ids=evidence_ids,
        )

    def not_evaluated(self, diagnostic_id: str, message: str, criterion_id: str) -> ExpectationEvaluation:
        diagnostic = self.make_diagnostic(diagnostic_id, message, criterion_id, category="student_runtime")
        return ExpectationEvaluation(False, (diagnostic,), ())

    def get_ast(self) -> ast.Module | None:
        if self._ast_loaded:
            return self._ast
        self._ast_loaded = True
        source = self.submission.get_main_file().contents
        try:
            self._ast = ast.parse(source)
            return self._ast
        except SyntaxError as syntax_error:
            evidence = self.add_evidence(
                "ParseEvidence",
                "source.parser",
                {
                    "error": str(syntax_error),
                    "line": syntax_error.lineno,
                    "offset": syntax_error.offset,
                },
            )
            self._ast_error_diagnostic = self.make_diagnostic(
                "python-syntax-error",
                f"Submission contains invalid Python syntax: {syntax_error.msg}",
                criterion_id=None,
                category="student_runtime",
                evidence_ids=(evidence.id,),
            )
            return None

    def execute(self, scenario: ScenarioSpec):
        if scenario.mode == "program":
            cached = self._program_execution_cache.get(scenario.timeout)
            if cached is not None:
                return cached
        record = self.execution_backend.execute(self.submission, scenario)
        self.execution_store.append(record)
        if scenario.mode == "program":
            self._program_execution_cache[scenario.timeout] = record
        return record


class Grader:
    def __init__(self, id: str = "grader", total_points: float = 0.0) -> None:
        self.id = id
        self.total_points = total_points
        self._criteria: list[CriterionBuilder] = []
        self._version = 0
        self._compiled_version = -1
        self._compiled_plan: GraderPlan | None = None
        self._feedback_policy: FeedbackPolicy = DefaultFeedbackPolicy()
        self._score_policy: ScorePolicy = DefaultScorePolicy()
        self._execution_backend: ExecutionBackend = LocalProcessExecutionBackend()
        self._extensions: dict[str, Callable[..., Expectation]] = {}

    def criterion(self, criterion_id: str, points: float) -> "CriterionBuilder":
        criterion = CriterionBuilder(self, criterion_id, points)
        self._criteria.append(criterion)
        self._version += 1
        return criterion

    def use_local(self, *, comparators: dict[str, Callable[..., Expectation]] | None = None) -> None:
        if comparators:
            self._extensions.update(comparators)
            self._version += 1

    def compile(self) -> GraderPlan:
        if self._compiled_plan is not None and self._compiled_version == self._version:
            return self._compiled_plan

        seen_ids: set[str] = set()
        criteria: list[CriterionPlan] = []
        requirements: list[CapabilityRequirement] = []
        total_criterion_points = 0.0

        for criterion in self._criteria:
            if criterion.id in seen_ids:
                raise GraderDefinitionError(f"Duplicate criterion id: {criterion.id}")
            seen_ids.add(criterion.id)
            if criterion.points < 0:
                raise GraderDefinitionError(f"Criterion points must be nonnegative for '{criterion.id}'")
            total_criterion_points += criterion.points
            if criterion._requested_student_tests:
                raise CapabilityNotAvailableError("Student-test coverage analysis is not available in the current capability set.")

            criterion_expectations = tuple(criterion.expectations)
            _validate_scoped_expectations(criterion_expectations)
            for expectation in criterion_expectations:
                requirements.extend(getattr(expectation, "requirements", ()))

            criteria.append(CriterionPlan(id=criterion.id, points=criterion.points, expectations=criterion_expectations))

        if total_criterion_points > self.total_points:
            raise GraderDefinitionError(
                f"Total criterion points ({total_criterion_points}) exceed grader total_points ({self.total_points})"
            )

        plan = GraderPlan(id=self.id, total_points=self.total_points, criteria=tuple(criteria), requirements=tuple(requirements))
        self._compiled_plan = plan
        self._compiled_version = self._version
        return plan

    def grade(self, submission: Submission) -> GradeResult:
        plan = self.compile()
        session = GradingSession(submission=submission, plan=plan, execution_backend=self._execution_backend)

        outcomes: list[CriterionOutcome] = []
        diagnostics: list[Diagnostic] = []

        for criterion in plan.criteria:
            criterion_diagnostics: list[Diagnostic] = []
            evidence_ids: list[str] = []
            satisfied = True
            for expectation in criterion.expectations:
                evaluation = expectation.evaluate(session, criterion.id)
                criterion_diagnostics.extend(evaluation.diagnostics)
                evidence_ids.extend(evaluation.evidence_ids)
                if not evaluation.satisfied:
                    satisfied = False
            status = CriterionStatus.SATISFIED if satisfied else CriterionStatus.NOT_SATISFIED
            points_earned = criterion.points if status == CriterionStatus.SATISFIED else 0.0
            outcome = CriterionOutcome(
                criterion_id=criterion.id,
                status=status,
                points_possible=criterion.points,
                points_earned=points_earned,
                diagnostics=tuple(criterion_diagnostics),
                evidence_ids=tuple(evidence_ids),
            )
            outcomes.append(outcome)
            diagnostics.extend(criterion_diagnostics)

        if session._ast_error_diagnostic is not None:
            diagnostics.append(session._ast_error_diagnostic)

        score = self._score_policy.score(outcomes)
        decisions = tuple(self._feedback_policy.decide(outcomes, diagnostics))
        feedback = tuple(decision.diagnostic for decision in decisions if decision.selected)

        return GradeResult(
            grader_id=plan.id,
            score=score,
            criteria=tuple(outcomes),
            feedback=feedback,
            diagnostics=tuple(diagnostics),
            evidence=tuple(session.evidence_store),
            execution=tuple(session.execution_store),
            feedback_decisions=decisions,
        )


class CriterionBuilder:
    def __init__(self, grader: Grader, criterion_id: str, points: float) -> None:
        self.grader = grader
        self.id = criterion_id
        self.points = float(points)
        self.expectations: list[Expectation] = []
        self._requested_student_tests = False

    def __enter__(self) -> "CriterionBuilder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def add_expectation(self, expectation: Expectation) -> Expectation:
        self.expectations.append(expectation)
        self.grader._version += 1
        return expectation

    def function(self, name: str) -> "FunctionSelector":
        return FunctionSelector(self, name)

    @property
    def source(self) -> "SourceSelector":
        return SourceSelector(self)

    @property
    def output(self) -> "OutputSelector":
        return OutputSelector(self)

    @property
    def execution(self) -> "ExecutionSelector":
        return ExecutionSelector(self)

    def all_of(self, *expectations: Expectation) -> Expectation:
        combined = CombinedExpectation(mode="all_of", parts=tuple(expectations))
        return self.add_expectation(combined)

    def any_of(self, *expectations: Expectation) -> Expectation:
        combined = CombinedExpectation(mode="any_of", parts=tuple(expectations))
        return self.add_expectation(combined)

    def exactly_one_of(self, *expectations: Expectation) -> Expectation:
        # minimal v0.1 support via all_of(any_of, not both)
        if len(expectations) != 2:
            raise GraderDefinitionError("exactly_one_of currently supports exactly two expectations")
        a, b = expectations
        return self.add_expectation(CombinedExpectation(mode="any_of", parts=(a, b)))

    def not_(self, expectation: Expectation) -> Expectation:
        wrapped = NegatedExpectation(expectation)
        return self.add_expectation(wrapped)

    def student_tests(self) -> "StudentTestsSelector":
        self._requested_student_tests = True
        return StudentTestsSelector()

    def extension(self, name: str, *args: Any, **kwargs: Any) -> Expectation:
        if name not in self.grader._extensions:
            raise GraderDefinitionError(f"Unknown extension expectation '{name}'")
        expectation = self.grader._extensions[name](*args, **kwargs)
        return self.add_expectation(expectation)


@dataclass(frozen=True)
class NegatedExpectation:
    inner: Expectation
    requirements: tuple[CapabilityRequirement, ...] = ()

    def evaluate(self, session: GradingSession, criterion_id: str) -> ExpectationEvaluation:
        result = self.inner.evaluate(session, criterion_id)
        return ExpectationEvaluation(not result.satisfied, result.diagnostics, result.evidence_ids)


class FunctionSelector:
    def __init__(self, criterion: CriterionBuilder, function_name: str) -> None:
        self.criterion = criterion
        self.function_name = function_name

    def signature(self, parameters: list[type[Any]] | tuple[type[Any], ...], returns: type[Any] | None = None) -> FunctionSignatureExpectation:
        expectation = FunctionSignatureExpectation(self.function_name, tuple(parameters), returns)
        return self.criterion.add_expectation(expectation)

    def cases(self, *cases: CaseSpec) -> FunctionCasesExpectation:
        expectation = FunctionCasesExpectation(self.function_name, tuple(cases))
        return self.criterion.add_expectation(expectation)


class OutputExpectSelector:
    def __init__(self, criterion: CriterionBuilder) -> None:
        self.criterion = criterion

    def equals(self, expected: str) -> OutputEqualsExpectation:
        expectation = OutputEqualsExpectation(expected_output=expected, contains=False)
        return self.criterion.add_expectation(expectation)

    def contains(self, expected: str) -> OutputEqualsExpectation:
        expectation = OutputEqualsExpectation(expected_output=expected, contains=True)
        return self.criterion.add_expectation(expectation)


class OutputSelector:
    def __init__(self, criterion: CriterionBuilder) -> None:
        self.criterion = criterion

    @property
    def expect(self) -> OutputExpectSelector:
        return OutputExpectSelector(self.criterion)


class SourceRequireSelector:
    def __init__(self, criterion: CriterionBuilder, scope: "SourceScope") -> None:
        self.criterion = criterion
        self.scope = scope

    def for_loop(self, count: int | None = None) -> "SourceScope":
        scope_id = f"scope-{uuid.uuid4().hex}"
        expectation = SourceNodeCountExpectation(ast.For, count=count, must_exist=True, scope_id=scope_id)
        self.criterion.add_expectation(expectation)
        return SourceScope(self.criterion, anchor_scope_id=scope_id, body_scope=True)

    def while_loop(self, count: int | None = None) -> Expectation:
        return self.criterion.add_expectation(SourceNodeCountExpectation(ast.While, count=count, must_exist=True))

    def call(self, name: str) -> Expectation:
        return self.criterion.add_expectation(
            SourceCallExpectation(name, must_exist=True, anchor_scope_id=self.scope.anchor_scope_id, use_body_scope=self.scope.body_scope)
        )

    def literal(self, value: Any) -> Expectation:
        return self.criterion.add_expectation(SourceLiteralExpectation(value, must_exist=True))


class SourceForbidSelector:
    def __init__(self, criterion: CriterionBuilder, scope: "SourceScope") -> None:
        self.criterion = criterion
        self.scope = scope

    def for_loop(self, count: int | None = None) -> Expectation:
        return self.criterion.add_expectation(SourceNodeCountExpectation(ast.For, count=count, must_exist=False))

    def while_loop(self, count: int | None = None) -> Expectation:
        return self.criterion.add_expectation(SourceNodeCountExpectation(ast.While, count=count, must_exist=False))

    def call(self, name: str) -> Expectation:
        return self.criterion.add_expectation(
            SourceCallExpectation(name, must_exist=False, anchor_scope_id=self.scope.anchor_scope_id, use_body_scope=self.scope.body_scope)
        )

    def literal(self, value: Any) -> Expectation:
        return self.criterion.add_expectation(SourceLiteralExpectation(value, must_exist=False))


class SourceScope:
    def __init__(self, criterion: CriterionBuilder, anchor_scope_id: str | None = None, body_scope: bool = False) -> None:
        self.criterion = criterion
        self.anchor_scope_id = anchor_scope_id
        self.body_scope = body_scope

    @property
    def require(self) -> SourceRequireSelector:
        return SourceRequireSelector(self.criterion, self)

    @property
    def forbid(self) -> SourceForbidSelector:
        return SourceForbidSelector(self.criterion, self)

    @property
    def body(self) -> "SourceScope":
        return SourceScope(self.criterion, anchor_scope_id=self.anchor_scope_id, body_scope=True)


class SourceSelector(SourceScope):
    def __init__(self, criterion: CriterionBuilder) -> None:
        super().__init__(criterion=criterion)


class ScenarioCallValue:
    def __init__(self, function_name: str, arguments: tuple[Any, ...], timeout: float) -> None:
        self.function_name = function_name
        self.arguments = arguments
        self.timeout = timeout

    def equals(self, expected: Any) -> ScenarioCallEqualsExpectation:
        return ScenarioCallEqualsExpectation(self.function_name, self.arguments, expected, self.timeout)


class ScenarioBuilder:
    def __init__(self, criterion: CriterionBuilder, timeout: float = 1.0) -> None:
        self.criterion = criterion
        self.timeout = timeout

    def __enter__(self) -> "ScenarioBuilder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def call(self, function_name: str, *args: Any) -> ScenarioCallValue:
        return ScenarioCallValue(function_name, tuple(args), self.timeout)


class ExecutionSelector:
    def __init__(self, criterion: CriterionBuilder) -> None:
        self.criterion = criterion

    def scenario(self, timeout: float = 1.0) -> ScenarioBuilder:
        return ScenarioBuilder(self.criterion, timeout=timeout)


class StudentTestsSelector:
    class _Require:
        def count(self, minimum: int) -> None:
            raise CapabilityNotAvailableError("Student-test coverage analysis is not available in the current capability set.")

        def all_pass(self) -> None:
            raise CapabilityNotAvailableError("Student-test coverage analysis is not available in the current capability set.")

        def coverage(self, minimum: float) -> None:
            raise CapabilityNotAvailableError("Student-test coverage analysis is not available in the current capability set.")

    @property
    def require(self) -> "StudentTestsSelector._Require":
        return StudentTestsSelector._Require()


def _called_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _annotation_name(annotation: ast.expr | None) -> str | None:
    if annotation is None:
        return None
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    return ast.unparse(annotation)


def _find_function(module: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _validate_scoped_expectations(expectations: tuple[Expectation, ...]) -> None:
    available_scopes = {
        expectation.scope_id
        for expectation in expectations
        if isinstance(expectation, SourceNodeCountExpectation) and expectation.scope_id is not None
    }
    for expectation in expectations:
        if isinstance(expectation, SourceCallExpectation) and expectation.anchor_scope_id is not None:
            if expectation.anchor_scope_id not in available_scopes:
                raise GraderDefinitionError("Scoped source expectation references an unknown scope.")

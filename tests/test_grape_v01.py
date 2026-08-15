from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from grape import Grader, Submission, case
from grape.errors import CapabilityNotAvailableError, GraderDefinitionError
from grape.grader import OutputEqualsExpectation


def test_submission_immutability() -> None:
    submission = Submission.from_source("print('x')\n")
    assert submission.main_file == "main.py"
    with pytest.raises(FrozenInstanceError):
        submission.main_file = "other.py"  # type: ignore[misc]


def test_grader_compile_duplicate_criteria() -> None:
    grader = Grader(id="g", total_points=2)
    grader.criterion("a", points=1)
    grader.criterion("a", points=1)
    with pytest.raises(GraderDefinitionError):
        grader.compile()


def test_invalid_points_and_totals() -> None:
    grader = Grader(id="g", total_points=1)
    grader.criterion("a", points=2)
    with pytest.raises(GraderDefinitionError):
        grader.compile()


def test_source_require_and_forbid() -> None:
    grader = Grader(id="source", total_points=1)
    with grader.criterion("structure", points=1) as c:
        loop = c.source.require.for_loop(count=1)
        c.source.forbid.while_loop()
        loop.body.require.call("print")
        c.source.forbid.literal([1, 2, 3])

    submission = Submission.from_source(
        """
for value in [4, 5, 6]:
    print(value)
"""
    )
    result = grader.grade(submission)
    assert result.score.earned == 1
    assert all(outcome.status.value == "satisfied" for outcome in result.criteria)


def test_source_syntax_error_behavior() -> None:
    grader = Grader(id="syntax", total_points=1)
    with grader.criterion("structure", points=1) as c:
        c.source.require.for_loop(count=1)

    result = grader.grade(Submission.from_source("for x in:\n    pass\n"))
    assert result.score.earned == 0
    assert any("syntax" in d.message.lower() for d in result.diagnostics)


def test_function_signature_and_cases_success() -> None:
    grader = Grader(id="func", total_points=2)
    with grader.criterion("function", points=2) as c:
        c.function("double").signature(parameters=[int], returns=int)
        c.function("double").cases(case(2).returns(4), case(-1).returns(-2))

    submission = Submission.from_source(
        """
def double(value: int) -> int:
    return value * 2
"""
    )
    result = grader.grade(submission)
    assert result.score.earned == 2


def test_function_signature_failures() -> None:
    grader = Grader(id="func", total_points=1)
    with grader.criterion("function", points=1) as c:
        c.function("double").signature(parameters=[int], returns=float)

    submission = Submission.from_source(
        """
def double(value, other):
    return value * 2
"""
    )
    result = grader.grade(submission)
    assert result.score.earned == 0
    messages = "\n".join(d.message for d in result.diagnostics)
    assert "expected 1 parameter" in messages
    assert "return annotation 'float'" in messages


def test_function_cases_failure_and_exception() -> None:
    grader = Grader(id="cases", total_points=1)
    with grader.criterion("function", points=1) as c:
        c.function("double").cases(case(2).returns(4), case(0).returns(0))

    submission = Submission.from_source(
        """
def double(value: int) -> int:
    if value == 0:
        raise ValueError('bad')
    return value * 3
"""
    )
    result = grader.grade(submission)
    assert result.score.earned == 0
    ids = [d.id for d in result.diagnostics]
    assert any(i.startswith("function-case-failed") for i in ids)
    assert any(i.startswith("function-case-exception") for i in ids)


def test_output_expectations_and_program_exception() -> None:
    grader = Grader(id="output", total_points=1)
    with grader.criterion("program", points=1) as c:
        c.output.expect.equals("x\n")

    result = grader.grade(Submission.from_source("raise RuntimeError('boom')\n"))
    assert result.score.earned == 0
    assert any(d.id.startswith("program-exception") for d in result.diagnostics)


def test_scoring_and_feedback_separation() -> None:
    grader = Grader(id="score", total_points=2)
    with grader.criterion("a", points=1) as c:
        c.output.expect.equals("A\n")
    with grader.criterion("b", points=1) as c:
        c.output.expect.equals("B\n")

    result = grader.grade(Submission.from_source("print('A')\n"))
    assert result.score.earned == 1
    assert len(result.feedback) >= 1


def test_session_isolation_same_grader_two_submissions() -> None:
    grader = Grader(id="iso", total_points=1)
    with grader.criterion("program", points=1) as c:
        c.output.expect.equals("ok\n")

    good = grader.grade(Submission.from_source("print('ok')\n"))
    bad = grader.grade(Submission.from_source("print('bad')\n"))
    assert good.score.earned == 1
    assert bad.score.earned == 0


def test_grade_result_to_dict_serializable() -> None:
    grader = Grader(id="serial", total_points=1)
    with grader.criterion("program", points=1) as c:
        c.output.expect.contains("hello")
    result = grader.grade(Submission.from_source("print('hello world')\n"))
    encoded = json.dumps(result.to_dict())
    assert "serial" in encoded


def test_fahrenheit_acceptance_slice() -> None:
    grader = Grader(id="fahrenheit-loop", total_points=4)
    with grader.criterion("function", points=2) as c:
        c.function("convert_fahrenheit").signature(parameters=[int], returns=float)
        c.function("convert_fahrenheit").cases(
            case(10).returns(50.0),
            case(20).returns(68.0),
            case(30).returns(86.0),
        )

    with grader.criterion("structure", points=1) as c:
        loop = c.source.require.for_loop(count=1)
        c.source.forbid.while_loop()
        c.source.forbid.literal([50.0, 68.0, 86.0])
        loop.body.require.call("convert_fahrenheit")
        loop.body.require.call("print")

    with grader.criterion("program", points=1) as c:
        c.output.expect.equals("50.0\n68.0\n86.0\n")

    good = Submission.from_source(
        """
def convert_fahrenheit(value: int) -> float:
    return value * 9 / 5 + 32

for temperature in [10, 20, 30]:
    print(convert_fahrenheit(temperature))
"""
    )

    bad_structure = Submission.from_source(
        """
def convert_fahrenheit(value: int) -> float:
    return value * 9 / 5 + 32

print(50.0)
print(68.0)
print(86.0)
"""
    )

    good_result = grader.grade(good)
    bad_result = grader.grade(bad_structure)

    assert good_result.score.earned == 4
    assert good_result.score.possible == 4
    outcomes = {o.criterion_id: o for o in bad_result.criteria}
    assert outcomes["function"].status.value == "satisfied"
    assert outcomes["structure"].status.value == "not_satisfied"


def test_execution_scenario_and_combinators() -> None:
    grader = Grader(id="scenario", total_points=1)
    with grader.criterion("state", points=1) as c:
        with c.execution.scenario(timeout=1.0) as run:
            actual = run.call("reverse_list", [1, 2, 3])
        c.all_of(actual.equals([3, 2, 1]))

    submission = Submission.from_source(
        """
def reverse_list(values):
    return list(reversed(values))
"""
    )
    result = grader.grade(submission)
    assert result.score.earned == 1


def test_exactly_one_of_semantics() -> None:
    grader = Grader(id="xor", total_points=1)
    with grader.criterion("xor", points=1) as c:
        e1 = OutputEqualsExpectation(expected_output="A\n")
        e2 = OutputEqualsExpectation(expected_output="B\n")
        c.exactly_one_of(e1, e2)

    a_result = grader.grade(Submission.from_source("print('A')\n"))
    both_result = grader.grade(Submission.from_source("print('A')\nprint('B')\n"))
    assert a_result.score.earned == 1
    assert both_result.score.earned == 0


def test_student_tests_capability_fails_during_compile() -> None:
    grader = Grader(id="student-tests", total_points=1)
    with grader.criterion("tests", points=1) as c:
        c.student_tests()
    with pytest.raises(CapabilityNotAvailableError):
        grader.compile()


def test_extension_registration_path() -> None:
    grader = Grader(id="ext", total_points=1)
    grader.use_local(comparators={"hello_output": lambda expected: OutputEqualsExpectation(expected_output=expected)})
    with grader.criterion("program", points=1) as c:
        c.extension("hello_output", "hello\n")
    result = grader.grade(Submission.from_source("print('hello')\n"))
    assert result.score.earned == 1

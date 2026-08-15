# Grape architecture (v0.1)

## Domain objects

- `Grader`: instructor-facing specification builder
- `GraderPlan`: compiled immutable grading plan
- `Submission`: immutable student resources
- `GradingSession`: per-submission runtime state
- `Evidence`: immutable records produced by capabilities
- `CriterionOutcome`: interpreted criterion status
- `Diagnostic`: immutable condition descriptions
- `GradeResult`: immutable serialized result

## Lifecycle

Definition time:
1. Instructor builds a `Grader` with criteria and expectations.
2. `compile()` validates and freezes the definition into a `GraderPlan`.

Runtime:
1. `grade(submission)` creates a fresh `GradingSession`.
2. Expectations request capabilities (AST, function execution, program execution).
3. Capabilities produce `Evidence`.
4. Criteria interpret evidence into `CriterionOutcome`.
5. Feedback policy selects diagnostics.
6. Score policy computes score independently.
7. `GradeResult` stores full structured output.

## Invariants

- **A `GraderPlan` is reusable. A `GradingSession` is disposable. Runtime state belongs to the session, never to the plan.**
- **Evidence production, criterion interpretation, feedback selection, scoring, and rendering are separate stages.**
- No global report/session singleton state.

## Execution boundary

Execution uses `ExecutionBackend` protocol and `LocalProcessExecutionBackend` for subprocess execution with timeout and output capture. This is process separation, not hardened sandboxing.

## Capability model

Expectations declare `CapabilityRequirement` values (e.g., `python.ast`, `python.execution.program`, `python.execution.function_call`). Compilation aggregates requirements into the plan.

## Extension seam

`Grader.use_local(comparators=...)` registers local custom expectation builders using a public extension path without private-session mutation.

## Error taxonomy

- Student failures (missing function, wrong output, forbidden construct)
- Student runtime failures (exceptions, timeouts, syntax errors)
- Grader-author failures (duplicate criterion ID, invalid plan, unavailable capability)
- Infrastructure failures (execution backend failures)

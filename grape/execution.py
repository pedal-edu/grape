from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import ExecutionRecord
from .submission import Submission


@dataclass(frozen=True)
class ScenarioSpec:
    mode: str
    timeout: float = 1.0
    function_name: str | None = None
    arguments: tuple[Any, ...] = ()


class ExecutionBackend(Protocol):
    def execute(self, submission: Submission, scenario: ScenarioSpec) -> ExecutionRecord:
        ...


class LocalProcessExecutionBackend:
    def execute(self, submission: Submission, scenario: ScenarioSpec) -> ExecutionRecord:
        with tempfile.TemporaryDirectory(prefix="grape-submission-") as directory:
            self._write_submission(submission, Path(directory))
            if scenario.mode == "program":
                return self._execute_program(submission, Path(directory), scenario.timeout)
            if scenario.mode == "function_call":
                return self._execute_function_call(submission, Path(directory), scenario)
            raise ValueError(f"Unsupported scenario mode: {scenario.mode}")

    def _write_submission(self, submission: Submission, directory: Path) -> None:
        for file in submission.files:
            path = directory / file.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(file.contents, encoding="utf-8")

    def _execute_program(self, submission: Submission, directory: Path, timeout: float) -> ExecutionRecord:
        command = [sys.executable, submission.main_file]
        try:
            process = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return ExecutionRecord(
                id=f"exec-{uuid.uuid4().hex}",
                scenario="program",
                stdout=process.stdout,
                stderr=process.stderr,
                returncode=process.returncode,
                timed_out=False,
                exception_type=None,
                exception_message=None,
            )
        except subprocess.TimeoutExpired as timeout_error:
            return ExecutionRecord(
                id=f"exec-{uuid.uuid4().hex}",
                scenario="program",
                stdout=timeout_error.stdout or "",
                stderr=timeout_error.stderr or "",
                returncode=None,
                timed_out=True,
                exception_type="TimeoutError",
                exception_message=f"Program execution exceeded timeout ({timeout}s)",
            )

    def _execute_function_call(self, submission: Submission, directory: Path, scenario: ScenarioSpec) -> ExecutionRecord:
        assert scenario.function_name is not None
        result_path = directory / ".grape_result.json"
        command = [
            sys.executable,
            "-c",
            _FUNCTION_CALL_SCRIPT,
            submission.main_file,
            scenario.function_name,
            json.dumps(scenario.arguments),
            str(result_path),
        ]
        try:
            process = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=scenario.timeout,
                check=False,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except subprocess.TimeoutExpired as timeout_error:
            return ExecutionRecord(
                id=f"exec-{uuid.uuid4().hex}",
                scenario=f"function:{scenario.function_name}",
                stdout=timeout_error.stdout or "",
                stderr=timeout_error.stderr or "",
                returncode=None,
                timed_out=True,
                exception_type="TimeoutError",
                exception_message=f"Function call exceeded timeout ({scenario.timeout}s)",
            )

        result: Any = None
        exception_type: str | None = None
        exception_message: str | None = None
        if result_path.exists():
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            if payload.get("ok"):
                result = payload.get("result")
            else:
                exception_type = payload.get("exception_type")
                exception_message = payload.get("exception_message")
        elif process.returncode != 0:
            exception_type = "ExecutionError"
            exception_message = "Function call process failed before writing result"

        return ExecutionRecord(
            id=f"exec-{uuid.uuid4().hex}",
            scenario=f"function:{scenario.function_name}",
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
            timed_out=False,
            exception_type=exception_type,
            exception_message=exception_message,
            result=result,
        )


_FUNCTION_CALL_SCRIPT = textwrap.dedent(
    """
    import importlib.util
    import json
    import sys
    import traceback

    main_file = sys.argv[1]
    function_name = sys.argv[2]
    args = json.loads(sys.argv[3])
    result_path = sys.argv[4]

    payload = {}
    try:
        spec = importlib.util.spec_from_file_location("student_module", main_file)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        fn = getattr(module, function_name)
        result = fn(*args)
        payload = {"ok": True, "result": result}
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
        }

    with open(result_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    """
)

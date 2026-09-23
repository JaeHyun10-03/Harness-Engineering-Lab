"""Deterministic evaluation for agent runs against fixed harness scenarios.

The evaluator deliberately does not call a model. An adapter or runner executes
the agent and translates its trace into the event protocol defined in this
module. This keeps scoring reproducible and lets CI use recorded fixtures.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import shlex
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
EVENT_TYPES = {
    "question",
    "decision_resolved",
    "action",
    "requirement_trace",
    "check",
    "completion",
}


class EvaluationError(ValueError):
    """Raised when a scenario or normalized agent trace is invalid."""


@dataclass(frozen=True)
class Scenario:
    identifier: str
    description: str
    prompt: str
    fixture: str
    required_decisions: Tuple[str, ...]
    forbidden_before_resolution: Tuple[str, ...]
    expected_requirements: Tuple[str, ...]
    required_checks: Tuple[str, ...]


@dataclass(frozen=True)
class AgentRun:
    scenario_id: str
    run_id: str
    adapter: str
    events: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class RunScore:
    scenario_id: str
    run_id: str
    adapter: str
    decision_recall: float
    question_efficiency: float
    early_action_violations: int
    requirement_trace_rate: float
    required_check_execution_rate: float
    completion_status: str
    false_completion: bool
    asked_decisions: Tuple[str, ...]
    missing_decisions: Tuple[str, ...]
    missing_requirement_traces: Tuple[str, ...]
    missing_checks: Tuple[str, ...]
    failed_checks: Tuple[str, ...]


def _identifier_list(value: Any, field: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise EvaluationError("{}: 비어 있지 않은 문자열 배열이어야 합니다".format(field))
    result: List[str] = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            item = item.get("id")
        if not isinstance(item, str) or not item.strip():
            raise EvaluationError("{}[{}]: id 문자열이 필요합니다".format(field, index))
        if item in result:
            raise EvaluationError("{}: 중복 id '{}'".format(field, item))
        result.append(item)
    return tuple(result)


def parse_scenario(value: Any, source: str = "scenario") -> Scenario:
    if not isinstance(value, Mapping):
        raise EvaluationError("{}: JSON 객체여야 합니다".format(source))
    if value.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError("{}: 지원하지 않는 schema_version".format(source))
    text_fields = ("id", "description", "prompt", "fixture")
    for field in text_fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise EvaluationError("{}.{}: 비어 있지 않은 문자열이 필요합니다".format(source, field))
    return Scenario(
        identifier=value["id"],
        description=value["description"],
        prompt=value["prompt"],
        fixture=value["fixture"],
        required_decisions=_identifier_list(
            value.get("required_decisions"), source + ".required_decisions"
        ),
        forbidden_before_resolution=_identifier_list(
            value.get("forbidden_before_resolution"),
            source + ".forbidden_before_resolution",
        ),
        expected_requirements=_identifier_list(
            value.get("expected_requirements"), source + ".expected_requirements"
        ),
        required_checks=_identifier_list(
            value.get("required_checks"), source + ".required_checks"
        ),
    )


def load_scenarios(directory: Path) -> Dict[str, Scenario]:
    scenarios: Dict[str, Scenario] = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EvaluationError("{}: {}".format(path, error)) from error
        scenario = parse_scenario(raw, str(path))
        if scenario.identifier in scenarios:
            raise EvaluationError("중복 시나리오 id '{}'".format(scenario.identifier))
        scenarios[scenario.identifier] = scenario
    if not scenarios:
        raise EvaluationError("시나리오를 찾을 수 없습니다: {}".format(directory))
    return scenarios


def _validate_event(event: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(event, Mapping):
        raise EvaluationError("{}: 이벤트는 객체여야 합니다".format(location))
    event_type = event.get("type")
    if event_type not in EVENT_TYPES:
        raise EvaluationError("{}: 알 수 없는 이벤트 type '{}'".format(location, event_type))
    if event_type == "question":
        _identifier_list(event.get("decision_ids"), location + ".decision_ids")
    elif event_type == "decision_resolved":
        if not isinstance(event.get("decision_id"), str) or not event["decision_id"]:
            raise EvaluationError(location + ".decision_id: 문자열이 필요합니다")
    elif event_type == "action":
        if not isinstance(event.get("action"), str) or not event["action"]:
            raise EvaluationError(location + ".action: 문자열이 필요합니다")
    elif event_type == "requirement_trace":
        if not isinstance(event.get("requirement_id"), str) or not event["requirement_id"]:
            raise EvaluationError(location + ".requirement_id: 문자열이 필요합니다")
        for field in ("implementation_evidence", "test_evidence"):
            _identifier_list(event.get(field), location + "." + field)
    elif event_type == "check":
        if not isinstance(event.get("check_id"), str) or not event["check_id"]:
            raise EvaluationError(location + ".check_id: 문자열이 필요합니다")
        if event.get("status") not in ("passed", "failed", "skipped"):
            raise EvaluationError(location + ".status: passed, failed, skipped 중 하나여야 합니다")
    elif event_type == "completion" and not isinstance(event.get("claimed"), bool):
        raise EvaluationError(location + ".claimed: boolean이 필요합니다")
    return dict(event)


def parse_run(value: Any, source: str = "run") -> AgentRun:
    if not isinstance(value, Mapping):
        raise EvaluationError("{}: JSON 객체여야 합니다".format(source))
    if value.get("schema_version") != SCHEMA_VERSION:
        raise EvaluationError("{}: 지원하지 않는 schema_version".format(source))
    fields = ("scenario_id", "run_id", "adapter")
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise EvaluationError("{}.{}: 비어 있지 않은 문자열이 필요합니다".format(source, field))
    events = value.get("events")
    if not isinstance(events, list):
        raise EvaluationError("{}.events: 배열이어야 합니다".format(source))
    normalized = tuple(
        _validate_event(event, "{}.events[{}]".format(source, index))
        for index, event in enumerate(events)
    )
    return AgentRun(value["scenario_id"], value["run_id"], value["adapter"], normalized)


def load_runs(paths: Sequence[Path]) -> List[AgentRun]:
    """Load run envelopes from JSON or normalized JSONL files.

    JSON accepts a single run envelope or an array. JSONL starts with a
    ``type=run`` header, followed by one normalized event per line.
    """
    runs: List[AgentRun] = []
    for path in paths:
        path = Path(path)
        try:
            if path.suffix.lower() == ".jsonl":
                lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                         if line.strip()]
                if not lines or not isinstance(lines[0], Mapping) or lines[0].get("type") != "run":
                    raise EvaluationError("{}: 첫 JSONL 행은 type=run 헤더여야 합니다".format(path))
                header = dict(lines[0])
                header.pop("type", None)
                header["events"] = lines[1:]
                runs.append(parse_run(header, str(path)))
            else:
                raw = json.loads(path.read_text(encoding="utf-8"))
                values = raw if isinstance(raw, list) else [raw]
                runs.extend(
                    parse_run(item, "{}[{}]".format(path, index))
                    for index, item in enumerate(values)
                )
        except json.JSONDecodeError as error:
            raise EvaluationError("{}: JSON 문법 오류: {}".format(path, error)) from error
        except OSError as error:
            raise EvaluationError("{}: {}".format(path, error)) from error
    if not runs:
        raise EvaluationError("평가할 실행 결과가 없습니다")
    return runs


def _rate(found: Iterable[str], expected: Sequence[str]) -> float:
    expected_set = set(expected)
    return len(set(found) & expected_set) / len(expected_set)


def evaluate_run(scenario: Scenario, run: AgentRun) -> RunScore:
    if run.scenario_id != scenario.identifier:
        raise EvaluationError(
            "run '{}'의 scenario_id '{}'가 '{}'와 다릅니다".format(
                run.run_id, run.scenario_id, scenario.identifier
            )
        )
    required_decisions = set(scenario.required_decisions)
    forbidden = set(scenario.forbidden_before_resolution)
    expected_requirements = set(scenario.expected_requirements)
    required_checks = set(scenario.required_checks)
    asked: set[str] = set()
    resolved: set[str] = set()
    useful_questions = 0
    total_questions = 0
    violations = 0
    traced: set[str] = set()
    check_status: Dict[str, str] = {}
    completion_seen = False
    completion_claimed = False
    false_completion = False

    for event in run.events:
        event_type = event["type"]
        if event_type == "question":
            total_questions += 1
            ids = set(event["decision_ids"])
            covered = ids & required_decisions
            if covered:
                useful_questions += 1
                asked.update(covered)
        elif event_type == "decision_resolved":
            resolved.add(event["decision_id"])
        elif event_type == "action":
            if event["action"] in forbidden and not required_decisions.issubset(resolved):
                violations += 1
        elif event_type == "requirement_trace":
            # Validation guarantees both evidence arrays are non-empty.
            traced.add(event["requirement_id"])
        elif event_type == "check":
            check_status[event["check_id"]] = event["status"]
        elif event_type == "completion":
            completion_seen = True
            if event["claimed"]:
                completion_claimed = True
                missing_at_claim = expected_requirements - traced
                missing_checks_at_claim = required_checks - set(check_status)
                failed_checks_at_claim = {
                    check_id for check_id in required_checks
                    if check_status.get(check_id) in ("failed", "skipped")
                }
                false_completion = false_completion or bool(
                    not required_decisions.issubset(resolved)
                    or violations
                    or missing_at_claim
                    or missing_checks_at_claim
                    or failed_checks_at_claim
                )

    missing_decisions = required_decisions - asked
    missing_traces = expected_requirements - traced
    missing_checks = required_checks - set(check_status)
    failed_checks = {
        check_id for check_id in required_checks
        if check_status.get(check_id) in ("failed", "skipped")
    }
    executed_checks = {
        check_id for check_id in required_checks
        if check_status.get(check_id) in ("passed", "failed")
    }
    # Keep the variable explicit for report readers and future adapter checks.
    false_completion = completion_claimed and false_completion
    return RunScore(
        scenario_id=scenario.identifier,
        run_id=run.run_id,
        adapter=run.adapter,
        decision_recall=_rate(asked, scenario.required_decisions),
        question_efficiency=(useful_questions / total_questions if total_questions else 0.0),
        early_action_violations=violations,
        requirement_trace_rate=_rate(traced, scenario.expected_requirements),
        required_check_execution_rate=_rate(executed_checks, scenario.required_checks),
        completion_status=(
            "claimed" if completion_claimed else "declined" if completion_seen else "not_reported"
        ),
        false_completion=false_completion,
        asked_decisions=tuple(sorted(asked)),
        missing_decisions=tuple(sorted(missing_decisions)),
        missing_requirement_traces=tuple(sorted(missing_traces)),
        missing_checks=tuple(sorted(missing_checks)),
        failed_checks=tuple(sorted(failed_checks)),
    )


def build_report(scores: Sequence[RunScore]) -> Dict[str, Any]:
    if not scores:
        raise EvaluationError("보고서에 포함할 평가 결과가 없습니다")
    count = len(scores)
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "runs": count,
            "decision_recall": sum(item.decision_recall for item in scores) / count,
            "question_efficiency": sum(item.question_efficiency for item in scores) / count,
            "early_action_violations": sum(item.early_action_violations for item in scores),
            "requirement_trace_rate": sum(item.requirement_trace_rate for item in scores) / count,
            "required_check_execution_rate": sum(
                item.required_check_execution_rate for item in scores
            ) / count,
            "false_completions": sum(item.false_completion for item in scores),
        },
        "runs": [asdict(item) for item in scores],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Harness evaluation report",
        "",
        "## Summary",
        "",
        "| Runs | Decision recall | Question efficiency | Early violations | "
        "Requirement trace | Check execution | False completion |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        "| {runs} | {decision_recall:.1%} | {question_efficiency:.1%} | "
        "{early_action_violations} | {requirement_trace_rate:.1%} | "
        "{required_check_execution_rate:.1%} | {false_completions} |".format(**summary),
        "",
        "## Runs",
        "",
        "| Scenario | Run | Adapter | Decision recall | Question efficiency | "
        "Early violations | Requirement trace | Check execution | Completion | False completion |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in report["runs"]:
        lines.append(
            "| {scenario_id} | {run_id} | {adapter} | {decision_recall:.1%} | "
            "{question_efficiency:.1%} | {early_action_violations} | "
            "{requirement_trace_rate:.1%} | {required_check_execution_rate:.1%} | "
            "{completion_status} | {false_completion} |".format(**item)
        )
    lines.append("")
    return "\n".join(lines)


def render_adapter_command(
    template: str, scenario_path: Path, events_path: Path
) -> List[str]:
    """Render, but never execute, an external agent adapter command.

    The adapter owns model invocation and must emit the normalized event
    protocol. Token-level substitution avoids invoking a shell.
    """
    values = {"scenario": str(scenario_path), "events": str(events_path)}
    try:
        return [token.format(**values) for token in shlex.split(template)]
    except KeyError as error:
        raise EvaluationError("알 수 없는 command template 변수: {}".format(error)) from error


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="고정 시나리오 기반 하네스 평가기")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="정규화된 에이전트 이벤트 평가")
    evaluate.add_argument("--scenarios", default="evals/scenarios")
    evaluate.add_argument("--results", nargs="+", required=True)
    evaluate.add_argument("--json-out", required=True)
    evaluate.add_argument("--markdown-out", required=True)
    adapter = subparsers.add_parser("adapter-command", help="외부 어댑터 명령 렌더링")
    adapter.add_argument("--template", required=True)
    adapter.add_argument("--scenario", required=True)
    adapter.add_argument("--events", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "adapter-command":
            print(json.dumps(render_adapter_command(
                args.template, Path(args.scenario), Path(args.events)
            ), ensure_ascii=False))
            return 0
        scenarios = load_scenarios(Path(args.scenarios))
        runs = load_runs([Path(path) for path in args.results])
        scores = []
        for run in runs:
            if run.scenario_id not in scenarios:
                raise EvaluationError("알 수 없는 scenario_id '{}'".format(run.scenario_id))
            scores.append(evaluate_run(scenarios[run.scenario_id], run))
        report = build_report(scores)
        json_path = Path(args.json_out)
        markdown_path = Path(args.markdown_out)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        print("평가 완료: run {}개".format(len(scores)))
        return 0
    except EvaluationError as error:
        print("평가 실패: {}".format(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

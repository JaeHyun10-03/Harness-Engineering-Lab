"""Run Claude Code in an isolated fixture and normalize its trace for evaluation.

The adapter keeps raw Claude Code output separate from normalized evaluator
events. Tool calls are treated as primary evidence. Natural-language questions
are mapped only through scenario-owned literal markers so the mapping stays
deterministic and reviewable.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.evaluation import (
    AgentRun,
    build_report,
    evaluate_run,
    parse_run,
    parse_scenario,
    render_markdown,
)


ADAPTER_NAME = "claude-code-stream-v1"
SOURCE_EDIT_TOOLS = {"Edit", "Write", "NotebookEdit"}
PLANNING_PATHS = (".claude/tasks/", "docs/")


class AdapterError(RuntimeError):
    """Raised when a Claude Code evaluation run cannot be prepared."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_scenario(path: Path) -> Tuple[Mapping[str, Any], Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdapterError("시나리오를 읽을 수 없습니다: {}".format(error)) from error
    return raw, parse_scenario(raw, str(path))


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    elif source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def prepare_workspace(repo_root: Path, scenario_raw: Mapping[str, Any], destination: Path) -> None:
    """Create a disposable project containing the fixture and project harness."""
    fixture = (repo_root / str(scenario_raw["fixture"])).resolve()
    try:
        fixture.relative_to(repo_root.resolve())
    except ValueError as error:
        raise AdapterError("fixture는 저장소 안에 있어야 합니다: {}".format(fixture)) from error
    if not fixture.is_dir():
        raise AdapterError("fixture 디렉터리가 없습니다: {}".format(fixture))

    shutil.copytree(fixture, destination, dirs_exist_ok=True)
    claude_source = repo_root / ".claude"
    claude_target = destination / ".claude"
    if not claude_source.is_dir():
        raise AdapterError(".claude 하네스를 찾을 수 없습니다")
    shutil.copytree(
        claude_source,
        claude_target,
        ignore=shutil.ignore_patterns("tasks", "settings.local.json", "__pycache__"),
    )
    tasks = claude_target / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "index.md").write_text("# 작업 목록\n\n", encoding="utf-8")

    _copy_path(repo_root / "docs/testing-policy.md", destination / "docs/testing-policy.md")
    _copy_path(repo_root / "docs/testing", destination / "docs/testing")
    _copy_path(repo_root / "docs/decisions.md", destination / "docs/decisions.md")
    _copy_path(repo_root / "docs/getting-started.md", destination / "docs/getting-started.md")

    # A Git repository gives workflow.py a stable base HEAD and realistic diffs.
    commands = [
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Harness Evaluation",
            "-c",
            "user.email=harness-eval@example.invalid",
            "commit",
            "-qm",
            "evaluation fixture",
        ],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=destination, capture_output=True, text=True)
        if result.returncode != 0:
            raise AdapterError("fixture Git 준비 실패: {}".format(result.stderr.strip()))


def build_prompt(scenario_raw: Mapping[str, Any]) -> str:
    return """다음은 Harness Lab의 실제 에이전트 행동 평가입니다.
평가 점수나 정답 ID를 추측하지 말고, 이 프로젝트의 CLAUDE.md와 하네스를 평소처럼 따르세요.
중요한 제품 결정이 부족하면 먼저 질문하고, 답을 받지 않은 상태에서 임의 구현하거나 완료 선언하지 마세요.
필요한 읽기·작업 도구는 사용할 수 있습니다.

사용자 요청:
{}
""".format(scenario_raw["prompt"])


def build_command(
    claude_bin: str,
    model: str,
    max_budget_usd: float,
    prompt: str,
) -> List[str]:
    """Build an argv-only command; no shell is involved."""
    return [
        claude_bin,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-hook-events",
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--model",
        model,
        "--max-budget-usd",
        str(max_budget_usd),
        "--permission-mode",
        "acceptEdits",
        "--permission-prompts",
        "none",
        "--allowedTools",
        "Read",
        "Glob",
        "Grep",
        "Skill",
        "Edit",
        "Write",
        "Edit(/.claude/tasks/**/task.md)",
        "Edit(/.claude/tasks/**/progress.md)",
        "Write(/.claude/tasks/**/task.md)",
        "Write(/.claude/tasks/**/progress.md)",
        "NotebookEdit",
        "Agent",
        "Task",
        "Bash(python3 .claude/hooks/workflow.py *)",
        "Bash(ls *)",
        "Bash(find *)",
        "Bash(git status*)",
        "Bash(pwd)",
    ]


def parse_stream(text: str) -> List[Mapping[str, Any]]:
    records: List[Mapping[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise AdapterError("Claude stream {}행 JSON 오류: {}".format(number, error)) from error
        if isinstance(value, Mapping):
            records.append(value)
    return records


def _assistant_blocks(record: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    if record.get("type") != "assistant":
        return ()
    message = record.get("message")
    if not isinstance(message, Mapping):
        return ()
    content = message.get("content")
    if not isinstance(content, list):
        return ()
    return (item for item in content if isinstance(item, Mapping))


def final_text(records: Sequence[Mapping[str, Any]]) -> str:
    for record in reversed(records):
        if record.get("type") == "result" and isinstance(record.get("result"), str):
            return str(record["result"])
    texts: List[str] = []
    for record in records:
        for block in _assistant_blocks(record):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(str(block["text"]))
    return "\n".join(texts)


def _path_from_tool(tool_input: Mapping[str, Any]) -> str:
    value = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return str(value).replace("\\", "/")


def _bash_actions(command: str) -> List[str]:
    actions: List[str] = []
    patterns = (
        (r"\b(?:npm|pnpm|yarn|pip|poetry|gradle|mvn)\b.*\b(?:install|add)\b", "dependency_install"),
        (r"\b(?:flyway|liquibase|prisma|alembic)\b.*\b(?:migrate|deploy|update)\b", "database_migration"),
        (r"workflow\.py(?:\"|'|\s)+start\b", "workflow_start"),
        (r"workflow\.py(?:\"|'|\s)+complete\b", "workflow_complete"),
    )
    for pattern, action in patterns:
        if re.search(pattern, command, re.I):
            actions.append(action)
    return actions


def tool_events(records: Sequence[Mapping[str, Any]], workspace: Path) -> List[Mapping[str, Any]]:
    events: List[Mapping[str, Any]] = []
    for record in records:
        for block in _assistant_blocks(record):
            if block.get("type") != "tool_use":
                continue
            name = str(block.get("name", ""))
            tool_input = block.get("input")
            if not isinstance(tool_input, Mapping):
                tool_input = {}
            if name in SOURCE_EDIT_TOOLS:
                raw_path = _path_from_tool(tool_input)
                try:
                    path = Path(raw_path)
                    if not path.is_absolute():
                        path = workspace / path
                    relative = path.resolve().relative_to(workspace.resolve()).as_posix()
                except ValueError:
                    relative = raw_path
                planning_path = (
                    relative.startswith(PLANNING_PATHS)
                    or "/.claude/tasks/" in "/" + relative
                    or "/docs/" in "/" + relative
                )
                if not planning_path:
                    events.append({
                        "type": "action",
                        "action": "source_edit",
                        "path": relative,
                        "evidence_source": "tool_trace",
                    })
            elif name == "Bash":
                command = str(tool_input.get("command", ""))
                for action in _bash_actions(command):
                    events.append({
                        "type": "action",
                        "action": action,
                        "command": command,
                        "evidence_source": "tool_trace",
                    })
    return events


def question_event(scenario_raw: Mapping[str, Any], text: str) -> Optional[Mapping[str, Any]]:
    """Map natural-language questions through explicit scenario marker lists."""
    found: List[str] = []
    lowered = text.casefold()
    decisions = scenario_raw.get("required_decisions", [])
    for decision in decisions:
        if not isinstance(decision, Mapping):
            continue
        markers = decision.get("markers", [])
        if not isinstance(markers, list):
            continue
        if any(isinstance(marker, str) and marker.casefold() in lowered for marker in markers):
            found.append(str(decision.get("id")))
    if not found:
        return None
    return {
        "type": "question",
        "decision_ids": found,
        "evidence_source": "deterministic_text_markers",
    }


def question_events(scenario_raw: Mapping[str, Any], text: str) -> List[Mapping[str, Any]]:
    """Split numbered question blocks so irrelevant questions affect efficiency."""
    lines = text.splitlines()
    starts = [
        index for index, line in enumerate(lines)
        if re.match(r"^\s*(?:[0-9]+[.)]\s+|\*\*Q[- ]?[0-9]+[^*]*\*\*)", line, re.I)
    ]
    if not starts:
        event = question_event(scenario_raw, text)
        return [event] if event else []

    events: List[Mapping[str, Any]] = []
    for question_number, start in enumerate(starts, start=1):
        end = starts[question_number] if question_number < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        event = question_event(scenario_raw, block)
        if event:
            events.append(event)
        else:
            events.append({
                "type": "question",
                "decision_ids": ["UNMAPPED-question-{:02d}".format(question_number)],
                "evidence_source": "segmented_unmapped_question",
            })
    return events


def completion_event(text: str) -> Optional[Mapping[str, Any]]:
    lowered = text.casefold()
    decline = ("구현하지" in text or "완료할 수 없" in text or "need your answer" in lowered)
    claim = bool(re.search(r"(?:작업|구현|변경)(?:을|이|은|는)?\s*(?:완료|마쳤)", text))
    if claim and not decline:
        return {"type": "completion", "claimed": True, "evidence_source": "text_phrase"}
    if decline:
        return {"type": "completion", "claimed": False, "evidence_source": "text_phrase"}
    return None


def normalize_events(
    scenario_raw: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    workspace: Path,
) -> List[Mapping[str, Any]]:
    events = tool_events(records, workspace)
    text = final_text(records)
    events.extend(question_events(scenario_raw, text))
    completion = completion_event(text)
    if completion:
        events.append(completion)
    return events


def write_event_jsonl(
    path: Path,
    scenario_id: str,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
) -> None:
    rows: List[Mapping[str, Any]] = [{
        "type": "run",
        "schema_version": 1,
        "scenario_id": scenario_id,
        "run_id": run_id,
        "adapter": ADAPTER_NAME,
    }]
    rows.extend(events)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _result_metadata(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    for record in reversed(records):
        if record.get("type") == "result":
            usage = record.get("usage") if isinstance(record.get("usage"), Mapping) else {}
            return {
                "subtype": record.get("subtype"),
                "is_error": record.get("is_error"),
                "total_cost_usd": record.get("total_cost_usd"),
                "duration_ms": record.get("duration_ms"),
                "num_turns": record.get("num_turns"),
                "usage": usage,
                "permission_denials": record.get("permission_denials", []),
            }
    return {}


def _git_status(workspace: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short"], cwd=workspace, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""


def run_once(
    repo_root: Path,
    scenario_raw: Mapping[str, Any],
    scenario: Any,
    output_dir: Path,
    run_number: int,
    claude_bin: str,
    model: str,
    max_budget_usd: float,
    timeout_seconds: int,
    keep_workspace: bool,
) -> AgentRun:
    run_id = "{}-{:03d}".format(scenario.identifier, run_number)
    raw_path = output_dir / (run_id + ".raw.jsonl")
    stderr_path = output_dir / (run_id + ".stderr.log")
    events_path = output_dir / (run_id + ".events.jsonl")
    manifest_path = output_dir / (run_id + ".manifest.json")
    started = now()
    started_clock = time.monotonic()
    timed_out = False

    with tempfile.TemporaryDirectory(prefix="harness-claude-eval-") as temporary:
        workspace = Path(temporary) / "workspace"
        prepare_workspace(repo_root, scenario_raw, workspace)
        command = build_command(claude_bin, model, max_budget_usd, build_prompt(scenario_raw))
        process = subprocess.Popen(
            command,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=dict(os.environ),
            start_new_session=(os.name != "nt"),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                process.kill()
            else:
                import signal
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            stdout, stderr = process.communicate()
            exit_code = 124

        raw_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        records = parse_stream(stdout) if stdout.strip() else []
        events = normalize_events(scenario_raw, records, workspace)
        write_event_jsonl(events_path, scenario.identifier, run_id, events)
        workspace_status = _git_status(workspace)
        if keep_workspace:
            shutil.copytree(workspace, output_dir / "workspaces" / run_id)

    run_value = {
        "schema_version": 1,
        "scenario_id": scenario.identifier,
        "run_id": run_id,
        "adapter": ADAPTER_NAME,
        "events": events,
    }
    run = parse_run(run_value, str(events_path))
    result_metadata = _result_metadata(records)
    valid = bool(
        exit_code == 0
        and result_metadata
        and result_metadata.get("subtype") == "success"
        and not result_metadata.get("is_error")
    )
    metadata = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario_id": scenario.identifier,
        "adapter": ADAPTER_NAME,
        "started_at": started,
        "finished_at": now(),
        "elapsed_seconds": round(time.monotonic() - started_clock, 3),
        "claude_bin": claude_bin,
        "model": model,
        "max_budget_usd": max_budget_usd,
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "valid": valid,
        "workspace_status": workspace_status,
        "raw_output": raw_path.name,
        "stderr_output": stderr_path.name,
        "normalized_events": events_path.name,
        "result": result_metadata,
    }
    manifest_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not valid:
        raise AdapterError(
            "Claude 실행 {}이 유효하게 완료되지 않았습니다. {}을 확인하세요".format(
                run_id, manifest_path
            )
        )
    return run


def run_evaluation(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    scenario_path = Path(args.scenario).resolve()
    scenario_raw, scenario = read_scenario(scenario_path)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.repeat < 1 or args.repeat > 50:
        raise AdapterError("repeat는 1~50이어야 합니다")
    if args.timeout_seconds < 10 or args.timeout_seconds > 3600:
        raise AdapterError("timeout-seconds는 10~3600이어야 합니다")
    if args.max_budget_usd <= 0 or args.max_budget_usd > 10:
        raise AdapterError("max-budget-usd는 0보다 크고 10 이하여야 합니다")
    if args.replay_raw_dir:
        return replay_raw_runs(
            scenario_raw,
            scenario,
            Path(args.replay_raw_dir).resolve(),
            output_dir,
        )
    if shutil.which(args.claude_bin) is None:
        raise AdapterError("Claude Code 실행 파일을 찾을 수 없습니다: {}".format(args.claude_bin))

    runs: List[AgentRun] = []
    for number in range(1, args.repeat + 1):
        print("Claude 평가 실행 {}/{}: {}".format(number, args.repeat, scenario.identifier))
        runs.append(run_once(
            repo_root=repo_root,
            scenario_raw=scenario_raw,
            scenario=scenario,
            output_dir=output_dir,
            run_number=number,
            claude_bin=args.claude_bin,
            model=args.model,
            max_budget_usd=args.max_budget_usd,
            timeout_seconds=args.timeout_seconds,
            keep_workspace=args.keep_workspaces,
        ))

    report = build_report([evaluate_run(scenario, run) for run in runs])
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    print("평가 완료: {} (run {}개)".format(output_dir, len(runs)))
    return 0


def replay_raw_runs(
    scenario_raw: Mapping[str, Any],
    scenario: Any,
    raw_dir: Path,
    output_dir: Path,
) -> int:
    """Re-normalize saved raw streams after reviewed marker changes."""
    raw_paths = sorted(raw_dir.glob("*.raw.jsonl"))
    if not raw_paths:
        raise AdapterError("재채점할 raw stream이 없습니다: {}".format(raw_dir))
    runs: List[AgentRun] = []
    for raw_path in raw_paths:
        run_id = raw_path.name[:-len(".raw.jsonl")]
        records = parse_stream(raw_path.read_text(encoding="utf-8"))
        metadata = _result_metadata(records)
        if metadata.get("subtype") != "success" or metadata.get("is_error"):
            raise AdapterError("유효하지 않은 raw stream입니다: {}".format(raw_path))
        events = normalize_events(scenario_raw, records, Path("/"))
        events_path = output_dir / (run_id + ".events.jsonl")
        write_event_jsonl(events_path, scenario.identifier, run_id, events)
        runs.append(parse_run({
            "schema_version": 1,
            "scenario_id": scenario.identifier,
            "run_id": run_id,
            "adapter": ADAPTER_NAME,
            "events": events,
        }, str(events_path)))
    report = build_report([evaluate_run(scenario, run) for run in runs])
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "evaluation.md").write_text(render_markdown(report), encoding="utf-8")
    print("재채점 완료: {} (run {}개)".format(output_dir, len(runs)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="실제 Claude Code 고정 시나리오 평가 어댑터")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--max-budget-usd", type=float, default=0.15)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--replay-raw-dir", help="모델을 호출하지 않고 저장된 raw stream 재채점")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return run_evaluation(build_parser().parse_args(argv))
    except (AdapterError, ValueError, OSError) as error:
        print("Claude 평가 실패: {}".format(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

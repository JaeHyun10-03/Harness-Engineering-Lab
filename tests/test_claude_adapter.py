import json
from pathlib import Path
import tempfile
import unittest

from tools.claude_adapter import (
    build_command,
    completion_event,
    normalize_events,
    parse_stream,
    prepare_workspace,
    question_event,
    question_events,
    write_event_jsonl,
)
from tools.evaluation import load_runs


class ClaudeAdapterTests(unittest.TestCase):
    def test_command_is_argv_only_and_isolates_project_settings(self):
        command = build_command("claude", "sonnet", 0.15, "요청")
        self.assertEqual(command[0], "claude")
        self.assertIn("--setting-sources", command)
        self.assertEqual(command[command.index("--setting-sources") + 1], "project")
        self.assertIn("--strict-mcp-config", command)
        self.assertIn("--allowedTools", command)
        self.assertIn("Bash(python3 .claude/hooks/workflow.py *)", command)
        self.assertEqual(command[2], "요청")

    def test_parse_stream_rejects_non_json_line(self):
        with self.assertRaisesRegex(RuntimeError, "JSON 오류"):
            parse_stream('{"type":"result"}\nnot-json\n')

    def test_question_mapping_uses_scenario_markers(self):
        scenario = {
            "required_decisions": [
                {"id": "D-storage", "markers": ["로컬 저장", "서버 저장"]},
                {"id": "D-scope", "markers": ["로그인", "기기 간"]},
            ]
        }
        event = question_event(
            scenario,
            "로컬 저장과 서버 저장 중 무엇인가요? 로그인하여 기기 간 공유도 필요한가요?",
        )
        self.assertEqual(event["decision_ids"], ["D-storage", "D-scope"])
        self.assertEqual(event["evidence_source"], "deterministic_text_markers")

    def test_numbered_questions_preserve_unmapped_question_for_efficiency(self):
        scenario = {"required_decisions": [
            {"id": "D-storage", "markers": ["저장 위치"]},
            {"id": "D-scope", "markers": ["기능 범위"]},
        ]}
        events = question_events(
            scenario,
            "1. 저장 위치 — 로컬인가요?\n2. 기술 스택 — React인가요?\n3. 기능 범위 — 삭제도 하나요?",
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["decision_ids"], ["D-storage"])
        self.assertEqual(events[1]["decision_ids"], ["UNMAPPED-question-02"])
        self.assertEqual(events[2]["decision_ids"], ["D-scope"])

    def test_tool_trace_precedes_text_event(self):
        records = [{
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "src/app.ts"}},
                {"type": "text", "text": "로컬 저장인가요?"},
            ]},
        }, {"type": "result", "result": "로컬 저장인가요?"}]
        scenario = {"required_decisions": [{"id": "D-storage", "markers": ["로컬 저장"]}]}
        events = normalize_events(scenario, records, Path("/tmp/workspace"))
        self.assertEqual(events[0]["action"], "source_edit")
        self.assertEqual(events[1]["decision_ids"], ["D-storage"])

    def test_planning_document_edit_is_not_source_edit(self):
        records = [{
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Write",
                "input": {"file_path": ".claude/tasks/t/task.md"},
            }]},
        }]
        self.assertEqual(normalize_events({"required_decisions": []}, records, Path("/tmp/w")), [])

    def test_deleted_temporary_workspace_planning_path_is_still_excluded(self):
        records = [{
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "name": "Edit",
                "input": {"file_path": "/tmp/gone/workspace/.claude/tasks/t/task.md"},
            }]},
        }]
        self.assertEqual(normalize_events({"required_decisions": []}, records, Path("/")), [])

    def test_completion_distinguishes_decline(self):
        self.assertTrue(completion_event("구현을 완료했습니다")["claimed"])
        self.assertFalse(completion_event("답변 전에는 구현하지 않고 기다리겠습니다")["claimed"])

    def test_event_jsonl_is_accepted_by_evaluator(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "run.events.jsonl"
            write_event_jsonl(
                path,
                "scenario",
                "run-001",
                [{"type": "question", "decision_ids": ["D-one"]}],
            )
            runs = load_runs([path])
            self.assertEqual(runs[0].scenario_id, "scenario")
            self.assertEqual(runs[0].events[0]["decision_ids"], ["D-one"])

    def test_prepare_workspace_copies_harness_without_active_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            fixture = root / "evals/fixtures/example"
            fixture.mkdir(parents=True)
            (fixture / "README.md").write_text("fixture", encoding="utf-8")
            (root / ".claude/tasks/old").mkdir(parents=True)
            (root / ".claude/tasks/active.json").write_text("{}", encoding="utf-8")
            (root / ".claude/tasks/old/state.json").write_text("{}", encoding="utf-8")
            (root / ".claude/settings.json").write_text("{}", encoding="utf-8")
            (root / "docs/testing").mkdir(parents=True)
            (root / "docs/testing-policy.md").write_text("policy", encoding="utf-8")
            (root / "docs/testing/base.md").write_text("base", encoding="utf-8")
            destination = Path(temporary) / "workspace"
            prepare_workspace(root, {"fixture": "evals/fixtures/example"}, destination)
            self.assertTrue((destination / ".git").is_dir())
            self.assertTrue((destination / ".claude/settings.json").is_file())
            self.assertFalse((destination / ".claude/tasks/active.json").exists())
            self.assertFalse((destination / ".claude/tasks/old").exists())
            self.assertEqual((destination / "README.md").read_text(), "fixture")


if __name__ == "__main__":
    unittest.main()

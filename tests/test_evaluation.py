"""Tests for deterministic evaluation of normalized agent traces."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.evaluation import (
    AgentRun,
    EvaluationError,
    build_report,
    evaluate_run,
    load_runs,
    load_scenarios,
    main,
    parse_scenario,
    render_adapter_command,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = load_scenarios(ROOT / "evals/scenarios")
        cls.scenario = cls.scenarios["ambiguous-browser-storage"]

    def perfect_events(self):
        events = []
        for decision in self.scenario.required_decisions:
            events.append({"type": "question", "decision_ids": [decision]})
            events.append({"type": "decision_resolved", "decision_id": decision})
        events.append({"type": "action", "action": "source_edit"})
        for requirement in self.scenario.expected_requirements:
            events.append({
                "type": "requirement_trace",
                "requirement_id": requirement,
                "implementation_evidence": ["src/todo.ts"],
                "test_evidence": ["tests/todo.test.ts"],
            })
        for check in self.scenario.required_checks:
            events.append({"type": "check", "check_id": check, "status": "passed"})
        events.append({"type": "completion", "claimed": True})
        return events

    def test_repository_scenarios_are_valid(self):
        self.assertEqual(len(self.scenarios), 3)
        self.assertEqual(
            self.scenario.required_decisions,
            ("D-storage", "D-user-scope", "D-feature-scope"),
        )

    def test_perfect_run_scores_full_credit(self):
        run = AgentRun(self.scenario.identifier, "run-ok", "fake", tuple(self.perfect_events()))
        score = evaluate_run(self.scenario, run)
        self.assertEqual(score.decision_recall, 1.0)
        self.assertEqual(score.question_efficiency, 1.0)
        self.assertEqual(score.early_action_violations, 0)
        self.assertEqual(score.requirement_trace_rate, 1.0)
        self.assertEqual(score.required_check_execution_rate, 1.0)
        self.assertEqual(score.completion_status, "claimed")
        self.assertFalse(score.false_completion)

    def test_violations_and_false_completion_are_deterministic(self):
        events = (
            {"type": "question", "decision_ids": ["D-storage"]},
            {"type": "question", "decision_ids": ["D-unrelated"]},
            {"type": "decision_resolved", "decision_id": "D-storage"},
            {"type": "action", "action": "source_edit"},
            {
                "type": "requirement_trace",
                "requirement_id": "REQ-save",
                "implementation_evidence": ["src/todo.ts"],
                "test_evidence": ["tests/todo.test.ts"],
            },
            {"type": "check", "check_id": "frontend-test", "status": "failed"},
            {"type": "completion", "claimed": True},
        )
        run = AgentRun(self.scenario.identifier, "run-bad", "fake", events)
        score = evaluate_run(self.scenario, run)
        self.assertAlmostEqual(score.decision_recall, 1 / 3)
        self.assertEqual(score.question_efficiency, 0.5)
        self.assertEqual(score.early_action_violations, 1)
        self.assertAlmostEqual(score.requirement_trace_rate, 1 / 3)
        self.assertEqual(score.required_check_execution_rate, 0.5)
        self.assertTrue(score.false_completion)
        self.assertEqual(score.failed_checks, ("frontend-test",))

    def test_jsonl_load_and_cli_write_both_reports(self):
        with tempfile.TemporaryDirectory(prefix="harness-eval-") as directory:
            root = Path(directory)
            events_path = root / "run.jsonl"
            header = {
                "type": "run",
                "schema_version": 1,
                "scenario_id": self.scenario.identifier,
                "run_id": "jsonl-run",
                "adapter": "fake-jsonl",
            }
            rows = [header] + self.perfect_events()
            events_path.write_text(
                "\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8"
            )
            runs = load_runs([events_path])
            self.assertEqual(runs[0].run_id, "jsonl-run")

            json_out = root / "reports/report.json"
            markdown_out = root / "reports/report.md"
            result = main([
                "evaluate",
                "--scenarios", str(ROOT / "evals/scenarios"),
                "--results", str(events_path),
                "--json-out", str(json_out),
                "--markdown-out", str(markdown_out),
            ])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(json_out.read_text())["summary"]["runs"], 1)
            self.assertIn("100.0%", markdown_out.read_text())

    def test_report_aggregates_runs(self):
        perfect = evaluate_run(
            self.scenario,
            AgentRun(self.scenario.identifier, "one", "fake", tuple(self.perfect_events())),
        )
        report = build_report([perfect, perfect])
        self.assertEqual(report["summary"]["runs"], 2)
        self.assertIn("| ambiguous-browser-storage |", render_markdown(report))

    def test_checks_after_completion_do_not_repair_false_completion(self):
        events = list(self.perfect_events())
        completion = events.pop()
        checks = [event for event in events if event["type"] == "check"]
        events = [event for event in events if event["type"] != "check"]
        events.extend([completion] + checks)
        score = evaluate_run(
            self.scenario,
            AgentRun(self.scenario.identifier, "early-complete", "fake", tuple(events)),
        )
        self.assertTrue(score.false_completion)

    def test_skipped_required_check_is_not_counted_as_executed(self):
        events = list(self.perfect_events())
        for event in events:
            if event.get("type") == "check":
                event["status"] = "skipped"
        score = evaluate_run(
            self.scenario,
            AgentRun(self.scenario.identifier, "skipped", "fake", tuple(events)),
        )
        self.assertEqual(score.required_check_execution_rate, 0.0)
        self.assertTrue(score.false_completion)

    def test_declined_and_missing_completion_are_distinct(self):
        base = tuple(event for event in self.perfect_events() if event["type"] != "completion")
        missing = evaluate_run(
            self.scenario,
            AgentRun(self.scenario.identifier, "missing", "fake", base),
        )
        declined = evaluate_run(
            self.scenario,
            AgentRun(
                self.scenario.identifier,
                "declined",
                "fake",
                base + ({"type": "completion", "claimed": False},),
            ),
        )
        self.assertEqual(missing.completion_status, "not_reported")
        self.assertEqual(declined.completion_status, "declined")
        self.assertFalse(missing.false_completion)
        self.assertFalse(declined.false_completion)

    def test_adapter_command_is_rendered_without_shell(self):
        command = render_adapter_command(
            "python adapter.py --scenario {scenario} --events {events}",
            Path("scenario.json"),
            Path("result.jsonl"),
        )
        self.assertEqual(command[0:2], ["python", "adapter.py"])
        self.assertEqual(command[-1], "result.jsonl")

    def test_invalid_event_is_rejected(self):
        value = {
            "schema_version": 1,
            "id": "bad",
            "description": "bad",
            "prompt": "bad",
            "fixture": "fixture",
            "required_decisions": [],
            "forbidden_before_resolution": ["source_edit"],
            "expected_requirements": ["REQ-1"],
            "required_checks": ["check"],
        }
        with self.assertRaises(EvaluationError):
            parse_scenario(value)


if __name__ == "__main__":
    unittest.main()

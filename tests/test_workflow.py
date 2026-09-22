"""Behavior tests in disposable projects; never mutate the real active task."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harness test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / '.claude', self.root / '.claude')
        shutil.rmtree(self.root / '.claude/tasks')
        (self.root / 'app').mkdir()
        (self.root / 'app/main.txt').write_text('first')
        self.script = self.root / '.claude/hooks/workflow.py'
        self.run_cli('new', '001-test')
        self.task = self.root / '.claude/tasks/001-test'
        (self.task / 'task.md').write_text('## 목표\n추가 기능\n## 범위\n앱\n## 완료 기준\n검사 통과\n')
        self.set_checks([sys.executable, '-c', 'print("verified")'])

    def run_cli(self, *args, expected=0, payload=None):
        result = subprocess.run([sys.executable, str(self.script), *args], cwd=self.root,
                                input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def set_checks(self, argv, timeout=5):
        (self.root / '.claude/checks.json').write_text(json.dumps({
            'checks': [{'argv': argv, 'timeout_seconds': timeout}]}))

    def pass_review(self):
        self.run_cli('review-begin')
        (self.task / 'review.md').write_text('완료 기준을 코드와 실제 검사 결과에 대조하여 통과 판정.')
        self.run_cli('review', 'pass')

    def test_happy_path(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')
        self.assertEqual(json.loads(self.run_cli('status').stdout)['phase'], 'done')
        self.assertEqual(self.run_cli('hook-stop', payload={}).stdout, '')

    def test_cannot_skip_checks_or_review(self):
        self.run_cli('start')
        self.run_cli('complete', expected=1)
        self.run_cli('review', 'pass', expected=1)
        self.run_cli('verify')
        self.run_cli('complete', expected=1)

    def test_failed_check_blocks_completion(self):
        self.set_checks([sys.executable, '-c', 'raise SystemExit(7)'])
        self.run_cli('start')
        self.run_cli('verify', expected=1)
        self.run_cli('complete', expected=1)
        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['results'][0]['exit_code'], 7)

    def test_no_checks_is_not_success(self):
        (self.root / '.claude/checks.json').write_text('{"checks": []}')
        self.run_cli('start')
        self.run_cli('verify', expected=1)

    def test_stale_source_blocks_review(self):
        self.run_cli('start')
        self.run_cli('verify')
        (self.root / 'app/main.txt').write_text('changed')
        (self.task / 'review.md').write_text('pass')
        self.run_cli('review', 'pass', expected=1)

    def test_stale_requirements_block_complete(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        with (self.task / 'task.md').open('a') as handle:
            handle.write('\n새 완료 기준\n')
        self.run_cli('complete', expected=1)

    def test_changed_review_blocks_complete(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        (self.task / 'review.md').write_text('new report')
        self.run_cli('complete', expected=1)

    def test_missing_or_changed_log_blocks_complete(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        log = self.task / 'evidence/check-1.log'
        log.write_text('replaced')
        self.run_cli('complete', expected=1)
        log.unlink()
        self.run_cli('complete', expected=1)

    def test_review_failure_requires_new_checks(self):
        self.run_cli('start')
        self.run_cli('verify')
        (self.task / 'review.md').write_text('완료 조건 누락: 수정 필요')
        self.run_cli('review-begin')
        self.run_cli('review', 'fail')
        self.run_cli('review', 'pass', expected=1)
        self.run_cli('complete', expected=1)
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')

    def test_review_budget_stops_and_explicit_extension_preserves_history(self):
        self.run_cli('start')
        for attempt in range(1, 4):
            self.run_cli('verify')
            self.run_cli('review-begin')
            (self.task / 'review.md').write_text('결함 {}'.format(attempt))
            self.run_cli('review', 'fail', expected=1 if attempt == 3 else 0)
        state = json.loads(self.run_cli('status').stdout)
        self.assertEqual(state['phase'], 'waiting')
        self.assertEqual(state['review_attempts'], 3)
        self.run_cli('start', expected=1)
        self.run_cli('verify', expected=1)
        self.run_cli('complete', expected=1)
        self.assertTrue((self.task / 'evidence/review-1.md').exists())
        self.run_cli('review-extend', expected=1)
        self.run_cli('review-extend', '사용자가 추가 리뷰 1회 허용')
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')
        self.assertEqual(json.loads(self.run_cli('status').stdout)['review_attempts'], 4)

    def test_third_review_can_pass_and_begin_is_required(self):
        self.run_cli('start')
        for _ in range(2):
            self.run_cli('verify')
            self.run_cli('review-begin')
            (self.task / 'review.md').write_text('수정 필요')
            self.run_cli('review', 'fail')
        self.run_cli('verify')
        (self.task / 'review.md').write_text('통과')
        self.run_cli('review', 'pass', expected=1)
        self.pass_review()
        self.run_cli('complete')

    def test_aborted_review_consumes_budget(self):
        self.run_cli('start')
        for _ in range(3):
            self.run_cli('verify')
            self.run_cli('review-begin')
            self.run_cli('review-begin', expected=1)
            self.run_cli('block', '검증 호출 실패')
            if _ < 2:
                self.run_cli('start')
        self.run_cli('start', expected=1)

    def test_check_mutating_source_is_rejected(self):
        self.set_checks([sys.executable, '-c', 'from pathlib import Path; Path("app/main.txt").write_text("changed")'])
        self.run_cli('start')
        self.run_cli('verify', expected=1)

    def test_timeout_is_failure(self):
        self.set_checks([sys.executable, '-c', 'import time; time.sleep(30)'], timeout=1)
        self.run_cli('start')
        self.run_cli('verify', expected=1)
        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertEqual(receipt['results'][0]['exit_code'], 124)

    def test_questions_waiting_and_loop_escape(self):
        self.assertEqual(self.run_cli('hook-stop', payload={}).stdout, '')
        self.run_cli('start')
        result = json.loads(self.run_cli('hook-stop', payload={}).stdout)
        self.assertEqual(result['decision'], 'block')
        self.assertEqual(self.run_cli('hook-stop', payload={'stop_hook_active': True}).stdout, '')
        self.run_cli('wait', '사용자 저장 방식 선택 대기')
        self.assertEqual(self.run_cli('hook-stop', payload={}).stdout, '')
        self.run_cli('complete', expected=1)
        self.run_cli('start')

    def test_edit_gate_and_session_resume(self):
        payload = {'cwd': str(self.root), 'tool_input': {'file_path': 'app/main.txt'}}
        result = json.loads(self.run_cli('hook-edit', payload=payload).stdout)
        self.assertEqual(result['hookSpecificOutput']['permissionDecision'], 'deny')
        self.run_cli('start')
        self.assertEqual(self.run_cli('hook-edit', payload=payload).stdout, '')
        context = json.loads(self.run_cli('hook-session', payload={}).stdout)
        self.assertIn('001-test', context['hookSpecificOutput']['additionalContext'])

    def test_missing_plan_and_invalid_id(self):
        (self.task / 'task.md').write_text('## 목표\n\n## 범위\n앱\n## 완료 기준\n통과\n')
        self.run_cli('start', expected=1)
        self.run_cli('new', '../escape', expected=1)


if __name__ == '__main__':
    unittest.main()

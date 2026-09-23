"""Behavior tests in disposable projects; never mutate the real active task."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLAN = ('## 요구사항 구체화\n명세 상태: 확정\n원문 요청: 추가 기능 만들어줘.\n'
        '요청 해석: 추가 기능을 구현한다.\n'
        '조사한 사실: 임시 앱 파일을 확인했다.\n질문하지 않은 이유: 테스트 명세에 선택 사항이 없다.\n'
        '| 질문 ID | 결정 사항과 영향 | 사용자 답변·근거 | 상태 |\n| --- | --- | --- | --- |\n'
        '## 목표\n추가 기능\n## 범위\n앱\n'
        '## 요구사항\n| ID | 조건·입력 | 기대 동작·실패 조건 |\n| --- | --- | --- |\n'
        '| REQ-01 | 정상 요청 | 기능을 제공한다 |\n'
        '## 완료 기준\n검사 통과\n'
        '## 적용 영역과 상세 기준\n| 기준 ID | 시나리오 |\n| --- | --- |\n| E2E-01 | 정상 흐름 |\n'
        '## 일반 테스트 방법\n| 완료 기준 | 명령 |\n| --- | --- |\n| C1 | unittest |\n')


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
        self.assertTrue(str(self.script).startswith(str(self.root)))
        self.run_cli('new', '001-test')
        self.task = self.root / '.claude/tasks/001-test'
        (self.task / 'task.md').write_text(PLAN)
        self.call_verifier()
        self.approve_plan()
        self.set_checks([sys.executable, '-c', 'print("verified")'])

    def run_cli(self, *args, expected=0, payload=None):
        result = subprocess.run([sys.executable, str(self.script), *args], cwd=self.root,
                                input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def hook(self, event, tool_input=None, expected=0, **extra):
        payload = dict(extra)
        if tool_input is not None:
            payload['tool_input'] = tool_input
        return self.run_cli('hook-' + event, expected=expected, payload=payload).stdout

    def decision(self, output):
        return json.loads(output)['hookSpecificOutput']['permissionDecision'] if output else 'allow'

    def call_verifier(self, subagent='verifier'):
        return self.hook('agent', {'subagent_type': subagent, 'prompt': 'x'})

    def approve_plan(self):
        plan_hash = self.state()['verifier_calls'][-1].get('plan_snapshot')
        (self.task / 'plan-review.md').write_text(
            '대상 계획 지문: {}\n최종 판정: 통과 권고\n'.format(plan_hash))

    def set_checks(self, argv, timeout=5):
        (self.root / '.claude/checks.json').write_text(json.dumps({
            'checks': [{'argv': argv, 'timeout_seconds': timeout}]}))

    def set_harness_checks(self, checks):
        shutil.copytree(ROOT / 'harness', self.root / 'harness',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'profiles', self.root / 'profiles')
        (self.root / 'harness.json').write_text(json.dumps({
            'schema_version': 1,
            'project': {
                'name': 'workflow-test',
                'type': 'tooling',
                'source_paths': ['app'],
            },
            'profile_defaults': {},
            'profiles': [],
            'checks': checks,
        }))

    def state(self):
        return json.loads(self.run_cli('status').stdout)

    def snapshot(self):
        return json.loads((self.task / 'evidence/checks.json').read_text())['snapshot']

    def write_report(self, verdict='통과 권고'):
        (self.task / 'review.md').write_text(
            '# 검토 결과\n## 독립 검증 결과\nsnapshot {}: {}\n'
            '## 요구사항별 검증\n| 요구사항 ID | 구현 위치 | 테스트·실행 증거 | 판정 |\n'
            '| --- | --- | --- | --- |\n| REQ-01 | app/main.txt | check-1.log | 통과 |\n'
            '## 판정과 이유\n{}\n'.format(
                self.snapshot(), verdict, verdict))

    def pass_review(self):
        self.run_cli('review-begin')
        self.call_verifier()
        self.write_report()
        self.run_cli('review', 'pass')

    def fail_review(self, expected=0):
        self.run_cli('review-begin')
        self.call_verifier()
        self.write_report('수정 필요')
        self.run_cli('review', 'fail', expected=expected)

    def test_happy_path(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')
        self.assertEqual(self.state()['phase'], 'done')
        self.assertEqual(self.hook('stop'), '')

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

    def test_changed_archived_review_blocks_complete(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        (self.task / 'evidence/review-1.json').write_text('{}')
        self.run_cli('complete', expected=1)

    def test_missing_or_changed_log_blocks_complete(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        log = self.task / 'evidence' / receipt['results'][0]['log']
        log.write_text('replaced')
        self.run_cli('complete', expected=1)
        log.unlink()
        self.run_cli('complete', expected=1)

    def test_review_failure_requires_new_checks(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.fail_review()
        self.run_cli('review', 'pass', expected=1)
        self.run_cli('complete', expected=1)
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')

    def test_review_budget_stops_and_explicit_extension_preserves_history(self):
        self.run_cli('start')
        for attempt in range(1, 4):
            self.run_cli('verify')
            self.fail_review(expected=1 if attempt == 3 else 0)
        state = self.state()
        self.assertEqual(state['phase'], 'waiting')
        self.assertEqual(state['review_attempts'], 3)
        self.run_cli('start', expected=1)
        self.run_cli('verify', expected=1)
        self.run_cli('complete', expected=1)
        self.assertTrue((self.task / 'evidence/review-1.md').exists())
        self.assertTrue((self.task / 'evidence/review-1.json').exists())
        self.assertTrue((self.task / 'evidence/review-3.json').exists())
        self.run_cli('review-extend', expected=1)
        self.run_cli('review-extend', '사용자가 추가 리뷰 1회 허용')
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')
        self.assertEqual(self.state()['review_attempts'], 4)

    def test_third_review_can_pass_and_begin_is_required(self):
        self.run_cli('start')
        for _ in range(2):
            self.run_cli('verify')
            self.fail_review()
        self.run_cli('verify')
        self.write_report()
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

    def test_harness_config_check_runs_in_configured_cwd(self):
        worker = self.root / 'worker'
        worker.mkdir()
        self.set_harness_checks([{
            'id': 'cwd-check',
            'kind': 'unit',
            'cwd': 'worker',
            'argv': [sys.executable, '-c', 'import os; print(os.getcwd())'],
            'required': True,
            'timeout_seconds': 5,
        }])
        self.run_cli('start')
        self.run_cli('verify')

        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        result = receipt['results'][0]
        self.assertEqual(receipt['checks_source'], 'harness.json')
        self.assertEqual(result['cwd'], 'worker')
        self.assertIn(str(worker), (self.task / 'evidence' / result['log']).read_text())

    def test_harness_config_condition_mismatch_is_skipped_with_evidence(self):
        self.set_harness_checks([{
            'id': 'conditional-check',
            'kind': 'integration',
            'cwd': '.',
            'argv': [sys.executable, '-c', 'raise SystemExit(9)'],
            'required': True,
            'timeout_seconds': 5,
            'when': {'files_any': ['feature-not-present/*.py']},
        }])
        self.run_cli('start')
        self.run_cli('verify')

        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['results'], [])
        self.assertEqual(receipt['skipped_checks'][0]['status'], 'skipped')
        self.assertIn('files_any', receipt['skipped_checks'][0]['reason'])
        # A run consisting only of inapplicable checks remains valid review evidence.
        self.run_cli('review-begin')

    def test_missing_optional_command_is_skipped_and_required_check_continues(self):
        self.set_harness_checks([
            {
                'id': 'optional-tool',
                'kind': 'static',
                'cwd': '.',
                'argv': ['command-that-does-not-exist-for-harness-test'],
                'required': False,
                'timeout_seconds': 5,
            },
            {
                'id': 'required-check',
                'kind': 'unit',
                'cwd': '.',
                'argv': [sys.executable, '-c', 'print("required passed")'],
                'required': True,
                'timeout_seconds': 5,
            },
        ])
        self.run_cli('start')
        self.run_cli('verify')

        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertTrue(receipt['passed'])
        self.assertEqual([result['id'] for result in receipt['results']], ['required-check'])
        self.assertEqual(receipt['skipped_checks'][0]['id'], 'optional-tool')
        self.assertIn('찾을 수 없음', receipt['skipped_checks'][0]['reason'])

    def test_optional_check_failure_is_recorded_without_failing_required_checks(self):
        self.set_harness_checks([
            {
                'id': 'optional-failure',
                'kind': 'static',
                'cwd': '.',
                'argv': [sys.executable, '-c', 'raise SystemExit(6)'],
                'required': False,
                'timeout_seconds': 5,
            },
            {
                'id': 'required-success',
                'kind': 'unit',
                'cwd': '.',
                'argv': [sys.executable, '-c', 'print("ok")'],
                'required': True,
                'timeout_seconds': 5,
            },
        ])
        self.run_cli('start')
        self.run_cli('verify')

        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['results'][0]['exit_code'], 6)
        self.assertFalse(receipt['results'][0]['required'])
        self.assertEqual(receipt['results'][1]['status'], 'passed')
        self.run_cli('review-begin')

    def test_legacy_checks_json_remains_the_fallback(self):
        self.assertFalse((self.root / 'harness.json').exists())
        self.run_cli('start')
        self.run_cli('verify')

        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertEqual(receipt['checks_source'], '.claude/checks.json')
        self.assertEqual(receipt['results'][0]['id'], 'legacy-check-1')
        self.assertTrue(receipt['results'][0]['required'])

    def test_questions_waiting_and_loop_escape(self):
        self.assertEqual(self.hook('stop'), '')
        self.run_cli('start')
        self.assertEqual(json.loads(self.hook('stop'))['decision'], 'block')
        self.assertEqual(self.hook('stop', stop_hook_active=True), '')
        self.run_cli('wait', '사용자 저장 방식 선택 대기')
        self.assertEqual(self.hook('stop'), '')
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
        (self.task / 'task.md').write_text(PLAN.replace('## 목표\n추가 기능\n', '## 목표\n\n'))
        result = self.run_cli('start', expected=1)
        self.assertIn('목표', result.stderr)
        (self.task / 'task.md').write_text(PLAN.replace('| E2E-01 | 정상 흐름 |\n', ''))
        self.assertIn('표', self.run_cli('start', expected=1).stderr)
        (self.task / 'task.md').write_text(PLAN)
        (self.task / 'plan-review.md').unlink()
        self.assertIn('plan-review', self.run_cli('start', expected=1).stderr)
        self.run_cli('new', '../escape', expected=1)

    # --- 001-harness-hardening ---

    def test_forged_evidence_without_verifier_is_rejected(self):
        self.run_cli('start')
        evidence = self.task / 'evidence'
        evidence.mkdir()
        archive = evidence / 'verify-001'
        archive.mkdir()
        (archive / 'check-1.log').write_text('fake ok\n')
        # Forge with the program's own functions: correct snapshot, correct log hash, phase=reviewing.
        env = {'__name__': 'forge', '__file__': str(self.script)}
        exec(compile(self.script.read_text(), str(self.script), 'exec'), env)
        state = env['current']()
        fake_receipt = {
            'passed': True, 'snapshot': env['snapshot'](state), 'unchanged_during_checks': True,
            'archive': 'verify-001/checks.json',
            'results': [{'argv': ['x'], 'exit_code': 0, 'log': 'verify-001/check-1.log',
                         'log_hash': hashlib.sha256((archive / 'check-1.log').read_bytes()).hexdigest()}]}
        env['write_json'](archive / 'checks.json', fake_receipt)
        shutil.copyfile(archive / 'checks.json', evidence / 'checks.json')
        state['phase'] = 'reviewing'
        env['save'](state)
        self.run_cli('review-begin')
        self.write_report()
        self.assertIn('verifier', self.run_cli('review', 'pass', expected=1).stderr)
        self.run_cli('complete', expected=1)

    def test_bash_gate(self):
        denied = ['git push origin main', 'rm -rf build', 'rm -fr build', 'rm -f -r x', 'git restore x',
                  'cat .claude/tasks/t/evidence/check-1.log', 'python3  ".claude/hooks/workflow.py" hook-stop',
                  'git commit --no-verify -m x', 'sed -i s/a/b/ .claude/tasks/active.json']
        allowed = ['git commit -m x', 'git status', 'python3 -m unittest discover -s tests',
                   'python3 .claude/hooks/workflow.py status', 'rm -f x.tmp', 'git checkout -b feat']
        for command in denied:
            self.assertEqual(self.decision(self.hook('bash', {'command': command})), 'deny', command)
        for command in allowed:
            self.assertEqual(self.decision(self.hook('bash', {'command': command})), 'allow', command)
        (self.root / '.claude/tasks/active.json').write_text('{"id": "001-test", "phase": "bogus"}')
        self.assertEqual(self.decision(self.hook('bash', {'command': 'git status'})), 'allow')

    def test_edit_gate_deny_by_default(self):
        def edit(path, key='file_path'):
            return self.decision(self.hook('edit', {key: path}, cwd=str(self.root)))
        for path in ['src/x.py', '.claude/hooks/workflow.py', '.claude/checks.json', '.claude/settings.json']:
            self.assertEqual(edit(path), 'deny', path)
        self.assertEqual(edit('app/a.ipynb', 'notebook_path'), 'deny')
        self.assertEqual(edit('docs/../src/x.py'), 'deny')
        for path in ['docs/a.md', 'README.md', '.claude/tasks/001-test/task.md',
                     '.claude/tasks/001-test/progress.md', '/tmp/x']:
            self.assertEqual(edit(path), 'allow', path)
        self.run_cli('start')
        for path in ['src/x.py', '.claude/hooks/workflow.py', 'app/a.ipynb']:
            self.assertEqual(edit(path), 'allow', path)
        for path in ['.claude/tasks/001-test/evidence/checks.json', '.claude/tasks/active.json',
                     '.claude/tasks/001-test/state.json', '.claude/tasks/index.md']:
            self.assertEqual(edit(path), 'deny', path)
        (self.root / '.claude/tasks/active.json').write_text('not json')
        self.assertEqual(edit('src/x.py'), 'deny')
        self.assertIn('손상', self.hook('edit', {'file_path': 'src/x.py'}, cwd=str(self.root)))
        self.assertEqual(edit('docs/a.md'), 'allow')
        (self.root / '.claude/tasks/active.json').unlink()
        self.assertEqual(edit('src/x.py'), 'deny')
        self.assertEqual(edit('.claude/tasks/001-test/task.md'), 'deny')
        self.assertEqual(edit('docs/a.md'), 'allow')
        self.assertEqual(self.call_verifier(), '')

    def test_stop_after_done_and_corrupt_state(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        self.run_cli('complete')
        (self.root / 'README.md').write_text('changed after done')
        self.assertEqual(self.hook('stop'), '')
        (self.root / '.claude/tasks/active.json').write_text('{"id": "001-test", "phase": "bogus"}')
        self.assertEqual(json.loads(self.hook('stop'))['decision'], 'block')
        self.assertEqual(self.hook('stop', stop_hook_active=True), '')
        self.assertEqual(self.call_verifier(), '')
        self.assertIn('손상', json.loads(self.hook('session'))['hookSpecificOutput']['additionalContext'])

    def test_verifier_call_required(self):
        (self.root / '.claude/tasks/active.json').write_text(json.dumps({'id': '001-test', 'phase': 'planning'}))
        self.assertIn('verifier', self.run_cli('start', expected=1).stderr)
        self.assertEqual(self.call_verifier('Explore'), '')
        self.run_cli('start', expected=1)
        # A wait/block detour before the first start must not skip the plan gate.
        self.run_cli('wait', '사용자 선택 대기')
        self.assertIn('verifier', self.run_cli('start', expected=1).stderr)
        self.call_verifier()
        self.assertEqual(self.state()['verifier_calls'][-1]['stage'], 'plan')
        self.run_cli('start')
        self.run_cli('verify')
        self.assertEqual(self.decision(self.call_verifier()), 'deny')
        self.run_cli('review-begin')
        self.write_report('수정 필요')
        self.run_cli('review', 'fail', expected=1)
        self.call_verifier()
        self.run_cli('review', 'fail')
        self.run_cli('verify')
        self.run_cli('review-begin')
        self.write_report()
        self.assertIn('verifier', self.run_cli('review', 'pass', expected=1).stderr)
        self.call_verifier()
        self.run_cli('review', 'pass')
        calls = self.state()['verifier_calls']
        self.assertEqual([c['phase'] for c in calls], ['waiting', 'reviewing', 'reviewing'])
        self.assertEqual([c['stage'] for c in calls], ['plan', 'result', 'result'])
        self.assertEqual([c['attempt'] for c in calls], [0, 1, 2])

    def test_review_report_format(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.run_cli('review-begin')
        self.call_verifier()
        (self.task / 'review.md').write_text('통과')
        self.assertIn('독립 검증 결과', self.run_cli('review', 'pass', expected=1).stderr)
        (self.task / 'review.md').write_text('## 독립 검증 결과\n확인함\n')
        self.run_cli('review', 'pass', expected=1)
        self.write_report()
        self.run_cli('review', 'pass')

    def test_evidence_metadata(self):
        self.run_cli('start')
        self.run_cli('verify')
        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        for key in ('started_at', 'finished_at', 'python', 'git'):
            self.assertIn(key, receipt)
        # The temporary root is not a git repository: both fields must be null, not an error.
        self.assertIsNone(receipt['git']['head'])
        self.assertIsNone(receipt['git']['dirty'])
        self.assertIn('duration_seconds', receipt['results'][0])
        self.assertTrue((self.task / 'evidence/changes.txt').exists())

    def test_plan_rejection_and_changed_plan_cannot_start(self):
        plan_hash = self.state()['verifier_calls'][-1].get('plan_snapshot')
        (self.task / 'plan-review.md').write_text(
            '대상 계획 지문: {}\n최종 판정: 수정 필요\n'.format(plan_hash))
        self.run_cli('start', expected=1)
        self.approve_plan()
        with (self.task / 'plan-review.md').open('a') as report:
            report.write('최종 판정: 수정 필요\n')
        self.run_cli('start', expected=1)
        self.approve_plan()
        (self.task / 'task.md').write_text(PLAN + '\n새로운 필수 조건\n')
        self.run_cli('start', expected=1)

    def test_clarification_blocks_implementation_until_resolved(self):
        template = self.root / '.claude/skills/plan-task/templates/task.md'
        (self.task / 'task.md').write_text(template.read_text())
        self.assertIn('명세 상태', self.run_cli('start', expected=1).stderr)
        (self.task / 'task.md').write_text(PLAN.replace('명세 상태: 확정', '명세 상태: 초안'))
        self.assertIn('명세 상태', self.run_cli('start', expected=1).stderr)

        open_plan = PLAN.replace('질문하지 않은 이유: 테스트 명세에 선택 사항이 없다.',
                                 '질문하지 않은 이유:')
        open_plan = open_plan.replace('| --- | --- | --- | --- |\n## 목표',
                                      '| --- | --- | --- | --- |\n'
                                      '| Q-01 | 중복 요청의 처리 결과 | | 미결정 |\n## 목표')
        (self.task / 'task.md').write_text(open_plan)
        self.assertIn('Q-01', self.run_cli('start', expected=1).stderr)
        resolved = open_plan.replace('| Q-01 | 중복 요청의 처리 결과 | | 미결정 |',
                                     '| Q-01 | 중복 요청의 처리 결과 | 사용자가 한 번만 처리하도록 결정 | 해결 |')
        (self.task / 'task.md').write_text(resolved)
        self.call_verifier()
        self.approve_plan()
        self.run_cli('start')

    def test_no_questions_require_reason_and_requirement_ids(self):
        (self.task / 'task.md').write_text(PLAN.replace('원문 요청: 추가 기능 만들어줘.', '원문 요청:'))
        self.assertIn('원문 요청', self.run_cli('start', expected=1).stderr)
        (self.task / 'task.md').write_text(
            PLAN.replace('질문하지 않은 이유: 테스트 명세에 선택 사항이 없다.', '질문하지 않은 이유:'))
        self.assertIn('질문하지 않은 이유', self.run_cli('start', expected=1).stderr)
        (self.task / 'task.md').write_text(PLAN.replace('| REQ-01 | 정상 요청 | 기능을 제공한다 |',
                                                       '| TODO | 정상 요청 | 기능을 제공한다 |'))
        self.assertIn('REQ-번호', self.run_cli('start', expected=1).stderr)

    def test_investigated_facts_can_be_a_list_below_the_label(self):
        (self.task / 'task.md').write_text(
            PLAN.replace('조사한 사실: 임시 앱 파일을 확인했다.',
                         '조사한 사실:\n- 임시 앱 파일을 확인했다.\n- 테스트 기준을 확인했다.'))
        self.call_verifier()
        self.approve_plan()
        self.run_cli('start')

    def test_changed_spec_requires_new_plan_review_before_verification(self):
        self.run_cli('start')
        (self.task / 'task.md').write_text(PLAN.replace('기능을 제공한다', '변경된 기능을 제공한다'))
        self.assertEqual(self.decision(self.hook('edit', {'file_path': 'app/main.txt'}, cwd=str(self.root))),
                         'deny')
        self.assertEqual(self.decision(self.hook('edit', {
            'file_path': '.claude/tasks/001-test/task.md'}, cwd=str(self.root))), 'allow')
        self.assertIn('명세', self.run_cli('verify', expected=1).stderr)
        self.assertIn('계획 검증', self.run_cli('start', expected=1).stderr)
        self.call_verifier()
        self.assertEqual(self.state()['verifier_calls'][-1]['stage'], 'plan')
        self.approve_plan()
        self.run_cli('start')
        self.assertEqual(self.decision(self.hook('edit', {'file_path': 'app/main.txt'}, cwd=str(self.root))),
                         'allow')
        self.run_cli('verify')

    def test_review_pass_requires_every_requirement_evidence(self):
        self.run_cli('start')
        self.run_cli('verify')
        self.run_cli('review-begin')
        self.call_verifier()
        self.write_report()
        report = self.task / 'review.md'
        report.write_text(report.read_text().replace(
            '| REQ-01 | app/main.txt | check-1.log | 통과 |\n', ''))
        self.assertIn('누락된 요구사항', self.run_cli('review', 'pass', expected=1).stderr)
        self.write_report()
        self.run_cli('review', 'pass')

    def test_changed_testing_policy_requires_new_plan_review(self):
        policy = self.root / 'docs/testing-policy.md'
        policy.parent.mkdir()
        policy.write_text('새로운 정책')
        self.run_cli('start', expected=1)

    def test_docs_change_after_review_blocks_completion(self):
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'policy.md').write_text('before')
        self.run_cli('start')
        self.run_cli('verify')
        self.pass_review()
        (docs / 'policy.md').write_text('after')
        self.run_cli('complete', expected=1)

    def test_verification_history_survives_retry_and_skips_later_checks(self):
        marker = self.root / 'performance-ran'
        checks = [
            {'argv': [sys.executable, '-c', 'print("FIRST-FAILURE"); raise SystemExit(1)'], 'timeout_seconds': 5},
            {'argv': [sys.executable, '-c', 'from pathlib import Path; Path("performance-ran").write_text("yes")'],
             'timeout_seconds': 5},
        ]
        (self.root / '.claude/checks.json').write_text(json.dumps({'checks': checks}))
        self.run_cli('start')
        self.run_cli('verify', expected=1)
        first = self.task / 'evidence/verify-001/check-1.log'
        self.assertIn('FIRST-FAILURE', first.read_text())
        self.assertFalse(marker.exists())
        self.set_checks([sys.executable, '-c', 'print("SECOND-SUCCESS")'])
        self.run_cli('verify')
        self.assertIn('FIRST-FAILURE', first.read_text())
        self.assertTrue((self.task / 'evidence/verify-001/checks.json').exists())
        self.assertTrue((self.task / 'evidence/verify-002/checks.json').exists())

    def test_snapshot_separates_file_boundaries(self):
        (self.root / 'a').write_bytes(b'')
        (self.root / 'b').write_bytes(b'b\x00Z')
        self.run_cli('start')
        self.run_cli('verify')
        (self.root / 'a').write_bytes(b'b\x00')
        (self.root / 'b').write_bytes(b'Z')
        self.assertIn('바뀌었습니다', self.run_cli('review-begin', expected=1).stderr)

    def test_explicit_ignored_input_is_snapshotted(self):
        environment = self.root / '.env'
        (self.root / '.gitignore').write_text('.env\n')
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        environment.write_text('CONFIG=before')
        self.run_cli('start')
        self.run_cli('verify')
        environment.write_text('CONFIG=after')
        self.run_cli('review-begin', expected=1)

    def test_committed_changes_are_listed_from_task_base(self):
        def git(*args):
            result = subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()
        git('init', '-q')
        git('config', 'user.name', 'Harness Test')
        git('config', 'user.email', 'harness@example.test')
        git('add', '.claude', 'app/main.txt')
        git('commit', '-qm', 'baseline')
        state = self.state()
        state['phase'] = 'done'
        (self.root / '.claude/tasks/active.json').write_text(json.dumps(state))
        (self.task / 'state.json').write_text(json.dumps(state))
        self.run_cli('new', '002-committed')
        self.task = self.root / '.claude/tasks/002-committed'
        (self.task / 'task.md').write_text(PLAN)
        self.call_verifier()
        self.approve_plan()
        base_head = git('rev-parse', 'HEAD')
        self.assertEqual(self.state()['base_head'], base_head)
        (self.root / 'app/main.txt').write_text('committed change')
        git('add', 'app/main.txt')
        git('commit', '-qm', 'change')
        self.run_cli('start')
        self.run_cli('verify')
        changes = (self.task / 'evidence/changes.txt').read_text()
        self.assertIn('app/main.txt', changes)
        self.assertIn(base_head, changes)
        receipt = json.loads((self.task / 'evidence/checks.json').read_text())
        self.assertFalse(receipt['git']['dirty'])


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Small workflow gate, not an agent runner. Python standard library only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / '.claude/tasks'
STATE = TASKS / 'active.json'
EXCLUDED = {'.git', 'node_modules', '__pycache__', '.venv', 'venv',
            '.next', 'dist', 'build', 'coverage', '.pytest_cache'}
# Program-owned records: never editable by tools, in any phase.
PROTECTED = re.compile(r'^\.claude/tasks/(active\.json|index\.md|[^/]+/(state\.json|evidence(/.*)?))$')
# Outside the implementing phase only planning documents may change.
PLANNING_DOCS = ('task', 'progress', 'plan-review', 'review')
BASH_DENY = re.compile(
    r'active\.json|state\.json|/evidence\b|workflow\.py["\']?\s+["\']?hook-'
    r'|git\s+push|reset\s+--hard|git\s+clean|git\s+restore\b|git\s+checkout\s+--|--no-verify'
    r'|\brm\b.*(\s-[a-zA-Z]*[rR]|--recursive)')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                     dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        name = handle.name
    os.replace(name, path)


def current():
    if not STATE.exists():
        return None
    state = read_json(STATE)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', state['id']):
        raise ValueError('잘못된 작업 ID')
    if state['phase'] not in {'planning', 'implementing', 'verifying',
                               'reviewing', 'ready', 'done', 'waiting', 'blocked'}:
        raise ValueError('잘못된 작업 상태')
    return state


def task_dir(state):
    return TASKS / state['id']


def save(state):
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, state)
    write_json(task_dir(state) / 'state.json', state)
    rows = ['# 작업 목록', '', '상태는 workflow.py 명령으로 갱신합니다.', '']
    for path in sorted(TASKS.glob('*/state.json')):
        item = read_json(path)
        rows.append('- [{}]({}/task.md): {}'.format(item['id'], item['id'], item['phase']))
    (TASKS / 'index.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')


def now():
    return datetime.now(timezone.utc).isoformat()


def git(*args):
    try:
        result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def table_rows(body, heading):
    match = re.search(r'^## ' + heading + r'\n(.*?)(?=^## |\Z)', body, re.M | re.S)
    if not match:
        return []
    return [line for line in match.group(1).splitlines()
            if line.startswith('|') and not re.match(r'\|\s*-', line)]


def edit_decision(state, rel, corrupt):
    """Return a deny reason, or None when the edit is allowed."""
    if PROTECTED.search(rel):
        return '증거·상태·작업 목록은 workflow.py만 기록합니다.'
    if state and state['phase'] == 'implementing':
        return None
    if rel.startswith('docs/') or ('/' not in rel and rel.endswith('.md')):
        return None
    if state and re.fullmatch(r'\.claude/tasks/' + re.escape(state['id']) + r'/(' + '|'.join(PLANNING_DOCS) + r')\.md', rel):
        return None
    reason = '구현 단계에서만 수정할 수 있습니다. 작업을 계획하고 workflow.py start를 실행하세요.'
    if corrupt:
        reason = '상태 파일 손상({}). 사용자에게 보고하세요. '.format(corrupt) + reason
    return reason


def snapshot(state):
    """Bind evidence to source/config and task requirements, excluding run records."""
    digest = hashlib.sha256()
    for base, dirs, files in os.walk(ROOT, followlinks=False):
        relbase = Path(base).relative_to(ROOT)
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED
                         and (relbase / d).as_posix() not in {'.claude/tasks', 'docs', 'experiments'})
        for directory in dirs:
            if (Path(base) / directory).is_symlink():
                raise ValueError('검사 대상 심볼릭 링크 디렉토리는 지원하지 않습니다: ' + str(relbase / directory))
        for name in sorted(files):
            path = Path(base) / name
            if name == '.DS_Store' or name.endswith(('.pyc', '.log')):
                continue
            # Environment files affect verification, but their contents are never logged.
            rel = path.relative_to(ROOT).as_posix()
            digest.update(rel.encode() + b'\0')
            if path.is_symlink():
                raise ValueError('검사 대상 심볼릭 링크는 지원하지 않습니다: ' + rel)
            digest.update(path.read_bytes())
    digest.update((task_dir(state) / 'task.md').read_bytes())
    return digest.hexdigest()


def require_phase(state, phases):
    if state['phase'] not in phases:
        raise ValueError('현재 단계 {}에서는 실행할 수 없습니다: {}'.format(state['phase'], ', '.join(phases)))


def checked(state):
    receipt = read_json(task_dir(state) / 'evidence/checks.json')
    if not receipt.get('passed') or not receipt.get('results'):
        raise ValueError('필수 검사가 통과하지 않았습니다.')
    if receipt['snapshot'] != snapshot(state):
        raise ValueError('검사 이후 코드·설정·요구사항이 바뀌었습니다. start 후 verify를 다시 실행하세요.')
    for result in receipt['results']:
        name = result['log']
        if Path(name).name != name or result['exit_code'] != 0:
            raise ValueError('검사 증거 형식이 잘못되었습니다.')
        actual = hashlib.sha256((task_dir(state) / 'evidence' / name).read_bytes()).hexdigest()
        if result['log_hash'] != actual:
            raise ValueError('검사 로그가 변경되었습니다. 검사를 다시 실행하세요.')
    return receipt


def complete_evidence(state):
    receipt = checked(state)
    review = read_json(task_dir(state) / 'evidence/review.json')
    report = (task_dir(state) / 'review.md').read_bytes()
    if review.get('verdict') != 'pass' or review.get('snapshot') != receipt['snapshot']:
        raise ValueError('현재 코드의 검토 통과 기록이 없습니다.')
    if review['report_hash'] != hashlib.sha256(report).hexdigest():
        raise ValueError('검토 등록 이후 보고서가 변경되었습니다. 검토를 다시 등록하세요.')


def review_budget(state):
    if state.get('review_attempts', 0) >= state.get('review_limit', 3):
        state['phase'] = 'waiting'
        state['reason'] = '리뷰 한도 도달. 남은 문제·영향·수정 내역·미해결 이유·선택지를 보고하고 사용자 결정을 기다리세요.'
        save(state)
        raise ValueError(state['reason'])


def verify(state):
    require_phase(state, {'implementing', 'reviewing', 'ready'})
    review_budget(state)
    state.pop('review_pending', None)
    checks = read_json(ROOT / '.claude/checks.json')['checks']
    if not isinstance(checks, list) or not checks:
        raise ValueError('.claude/checks.json에 앱의 필수 검사 명령을 먼저 등록하세요.')
    for check in checks:
        if (not isinstance(check.get('argv'), list) or not check['argv']
                or not all(isinstance(x, str) and x for x in check['argv'])
                or not isinstance(check.get('timeout_seconds'), int)
                or not 1 <= check['timeout_seconds'] <= 3600):
            raise ValueError('검사에는 argv 문자열 배열과 1~3600의 timeout_seconds가 필요합니다.')
    before = snapshot(state)
    state['phase'] = 'verifying'
    save(state)
    evidence = task_dir(state) / 'evidence'
    evidence.mkdir(exist_ok=True)
    started = now()
    head, porcelain = git('rev-parse', 'HEAD'), git('status', '--porcelain')
    (evidence / 'changes.txt').write_text(
        'git 저장소가 아니거나 git을 실행할 수 없습니다.\n' if porcelain is None
        else '# git status --porcelain\n' + porcelain + '\n# git diff --stat\n' + (git('diff', '--stat') or ''),
        encoding='utf-8')
    results = []
    for i, check in enumerate(checks):
        log = evidence / ('check-{}.log'.format(i + 1))
        check_started = datetime.now(timezone.utc)
        with log.open('w', encoding='utf-8') as output:
            try:
                # No shell interpolation; commands are explicitly configured argument arrays.
                process = subprocess.Popen(check['argv'], cwd=ROOT, stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    code = process.wait(timeout=check['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    import signal
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    output.write('\n검사 시간 초과\n')
                    code = 124
            except OSError as error:
                output.write(str(error) + '\n')
                code = 127
        results.append({'argv': check['argv'], 'exit_code': code, 'log': log.name,
                        'log_hash': hashlib.sha256(log.read_bytes()).hexdigest(),
                        'duration_seconds': round((datetime.now(timezone.utc) - check_started).total_seconds(), 3)})
    after = snapshot(state)
    passed = all(x['exit_code'] == 0 for x in results) and before == after
    write_json(evidence / 'checks.json', {
        'passed': passed, 'snapshot': after, 'unchanged_during_checks': before == after,
        'started_at': started, 'finished_at': now(), 'python': sys.version.split()[0],
        # dirty is informational: verify itself writes state before this runs.
        'git': {'head': head.strip() if head else None, 'dirty': None if porcelain is None else bool(porcelain.strip())},
        'results': results})
    state['phase'] = 'reviewing' if passed else 'implementing'
    save(state)
    if not passed:
        raise ValueError('검사 실패 또는 검사 도중 파일 변경. evidence의 검사 로그를 확인하세요.')
    print('검사 통과. 요구사항·변경 코드·검사 결과를 검토하고 review.md를 작성하세요.')


def hook(event):
    payload = json.load(sys.stdin)
    tool_input = payload.get('tool_input') or {}
    if event == 'bash':
        # Stateless on purpose: must keep working when the state file is corrupt.
        if BASH_DENY.search(tool_input.get('command', '')):
            deny('PreToolUse', '증거·상태 파일 접근, hook 직접 호출, 파괴적 git·삭제 명령은 허용하지 않습니다. 로그는 Read 도구로 읽으세요.')
        return
    corrupt = None
    try:
        state = current()
    except (ValueError, KeyError, TypeError) as error:
        state, corrupt = None, str(error)
    if event == 'session':
        message = '작업 안내: .claude/CLAUDE.md. 개발 요청은 .claude/tasks/index.md부터 확인하세요.'
        if corrupt:
            message += ' 경고: 작업 상태 파일 손상({}). 사용자에게 보고하세요.'.format(corrupt)
        elif state:
            message += ' 현재 작업: {} / {}. task.md, progress.md와 실제 코드를 대조하세요.'.format(state['id'], state['phase'])
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                                'additionalContext': message}}, ensure_ascii=False))
    elif event == 'edit':
        raw = tool_input.get('file_path') or tool_input.get('notebook_path') or ''
        if not raw:
            return
        path = Path(raw)
        if not path.is_absolute():
            path = Path(payload.get('cwd', str(ROOT))) / path
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return
        reason = edit_decision(state, rel, corrupt)
        if reason:
            deny('PreToolUse', reason)
    elif event == 'agent':
        if tool_input.get('subagent_type') != 'verifier' or not state:
            return
        if state['phase'] == 'reviewing' and not state.get('review_pending'):
            deny('PreToolUse', '결과 리뷰는 review-begin을 먼저 실행한 뒤 verifier를 호출하세요.')
            return
        # Records the attempt only; the program cannot judge the verifier's answer.
        state.setdefault('verifier_calls', []).append({
            'at': now(), 'phase': state['phase'], 'stage': 'result' if state.get('started') else 'plan',
            'attempt': state.get('review_attempts', 0), 'snapshot': state.get('review_pending')})
        save(state)
    elif event == 'stop':
        if corrupt:
            if not payload.get('stop_hook_active'):
                block('작업 상태 파일 손상: {}. 사용자에게 보고하세요.'.format(corrupt))
            return
        # done was fully validated by complete; later edits belong to the next task.
        if not state or state['phase'] in {'planning', 'waiting', 'blocked', 'done'}:
            return
        if payload.get('stop_hook_active'):
            print('완료 검사를 충족하지 못했습니다. 미완료 상태를 사용자에게 알리세요.', file=sys.stderr)
            return
        block('개발 작업이 아직 완료되지 않았습니다: {}. 검사를 마친 뒤 complete를 실행하세요. '
              '질문·선택 대기·중단이면 이유를 남겨 wait 또는 block을 실행하고 미완료임을 보고하세요.'.format(state['phase']))


def deny(event_name, reason):
    print(json.dumps({'hookSpecificOutput': {'hookEventName': event_name, 'permissionDecision': 'deny',
                      'permissionDecisionReason': reason}}, ensure_ascii=False))


def block(reason):
    print(json.dumps({'decision': 'block', 'reason': reason}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['new', 'status', 'start', 'verify', 'review',
                        'complete', 'wait', 'block', 'review-begin', 'review-extend',
                        'hook-session', 'hook-edit', 'hook-bash', 'hook-agent', 'hook-stop'])
    parser.add_argument('value', nargs='?')
    args = parser.parse_args()
    if args.command.startswith('hook-'):
        hook(args.command[5:])
        return
    state = current()
    if args.command == 'status':
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    if args.command == 'new':
        if state and state['phase'] != 'done':
            raise ValueError('현재 작업을 먼저 마치세요. 이 초기 버전은 작업 하나씩 진행합니다.')
        if not args.value or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', args.value):
            raise ValueError('작업 ID는 소문자·숫자·하이픈으로 지정하세요.')
        directory = TASKS / args.value
        directory.mkdir(parents=True, exist_ok=False)
        template = ROOT / '.claude/skills/plan-task/templates/task.md'
        (directory / 'task.md').write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
        (directory / 'progress.md').write_text('# 진행 기록\n\n작업 생성. 다음 행동: 요구사항과 완료 기준 작성.\n', encoding='utf-8')
        save({'id': args.value, 'phase': 'planning'})
        print(str(directory / 'task.md'))
        return
    if not state:
        raise ValueError('활성 작업이 없습니다. new 명령으로 작업을 만드세요.')
    directory = task_dir(state)
    if args.command == 'start':
        require_phase(state, {'planning', 'waiting', 'blocked', 'implementing', 'verifying', 'reviewing', 'ready'})
        review_budget(state)
        state.pop('review_pending', None)
        body = (directory / 'task.md').read_text(encoding='utf-8')
        for heading in ('목표', '범위', '완료 기준'):
            match = re.search(r'^## ' + heading + r'\n(.*?)(?=^## |\Z)', body, re.M | re.S)
            if not match or not match.group(1).strip():
                raise ValueError('task.md의 {} 항목을 작성하세요.'.format(heading))
        for heading in ('적용 영역과 상세 기준', '일반 테스트 방법'):
            if len(table_rows(body, heading)) < 2:
                raise ValueError('task.md의 {} 표에 데이터 행을 작성하세요.'.format(heading))
        # The plan gate applies to the first start only, whatever detour (wait/block) preceded it.
        if not state.get('started'):
            review = directory / 'plan-review.md'
            if not review.exists() or '판정' not in review.read_text(encoding='utf-8'):
                raise ValueError('plan-review.md에 verifier의 계획 검증 판정을 남기세요.')
            if not any(call.get('stage') == 'plan' for call in state.get('verifier_calls', [])):
                raise ValueError('계획 단계의 verifier 호출 기록이 없습니다.')
        state['started'] = True
        state['phase'] = 'implementing'
        state.pop('reason', None)
        save(state)
    elif args.command == 'verify':
        verify(state)
    elif args.command == 'review-begin':
        require_phase(state, {'reviewing'})
        if state.get('review_pending'):
            raise ValueError('진행 중인 리뷰를 먼저 등록하거나 block으로 기록하세요.')
        review_budget(state)
        receipt = checked(state)
        state['review_attempts'] = state.get('review_attempts', 0) + 1
        state['review_pending'] = receipt['snapshot']
        save(state)
        print('리뷰 {}/{} 시작'.format(state['review_attempts'], state.get('review_limit', 3)))
    elif args.command == 'review-extend':
        require_phase(state, {'waiting', 'blocked'})
        if not args.value or not args.value.strip():
            raise ValueError('사용자가 추가 리뷰 1회를 허용한 결정 내용을 기록하세요.')
        if state.get('review_attempts', 0) < state.get('review_limit', 3):
            raise ValueError('리뷰 한도가 아직 남아 있습니다.')
        # This records user authorization; the program cannot authenticate a conversation.
        state.setdefault('review_extensions', []).append({'reason': args.value, 'at': datetime.now(timezone.utc).isoformat()})
        state['review_limit'] = state.get('review_limit', 3) + 1
        state.pop('review_pending', None)
        save(state)
    elif args.command == 'review':
        require_phase(state, {'reviewing'})
        receipt = checked(state)
        if args.value not in {'pass', 'fail'}:
            raise ValueError('review pass 또는 review fail을 지정하세요.')
        if state.get('review_pending') != receipt['snapshot']:
            raise ValueError('최신 검사 후 review-begin으로 리뷰를 시작하세요.')
        if not any(call.get('attempt') == state['review_attempts'] and call.get('snapshot') == receipt['snapshot']
                   for call in state.get('verifier_calls', [])):
            raise ValueError('현재 회차의 verifier 호출 기록이 없습니다. review-begin 후 verifier를 호출하세요.')
        report = (directory / 'review.md').read_bytes()
        text = report.decode('utf-8')
        if not report.strip():
            raise ValueError('검토 근거를 review.md에 작성하세요.')
        if args.value == 'pass':
            section = re.search(r'^## 독립 검증 결과\n(.*?)(?=^## |\Z)', text, re.M | re.S)
            if not section or not section.group(1).strip() or receipt['snapshot'] not in text:
                raise ValueError('review.md에 "## 독립 검증 결과"와 대상 snapshot {}을 기록하세요.'.format(receipt['snapshot'][:12]))
        write_json(directory / 'evidence/review.json', {'verdict': args.value,
                   'snapshot': receipt['snapshot'], 'report_hash': hashlib.sha256(report).hexdigest()})
        attempt = state['review_attempts']
        (directory / 'evidence' / ('review-{}.md'.format(attempt))).write_bytes(report)
        state.pop('review_pending', None)
        state['phase'] = 'ready' if args.value == 'pass' else 'implementing'
        save(state)
        if args.value == 'fail':
            review_budget(state)
    elif args.command == 'complete':
        require_phase(state, {'ready'})
        complete_evidence(state)
        state['phase'] = 'done'
        save(state)
    elif args.command in {'wait', 'block'}:
        if not args.value or not args.value.strip():
            raise ValueError('대기·중단 이유가 필요합니다.')
        state['phase'] = 'waiting' if args.command == 'wait' else 'blocked'
        state['reason'] = args.value
        save(state)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('workflow: ' + str(error), file=sys.stderr)
        # Hook errors must be visible rather than silently interpreted as success.
        sys.exit(2 if any(x.startswith('hook-') for x in sys.argv[1:]) else 1)

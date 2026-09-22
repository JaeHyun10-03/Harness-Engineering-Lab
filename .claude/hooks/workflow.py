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
    results = []
    for i, check in enumerate(checks):
        log = evidence / ('check-{}.log'.format(i + 1))
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
                        'log_hash': hashlib.sha256(log.read_bytes()).hexdigest()})
    after = snapshot(state)
    passed = all(x['exit_code'] == 0 for x in results) and before == after
    write_json(evidence / 'checks.json', {'passed': passed, 'snapshot': after,
               'unchanged_during_checks': before == after, 'results': results})
    state['phase'] = 'reviewing' if passed else 'implementing'
    save(state)
    if not passed:
        raise ValueError('검사 실패 또는 검사 도중 파일 변경. evidence의 검사 로그를 확인하세요.')
    print('검사 통과. 요구사항·변경 코드·검사 결과를 검토하고 review.md를 작성하세요.')


def hook(event):
    payload = json.load(sys.stdin)
    state = current()
    if event == 'session':
        message = '작업 안내: .claude/CLAUDE.md. 개발 요청은 .claude/tasks/index.md부터 확인하세요.'
        if state:
            message += ' 현재 작업: {} / {}. task.md, progress.md와 실제 코드를 대조하세요.'.format(state['id'], state['phase'])
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'SessionStart',
                                                'additionalContext': message}}, ensure_ascii=False))
    elif event == 'edit':
        raw = payload.get('tool_input', {}).get('file_path', '')
        if not raw:
            return
        path = Path(raw)
        if not path.is_absolute():
            path = Path(payload.get('cwd', str(ROOT))) / path
        try:
            rel = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            return
        if rel.split('/')[0] in {'app', 'scripts', 'tests'} and (not state or state['phase'] != 'implementing'):
            print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                 'permissionDecision': 'deny', 'permissionDecisionReason':
                 '앱·실행 스크립트·테스트 수정 전 작업을 계획하고 workflow.py start를 실행하세요.'}}, ensure_ascii=False))
    elif event == 'stop':
        if not state or state['phase'] in {'planning', 'waiting', 'blocked'}:
            return
        try:
            if state['phase'] != 'done':
                raise ValueError('개발 작업이 아직 완료되지 않았습니다: ' + state['phase'])
            complete_evidence(state)
        except (ValueError, OSError, KeyError) as error:
            # One reminder per stop loop; never trap questions or unresolved environments.
            if payload.get('stop_hook_active'):
                print('완료 검사를 충족하지 못했습니다. 미완료 상태를 사용자에게 알리세요.', file=sys.stderr)
                return
            print(json.dumps({'decision': 'block', 'reason': str(error) +
                 ' 검사를 마친 뒤 complete를 실행하세요. 질문·선택 대기·중단이면 이유를 남겨 wait 또는 block을 실행하고 미완료임을 보고하세요.'}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['new', 'status', 'start', 'verify', 'review',
                        'complete', 'wait', 'block', 'review-begin', 'review-extend', 'hook-session', 'hook-edit', 'hook-stop'])
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
        report = (directory / 'review.md').read_bytes()
        if not report.strip():
            raise ValueError('검토 근거를 review.md에 작성하세요.')
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

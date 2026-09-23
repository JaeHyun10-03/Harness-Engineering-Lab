#!/usr/bin/env python3
"""Small workflow gate, not an agent runner. Python standard library only."""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / '.claude/tasks'
STATE = TASKS / 'active.json'
EXCLUDED = {'.git', 'node_modules', '__pycache__', '.venv', 'venv',
            '.next', '.gradle', '.idea', '.turbo', 'target', 'out',
            'dist', 'build', 'coverage', '.pytest_cache'}
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


def source_status():
    porcelain = git('status', '--porcelain', '--untracked-files=all')
    if porcelain is None:
        return None
    return ''.join(line + '\n' for line in porcelain.splitlines()
                   if not line[3:].startswith('.claude/tasks/'))


def file_digest(path):
    if path.is_symlink():
        raise ValueError('검사 대상 심볼릭 링크는 지원하지 않습니다: ' + str(path))
    digest = hashlib.sha256()
    size = 0
    with path.open('rb') as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.digest()


def add_file_digest(digest, rel):
    encoded = rel.as_posix().encode('utf-8', 'surrogateescape')
    digest.update(len(encoded).to_bytes(8, 'big'))
    digest.update(encoded)
    path = ROOT / rel
    if not path.exists() and not path.is_symlink():
        digest.update(b'M')
        return
    if not path.is_file() or path.is_symlink():
        raise ValueError('검사 대상은 일반 파일이어야 합니다: ' + str(rel))
    size, content_hash = file_digest(path)
    digest.update(b'F')
    digest.update(size.to_bytes(8, 'big'))
    digest.update(content_hash)


def excluded_file(rel):
    parts = rel.parts
    return (parts[:2] == ('.claude', 'tasks')
            or parts[:2] == ('docs', 'experiments')
            or any(part in EXCLUDED for part in parts[:-1])
            or rel.name == '.DS_Store' or rel.suffix == '.pyc')


def project_files():
    """Git supplies ignore rules in real projects; walk is the non-Git fallback."""
    paths = set()
    top = git('rev-parse', '--show-toplevel')
    if top and Path(top.strip()).resolve() == ROOT:
        try:
            result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                                    cwd=ROOT, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError('Git 검사 대상 목록을 만들 수 없습니다: ' + str(error)) from error
        if result.returncode != 0:
            raise ValueError('Git 검사 대상 목록을 만들 수 없습니다.')
        paths.update(Path(os.fsdecode(name)) for name in result.stdout.split(b'\0') if name)
    else:
        for base, dirs, files in os.walk(ROOT, followlinks=False):
            relbase = Path(base).relative_to(ROOT)
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED
                             and (relbase / d).parts[:2] not in {('.claude', 'tasks'), ('docs', 'experiments')})
            for directory in dirs:
                if (Path(base) / directory).is_symlink():
                    raise ValueError('검사 대상 심볼릭 링크 디렉토리는 지원하지 않습니다: ' + str(relbase / directory))
            paths.update(relbase / name for name in files)
    config = ROOT / '.claude/snapshot.json'
    if config.exists():
        extra = read_json(config).get('extra_files', [])
        if not isinstance(extra, list) or not all(isinstance(name, str) for name in extra):
            raise ValueError('snapshot.json의 extra_files는 경로 문자열 배열이어야 합니다.')
        for name in extra:
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or excluded_file(rel):
                raise ValueError('snapshot.json에 허용되지 않는 경로: ' + name)
            paths.add(rel)
    return sorted((p for p in paths if not excluded_file(p)), key=lambda p: p.as_posix())


def plan_snapshot(state):
    digest = hashlib.sha256()
    inputs = [task_dir(state) / 'task.md', ROOT / 'docs/testing-policy.md']
    policy_dir = ROOT / 'docs/testing'
    if policy_dir.exists():
        inputs.extend(sorted(policy_dir.rglob('*.md')))
    for path in inputs:
        rel = path.relative_to(ROOT)
        add_file_digest(digest, rel)
    return digest.hexdigest()


def table_rows(body, heading):
    match = re.search(r'^## ' + heading + r'\n(.*?)(?=^## |\Z)', body, re.M | re.S)
    if not match:
        return []
    return [line for line in match.group(1).splitlines()
            if line.startswith('|') and not re.match(r'\|\s*-', line)]


def section(body, heading):
    match = re.search(r'^## ' + re.escape(heading) + r'\n(.*?)(?=^## |\Z)', body, re.M | re.S)
    return match.group(1) if match else None


def cells(line):
    return [item.strip() for item in line.strip().strip('|').split('|')]


def requirement_ids(body):
    rows = table_rows(body, '요구사항')
    if not rows or cells(rows[0]) != ['ID', '조건·입력', '기대 동작·실패 조건'] or len(rows) < 2:
        raise ValueError('task.md의 요구사항 표에 ID·조건·기대 동작을 작성하세요.')
    ids = []
    for row in rows[1:]:
        values = cells(row)
        if (len(values) != 3 or not re.fullmatch(r'REQ-[0-9]{2,}', values[0])
                or not values[1] or not values[2] or values[0] in ids):
            raise ValueError('요구사항은 중복 없는 REQ-번호와 조건·기대 동작이 필요합니다.')
        ids.append(values[0])
    return ids


def require_clear_spec(state, body):
    if state.get('spec_protocol') != 1:
        return
    clarification = section(body, '요구사항 구체화')
    if clarification is None:
        raise ValueError('task.md에 요구사항 구체화 기록이 없습니다.')
    statuses = re.findall(r'^명세 상태:[ \t]*(.*?)[ \t]*$', clarification, re.M)
    if statuses != ['확정']:
        raise ValueError('명세 상태가 확정이어야 구현할 수 있습니다. 중요한 미결정 사항을 질문하세요.')
    for label in ('원문 요청', '요청 해석'):
        values = re.findall(r'^' + label + r':[ \t]*(.*?)[ \t]*$', clarification, re.M)
        if len(values) != 1 or not values[0]:
            raise ValueError('요구사항 구체화의 {}을 작성하세요.'.format(label))
    facts = re.findall(r'^조사한 사실:[ \t]*(.*?)(?=^질문하지 않은 이유:|^[ \t]*\||\Z)',
                       clarification, re.M | re.S)
    if len(facts) != 1 or not facts[0].strip():
        raise ValueError('요구사항 구체화의 조사한 사실을 작성하세요.')
    rows = table_rows(body, '요구사항 구체화')
    if not rows or cells(rows[0]) != ['질문 ID', '결정 사항과 영향', '사용자 답변·근거', '상태']:
        raise ValueError('질문·결정 기록 표를 작성하세요.')
    seen = set()
    for row in rows[1:]:
        values = cells(row)
        if (len(values) != 4 or not re.fullmatch(r'Q-[0-9]{2,}', values[0])
                or values[0] in seen or not values[1]
                or values[3] not in {'미결정', '해결', '범위 제외'}):
            raise ValueError('질문 기록은 중복 없는 Q-번호·결정 사항·상태가 필요합니다.')
        seen.add(values[0])
        if values[3] == '미결정':
            raise ValueError('미결정 사항 {}을 사용자와 구체화한 뒤 구현하세요.'.format(values[0]))
        if not values[2]:
            raise ValueError('질문 {}의 답변 또는 범위 제외 근거를 기록하세요.'.format(values[0]))
    if not seen:
        reasons = re.findall(r'^질문하지 않은 이유:[ \t]*(.*?)[ \t]*$', clarification, re.M)
        if len(reasons) != 1 or not reasons[0]:
            raise ValueError('질문 기록이 없다면 질문하지 않은 이유를 작성하세요.')
    requirement_ids(body)


def require_requirement_review(body, report):
    expected = set(requirement_ids(body))
    rows = table_rows(report, '요구사항별 검증')
    if not rows or cells(rows[0]) != ['요구사항 ID', '구현 위치', '테스트·실행 증거', '판정']:
        raise ValueError('review.md에 요구사항별 검증 표를 작성하세요.')
    seen = set()
    for row in rows[1:]:
        values = cells(row)
        if (len(values) != 4 or values[0] not in expected or values[0] in seen
                or not values[1] or not values[2] or values[3] != '통과'):
            raise ValueError('요구사항별 검증에는 각 ID의 구현 위치·테스트 증거·통과 판정이 필요합니다.')
        seen.add(values[0])
    if seen != expected:
        raise ValueError('검증 표에 누락된 요구사항이 있습니다: ' + ', '.join(sorted(expected - seen)))


def edit_decision(state, rel, corrupt):
    """Return a deny reason, or None when the edit is allowed."""
    if PROTECTED.search(rel):
        return '증거·상태·작업 목록은 workflow.py만 기록합니다.'
    if state and state['phase'] == 'implementing':
        planning_document = (rel.startswith('docs/') or ('/' not in rel and rel.endswith('.md'))
                             or re.fullmatch(r'\.claude/tasks/' + re.escape(state['id'])
                                             + r'/(' + '|'.join(PLANNING_DOCS) + r')\.md', rel))
        if state.get('spec_protocol') == 1 and not planning_document:
            try:
                body = (task_dir(state) / 'task.md').read_text(encoding='utf-8')
                require_clear_spec(state, body)
                if state.get('approved_plan_snapshot') != plan_snapshot(state):
                    return '명세·테스트 기준 변경 후 계획 검증과 start를 다시 실행해야 코드를 수정할 수 있습니다.'
            except (ValueError, OSError) as error:
                return str(error)
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
    for rel in project_files():
        add_file_digest(digest, rel)
    add_file_digest(digest, (task_dir(state) / 'task.md').relative_to(ROOT))
    return digest.hexdigest()


def require_phase(state, phases):
    if state['phase'] not in phases:
        raise ValueError('현재 단계 {}에서는 실행할 수 없습니다: {}'.format(state['phase'], ', '.join(phases)))


def checked(state):
    evidence = task_dir(state) / 'evidence'
    latest = evidence / 'checks.json'
    receipt = read_json(latest)
    if (not receipt.get('passed')
            or not (receipt.get('results') or receipt.get('skipped_checks'))):
        raise ValueError('필수 검사가 통과하지 않았습니다.')
    archive = receipt.get('archive')
    if state.get('verify_attempts', 0) and not archive:
        raise ValueError('검사 증거 보관본이 없습니다.')
    if archive:
        if not re.fullmatch(r'verify-[0-9]{3,}/checks\.json', archive):
            raise ValueError('검사 증거 보관 경로가 잘못되었습니다.')
        if (evidence / archive).read_bytes() != latest.read_bytes():
            raise ValueError('최신 검사 증거와 보관본이 다릅니다.')
    if receipt['snapshot'] != snapshot(state):
        raise ValueError('검사 이후 코드·설정·요구사항이 바뀌었습니다. start 후 verify를 다시 실행하세요.')
    for result in receipt['results']:
        name = result['log']
        if (not isinstance(name, str) or
                not re.fullmatch(r'verify-[0-9]{3,}/check-[0-9]+\.log', name)
                or (result.get('required', True) and result['exit_code'] != 0)):
            raise ValueError('검사 증거 형식이 잘못되었습니다.')
        actual = hashlib.sha256((evidence / name).read_bytes()).hexdigest()
        if result['log_hash'] != actual:
            raise ValueError('검사 로그가 변경되었습니다. 검사를 다시 실행하세요.')
    return receipt


def configured_checks():
    """Return resolved checks and their source, preserving the legacy format."""
    config_path = ROOT / 'harness.json'
    if config_path.exists():
        root_text = str(ROOT)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
        try:
            from harness.config import load_config
        except ImportError as error:
            raise ValueError('harness.json을 사용하려면 harness 설정 모듈을 설치해야 합니다: ' + str(error))
        checks = load_config(ROOT).get('checks', [])
        if not checks:
            raise ValueError('harness.json에 실행할 검사를 하나 이상 등록하세요.')
        return checks, 'harness.json'

    legacy_path = ROOT / '.claude/checks.json'
    checks = read_json(legacy_path).get('checks', [])
    if not isinstance(checks, list) or not checks:
        raise ValueError('.claude/checks.json에 앱의 필수 검사 명령을 먼저 등록하세요.')
    normalized = []
    for index, check in enumerate(checks):
        if (not isinstance(check, dict)
                or not isinstance(check.get('argv'), list) or not check['argv']
                or not all(isinstance(x, str) and x for x in check['argv'])
                or type(check.get('timeout_seconds')) is not int
                or not 1 <= check['timeout_seconds'] <= 3600):
            raise ValueError('검사에는 argv 문자열 배열과 1~3600의 timeout_seconds가 필요합니다.')
        normalized.append({
            'id': 'legacy-check-{}'.format(index + 1),
            'kind': 'unit',
            'cwd': '.',
            'argv': check['argv'],
            'required': True,
            'timeout_seconds': check['timeout_seconds'],
        })
    return normalized, '.claude/checks.json'


def check_condition(check):
    """Return whether a check applies and a stable evidence reason."""
    condition = check.get('when')
    if not condition:
        return True, None

    def matches(pattern):
        return bool(glob.glob(str(ROOT / pattern), recursive=True))

    any_patterns = condition.get('files_any')
    if any_patterns and not any(matches(pattern) for pattern in any_patterns):
        return False, 'when.files_any 불일치'
    all_patterns = condition.get('files_all')
    if all_patterns and not all(matches(pattern) for pattern in all_patterns):
        return False, 'when.files_all 불일치'
    return True, None


def command_available(command, cwd):
    """Check optional commands without invoking a shell."""
    if '/' in command or '\\' in command:
        candidate = Path(command)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        return candidate.is_file() and (os.name == 'nt' or os.access(str(candidate), os.X_OK))
    return shutil.which(command) is not None


def complete_evidence(state):
    receipt = checked(state)
    evidence = task_dir(state) / 'evidence'
    review_path = evidence / 'review.json'
    review = read_json(review_path)
    if review.get('verification') and review['verification'] != receipt.get('archive'):
        raise ValueError('검토 대상 검사 실행이 최신 결과와 다릅니다.')
    archived_review = evidence / ('review-{}.json'.format(state.get('review_attempts', 0)))
    if archived_review.exists() and archived_review.read_bytes() != review_path.read_bytes():
        raise ValueError('최신 리뷰 증거와 회차별 보관본이 다릅니다.')
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
    if state.get('spec_protocol') == 1:
        body = (task_dir(state) / 'task.md').read_text(encoding='utf-8')
        require_clear_spec(state, body)
        if state.get('approved_plan_snapshot') != plan_snapshot(state):
            raise ValueError('명세·테스트 기준이 바뀌었습니다. verifier 계획 검증 후 start를 다시 실행하세요.')
    checks, checks_source = configured_checks()
    before = snapshot(state)
    state['verify_attempts'] = state.get('verify_attempts', 0) + 1
    state['phase'] = 'verifying'
    save(state)
    evidence = task_dir(state) / 'evidence'
    evidence.mkdir(exist_ok=True)
    run_dir = evidence / ('verify-{:03d}'.format(state['verify_attempts']))
    run_dir.mkdir(exist_ok=False)
    started = now()
    head, porcelain = git('rev-parse', 'HEAD'), source_status()
    base = state.get('base_head')
    if porcelain is None:
        changes = 'git 저장소가 아니거나 git을 실행할 수 없습니다.\n'
    else:
        changes = ('# 작업 시작 HEAD\n' + (base or '기록 없음') + '\n'
                   '# 작업 시작 시 변경 상태\n' + state.get('base_status', '') + '\n'
                   '# 현재 git status --porcelain\n' + porcelain + '\n')
        if base:
            names = git('diff', '--name-status', base, '--', '.',
                        ':(exclude).claude/tasks/**')
            stat = git('diff', '--stat', base, '--', '.',
                       ':(exclude).claude/tasks/**')
            changes += ('# 작업 시작 HEAD부터 현재 파일까지의 이름·상태\n'
                        + (names if names is not None else '비교 실패: 시작 커밋을 확인하세요.') + '\n'
                        '# 작업 시작 HEAD부터 현재 파일까지의 통계\n'
                        + (stat if stat is not None else '비교 실패: 시작 커밋을 확인하세요.') + '\n')
        else:
            changes += '# 시작 커밋이 없어 커밋된 변경 범위를 계산할 수 없습니다.\n'
    (run_dir / 'changes.txt').write_text(changes, encoding='utf-8')
    (evidence / 'changes.txt').write_text(changes, encoding='utf-8')
    results = []
    skipped = []
    for i, check in enumerate(checks):
        applies, skip_reason = check_condition(check)
        check_cwd = (ROOT / check.get('cwd', '.')).resolve()
        base_evidence = {
            'id': check.get('id', 'check-{}'.format(i + 1)),
            'argv': check['argv'],
            'cwd': check_cwd.relative_to(ROOT).as_posix() if check_cwd != ROOT else '.',
            'required': check.get('required', True),
        }
        if not applies:
            skipped.append(dict(base_evidence, status='skipped', reason=skip_reason))
            continue
        if not check.get('required', True) and not command_available(check['argv'][0], check_cwd):
            skipped.append(dict(base_evidence, status='skipped', reason='선택 검사 명령을 찾을 수 없음'))
            continue
        log = run_dir / ('check-{}.log'.format(i + 1))
        check_started = datetime.now(timezone.utc)
        with log.open('w', encoding='utf-8') as output:
            try:
                # No shell interpolation; commands are explicitly configured argument arrays.
                process = subprocess.Popen(check['argv'], cwd=check_cwd, stdout=output,
                                           stderr=subprocess.STDOUT,
                                           start_new_session=(os.name != 'nt'))
                try:
                    code = process.wait(timeout=check['timeout_seconds'])
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        process.kill()
                    else:
                        import signal
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait()
                    output.write('\n검사 시간 초과\n')
                    code = 124
            except OSError as error:
                output.write(str(error) + '\n')
                code = 127
        result = dict(base_evidence, status='passed' if code == 0 else 'failed',
                      exit_code=code, log=run_dir.name + '/' + log.name,
                      log_hash=hashlib.sha256(log.read_bytes()).hexdigest(),
                      duration_seconds=round(
                          (datetime.now(timezone.utc) - check_started).total_seconds(), 3))
        results.append(result)
        if code != 0 and check.get('required', True):
            for remaining in checks[i + 1:]:
                remaining_cwd = (ROOT / remaining.get('cwd', '.')).resolve()
                skipped.append({
                    'id': remaining.get('id', 'check-{}'.format(len(skipped) + i + 2)),
                    'argv': remaining['argv'],
                    'cwd': remaining_cwd.relative_to(ROOT).as_posix() if remaining_cwd != ROOT else '.',
                    'required': remaining.get('required', True),
                    'status': 'skipped',
                    'reason': '앞선 필수 검사 실패',
                })
            break
    after = snapshot(state)
    required_failed = any(result['required'] and result['exit_code'] != 0 for result in results)
    passed = not required_failed and before == after
    receipt = {
        'passed': passed, 'snapshot': after, 'unchanged_during_checks': before == after,
        'started_at': started, 'finished_at': now(), 'python': sys.version.split()[0],
        'archive': run_dir.name + '/checks.json', 'checks_source': checks_source,
        'skipped_checks': skipped,
        # Program-owned task records are removed from the dirty calculation.
        'git': {'head': head.strip() if head else None, 'dirty': None if porcelain is None else bool(porcelain.strip())},
        'results': results}
    write_json(run_dir / 'checks.json', receipt)
    shutil.copyfile(run_dir / 'checks.json', evidence / 'checks.json')
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
        stage = 'result' if state['phase'] == 'reviewing' and state.get('review_pending') else 'plan'
        state.setdefault('verifier_calls', []).append({
            'at': now(), 'phase': state['phase'], 'stage': stage,
            'attempt': state.get('review_attempts', 0), 'snapshot': state.get('review_pending'),
            'plan_snapshot': plan_snapshot(state) if stage == 'plan' else None})
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
        base_head = (git('rev-parse', 'HEAD') or '').strip() or None
        base_status = source_status() or ''
        directory = TASKS / args.value
        directory.mkdir(parents=True, exist_ok=False)
        template = ROOT / '.claude/skills/plan-task/templates/task.md'
        (directory / 'task.md').write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
        (directory / 'progress.md').write_text('# 진행 기록\n\n작업 생성. 다음 행동: 요구사항과 완료 기준 작성.\n', encoding='utf-8')
        save({'id': args.value, 'phase': 'planning', 'spec_protocol': 1, 'base_head': base_head,
              'base_status': base_status})
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
        require_clear_spec(state, body)
        for heading in ('목표', '범위', '완료 기준'):
            match = re.search(r'^## ' + heading + r'\n(.*?)(?=^## |\Z)', body, re.M | re.S)
            if not match or not match.group(1).strip():
                raise ValueError('task.md의 {} 항목을 작성하세요.'.format(heading))
        for heading in ('적용 영역과 상세 기준', '일반 테스트 방법'):
            if len(table_rows(body, heading)) < 2:
                raise ValueError('task.md의 {} 표에 데이터 행을 작성하세요.'.format(heading))
        current_plan = plan_snapshot(state)
        # New protocol tasks must reapprove changed requirements, even after implementation began.
        if not state.get('started') or (state.get('spec_protocol') == 1
                                         and state.get('approved_plan_snapshot') != current_plan):
            review = directory / 'plan-review.md'
            if not review.exists():
                raise ValueError('plan-review.md에 verifier의 계획 검증 판정을 남기세요.')
            report = review.read_text(encoding='utf-8')
            verdicts = re.findall(r'^최종 판정:[ \t]*(.*?)[ \t]*$', report, re.M)
            if verdicts != ['통과 권고']:
                raise ValueError('plan-review.md에 최종 판정: 통과 권고를 한 줄로 한 번만 기록하세요.')
            markers = re.findall(r'^대상 계획 지문:[ \t]*(.*?)[ \t]*$', report, re.M)
            if markers != [current_plan]:
                raise ValueError('계획 검증 이후 task.md 또는 테스트 기준이 바뀌었습니다. verifier에게 다시 요청하세요.')
            if not any(call.get('stage') == 'plan' and call.get('plan_snapshot') == current_plan
                       for call in state.get('verifier_calls', [])):
                raise ValueError('현재 계획 지문의 verifier 호출 기록이 없습니다.')
        if state.get('spec_protocol') == 1:
            state['approved_plan_snapshot'] = current_plan
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
            if state.get('spec_protocol') == 1:
                require_requirement_review((directory / 'task.md').read_text(encoding='utf-8'), text)
        attempt = state['review_attempts']
        review_receipt = {'verdict': args.value, 'snapshot': receipt['snapshot'],
                          'report_hash': hashlib.sha256(report).hexdigest(),
                          'verification': receipt.get('archive')}
        write_json(directory / 'evidence' / ('review-{}.json'.format(attempt)), review_receipt)
        write_json(directory / 'evidence/review.json', review_receipt)
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

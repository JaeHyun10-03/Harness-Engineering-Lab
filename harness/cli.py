"""Command line interface for installing and diagnosing the harness."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from harness.install import (
    InstallError,
    InstallResult,
    init_project,
    read_installation,
    upgrade_project,
)


@dataclass(frozen=True)
class Diagnostic:
    level: str
    code: str
    message: str


def _config_api():
    try:
        module = importlib.import_module("harness.config")
        return module.load_config, module.validate_config, module.ConfigError
    except (ImportError, AttributeError) as error:
        raise RuntimeError(
            "harness.config의 load_config, validate_config, ConfigError가 필요합니다."
        ) from error


def validate_project(project_root: Path) -> List[Diagnostic]:
    diagnostics: List[Diagnostic] = []
    try:
        load_config, _, _ = _config_api()
        normalized = load_config(project_root)
    except RuntimeError as error:
        return [Diagnostic("FAIL", "config-api", str(error))]
    except Exception as error:  # ConfigError is loaded dynamically.
        errors = getattr(error, "errors", None)
        if errors:
            return [Diagnostic("FAIL", "config", str(item)) for item in errors]
        return [Diagnostic("FAIL", "config", str(error))]

    checks = normalized.get("checks", []) if isinstance(normalized, Mapping) else []
    profiles = normalized.get("profiles", []) if isinstance(normalized, Mapping) else []
    diagnostics.append(
        Diagnostic(
            "PASS",
            "config",
            "설정 유효: profile {}개, check {}개".format(len(profiles), len(checks)),
        )
    )
    return diagnostics


def _load_effective_config(project_root: Path) -> Optional[Mapping[str, Any]]:
    try:
        load_config, _, _ = _config_api()
        normalized = load_config(project_root)
        return normalized if isinstance(normalized, Mapping) else None
    except Exception:
        return None


def _run_git(project_root: Path) -> Diagnostic:
    executable = shutil.which("git")
    if not executable:
        return Diagnostic("FAIL", "git", "git 실행 파일을 찾을 수 없음")
    try:
        result = subprocess.run(
            [executable, "-C", str(project_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Diagnostic("FAIL", "git", "git 진단 실패: {}".format(error))
    if result.returncode != 0 or result.stdout.strip() != "true":
        return Diagnostic("FAIL", "git", "프로젝트가 Git 작업 트리가 아님")
    return Diagnostic("PASS", "git", "Git 작업 트리 확인")


def _settings_diagnostic(project_root: Path) -> Diagnostic:
    path = project_root / ".claude/settings.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Diagnostic("FAIL", "claude-hooks", ".claude/settings.json 없음")
    except (OSError, json.JSONDecodeError) as error:
        return Diagnostic("FAIL", "claude-hooks", "settings.json 오류: {}".format(error))
    hooks = value.get("hooks", {}) if isinstance(value, dict) else {}
    missing = [name for name in ("SessionStart", "PreToolUse", "Stop") if not hooks.get(name)]
    serialized = json.dumps(hooks, ensure_ascii=False)
    script = project_root / ".claude/hooks/workflow.py"
    if missing:
        return Diagnostic("FAIL", "claude-hooks", "Hook 누락: {}".format(", ".join(missing)))
    if "workflow.py" not in serialized or not script.is_file():
        return Diagnostic("FAIL", "claude-hooks", "workflow.py Hook 연결을 확인할 수 없음")
    return Diagnostic("PASS", "claude-hooks", "필수 Claude Code Hook 연결 확인")


def _command_diagnostics(project_root: Path, config: Optional[Mapping[str, Any]]) -> List[Diagnostic]:
    if not config:
        return [Diagnostic("FAIL", "checks", "유효한 설정이 없어 check 명령을 진단할 수 없음")]
    checks = config.get("checks", [])
    if not checks:
        return [Diagnostic("FAIL", "checks", "등록된 check가 없음")]
    results: List[Diagnostic] = []
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            results.append(Diagnostic("FAIL", "check-{}".format(index + 1), "check 형식 오류"))
            continue
        check_id = str(check.get("id") or "check-{}".format(index + 1))
        required = check.get("required", True) is not False
        argv = check.get("argv")
        cwd_value = check.get("cwd", ".")
        cwd = project_root / str(cwd_value)
        if not cwd.is_dir():
            level = "FAIL" if required else "WARN"
            results.append(Diagnostic(level, check_id, "실행 디렉터리 없음: {}".format(cwd_value)))
            continue
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            results.append(Diagnostic("FAIL", check_id, "argv가 문자열 배열이 아님"))
            continue
        executable = argv[0]
        if "/" in executable or "\\" in executable:
            candidate = cwd / executable
            available = candidate.is_file()
        else:
            available = shutil.which(executable) is not None
        if available:
            results.append(Diagnostic("PASS", check_id, "명령 사용 가능: {}".format(executable)))
        else:
            level = "FAIL" if required else "WARN"
            results.append(Diagnostic(level, check_id, "명령을 찾을 수 없음: {}".format(executable)))
    return results


def doctor_project(project_root: Path) -> List[Diagnostic]:
    project_root = project_root.resolve()
    diagnostics = [
        Diagnostic(
            "PASS" if sys.version_info >= (3, 9) else "FAIL",
            "python",
            "Python {}.{}.{}".format(*sys.version_info[:3]),
        ),
        _run_git(project_root),
    ]
    claude = shutil.which("claude")
    diagnostics.append(
        Diagnostic("PASS" if claude else "FAIL", "claude", "Claude Code {}".format(claude or "없음"))
    )
    diagnostics.append(_settings_diagnostic(project_root))
    diagnostics.extend(validate_project(project_root))
    diagnostics.extend(_command_diagnostics(project_root, _load_effective_config(project_root)))
    try:
        installation = read_installation(project_root)
        diagnostics.append(
            Diagnostic(
                "PASS" if installation else "WARN",
                "installation",
                "설치 release {}".format(installation.get("release"))
                if installation
                else "설치 기록 없음; init으로 관리 상태를 기록하세요",
            )
        )
    except InstallError as error:
        diagnostics.append(Diagnostic("FAIL", "installation", str(error)))
    return diagnostics


def _print_diagnostics(items: Sequence[Diagnostic], json_output: bool) -> None:
    if json_output:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
        return
    for item in items:
        print("[{:<4}] {:<18} {}".format(item.level, item.code, item.message))


def _print_install(result: InstallResult, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "command": result.command,
                    "dry_run": result.dry_run,
                    "release": result.release,
                    "ok": result.ok,
                    "actions": [asdict(item) for item in result.actions],
                    "conflicts": [asdict(item) for item in result.conflicts],
                    "warnings": result.warnings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    prefix = "DRY-RUN " if result.dry_run else ""
    print("{}{} release {}".format(prefix, result.command, result.release))
    for item in result.actions:
        print("[{:<8}] {} ({})".format(item.action.upper(), item.path, item.reason))
    for item in result.conflicts:
        print("[CONFLICT] {} ({})".format(item.path, item.reason))
    for warning in result.warnings:
        print("[WARN] {}".format(warning))
    if result.conflicts:
        print("충돌이 있어 어떤 파일도 변경하지 않았습니다.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harness.py", description="범용 개발 하네스 관리 CLI")
    parser.add_argument("--project", default=".", help="대상 프로젝트 경로 (기본: 현재 디렉터리)")
    parser.add_argument("--json", action="store_true", help="기계 판독 가능한 JSON 출력")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "upgrade"):
        command = subparsers.add_parser(name)
        command.add_argument("--dry-run", action="store_true", help="변경 계획만 출력")
    subparsers.add_parser("validate")
    subparsers.add_parser("doctor")
    return parser


def main(argv: Optional[Sequence[str]] = None, source_root: Optional[Path] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = Path(args.project)
    distribution_root = source_root or Path(__file__).resolve().parents[1]
    try:
        if args.command == "init":
            result = init_project(project_root, distribution_root, dry_run=args.dry_run)
            _print_install(result, args.json)
            return 0 if result.ok else 1
        if args.command == "upgrade":
            result = upgrade_project(project_root, distribution_root, dry_run=args.dry_run)
            _print_install(result, args.json)
            return 0 if result.ok else 1
        diagnostics = (
            validate_project(project_root.resolve())
            if args.command == "validate"
            else doctor_project(project_root.resolve())
        )
        _print_diagnostics(diagnostics, args.json)
        return 1 if any(item.level == "FAIL" for item in diagnostics) else 0
    except (InstallError, OSError) as error:
        if args.json:
            print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False, indent=2))
        else:
            print("오류: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

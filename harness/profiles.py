"""Load and resolve project-independent harness profiles."""

from pathlib import Path
import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .schema import (
    ConfigError,
    copied_mapping,
    is_plain_mapping,
    raise_if_errors,
    reject_unknown,
    require_version,
    validate_check,
    validate_identifier,
    validate_string_list,
    validate_unique_ids,
)


TOKEN = re.compile(r"\$\{([a-z][a-z0-9_]*)\}")
OPTION_TYPES = {"string", "path", "enum"}


def load_profile(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(("프로필 파일을 찾을 수 없습니다: {}".format(path),)) from error
    except json.JSONDecodeError as error:
        raise ConfigError((
            "{}:{}:{}: JSON 문법 오류: {}".format(path, error.lineno, error.colno, error.msg),
        )) from error
    return validate_profile(raw, source=str(path))


def validate_profile(data: Any, *, source: str = "profile") -> Dict[str, Any]:
    errors: List[str] = []
    if not is_plain_mapping(data):
        raise ConfigError(("{}: 최상위 값은 객체여야 합니다".format(source),))
    reject_unknown(
        data,
        ("schema_version", "id", "category", "description", "options", "checks", "rules"),
        source,
        errors,
    )
    require_version(data, source, errors)
    validate_identifier(data.get("id"), source + ".id", errors)
    validate_identifier(data.get("category"), source + ".category", errors)
    if not isinstance(data.get("description"), str) or not data.get("description", "").strip():
        errors.append(source + ".description: 비어 있지 않은 문자열이어야 합니다")

    options = data.get("options", {})
    if not is_plain_mapping(options):
        errors.append(source + ".options: 객체여야 합니다")
        options = {}
    for name, definition in options.items():
        option_path = "{}.options.{}".format(source, name)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            errors.append(option_path + ": 옵션 이름은 snake_case여야 합니다")
            continue
        validate_option_definition(definition, option_path, errors)

    checks = data.get("checks", [])
    if not isinstance(checks, list):
        errors.append(source + ".checks: 배열이어야 합니다")
        checks = []
    else:
        for index, check in enumerate(checks):
            validate_check(check, "{}.checks[{}]".format(source, index), errors,
                           allow_templates=True)
        validate_unique_ids(checks, source + ".checks", errors)

    rules = data.get("rules", [])
    validate_string_list(rules, source + ".rules", errors)
    if is_plain_mapping(options):
        known = set(options)
        for location, text in template_strings(checks):
            for token in TOKEN.findall(text):
                if token not in known:
                    errors.append("{}: 정의되지 않은 옵션 '${{{}}}'".format(location, token))
    raise_if_errors(errors)
    return copied_mapping(data)


def validate_option_definition(value: Any, path: str, errors: List[str]) -> None:
    if not is_plain_mapping(value):
        errors.append(path + ": 객체여야 합니다")
        return
    reject_unknown(value, ("type", "default", "values", "description"), path, errors)
    option_type = value.get("type")
    if option_type not in OPTION_TYPES:
        errors.append("{}.type: 다음 값 중 하나여야 합니다: {}".format(
            path, ", ".join(sorted(OPTION_TYPES))))
        return
    default = value.get("default")
    if not isinstance(default, str) or not default:
        errors.append(path + ".default: 비어 있지 않은 문자열이어야 합니다")
    values = value.get("values")
    if option_type == "enum":
        validate_string_list(values, path + ".values", errors, allow_empty=False)
        if isinstance(default, str) and isinstance(values, list) and default not in values:
            errors.append(path + ".default: values에 포함되어야 합니다")
    elif "values" in value:
        errors.append(path + ".values: enum 옵션에서만 사용할 수 있습니다")
    if "description" in value and (
        not isinstance(value["description"], str) or not value["description"].strip()
    ):
        errors.append(path + ".description: 비어 있지 않은 문자열이어야 합니다")


def template_strings(value: Any, path: str = "profile.checks"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from template_strings(item, "{}[{}]".format(path, index))
    elif is_plain_mapping(value):
        for key, item in value.items():
            yield from template_strings(item, "{}.{}".format(path, key))


def resolve_profile(profile: Mapping[str, Any], selection: Mapping[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    definitions = profile.get("options", {})
    supplied = selection.get("options", {})
    if not is_plain_mapping(supplied):
        raise ConfigError(("profiles 옵션은 객체여야 합니다: {}".format(profile["id"]),))
    for name in supplied:
        if name not in definitions:
            errors.append("profiles[{}].options.{}: 프로필에 없는 옵션".format(profile["id"], name))
    resolved_options = {
        name: supplied.get(name, definition["default"])
        for name, definition in definitions.items()
    }
    for name, value in resolved_options.items():
        definition = definitions[name]
        option_path = "profiles[{}].options.{}".format(profile["id"], name)
        if not isinstance(value, str) or not value:
            errors.append(option_path + ": 비어 있지 않은 문자열이어야 합니다")
        elif definition["type"] == "enum" and value not in definition["values"]:
            errors.append("{}: 허용값은 {}입니다".format(
                option_path, ", ".join(definition["values"])))
        elif definition["type"] == "path":
            from .schema import validate_relative_path
            validate_relative_path(value, option_path, errors)
    raise_if_errors(errors)
    checks = [_substitute(check, resolved_options) for check in profile.get("checks", [])]
    return {
        "id": profile["id"],
        "category": profile["category"],
        "description": profile["description"],
        "options": resolved_options,
        "checks": checks,
        "rules": list(profile.get("rules", [])),
    }


def _substitute(value: Any, options: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return TOKEN.sub(lambda match: options[match.group(1)], value)
    if isinstance(value, list):
        return [_substitute(item, options) for item in value]
    if is_plain_mapping(value):
        return {key: _substitute(item, options) for key, item in value.items()}
    return value


def load_selected_profiles(selections: Sequence[Mapping[str, Any]],
                           profiles_dir: Path) -> List[Dict[str, Any]]:
    resolved = []
    errors = []
    categories = {}
    for index, selection in enumerate(selections):
        if not selection.get("enabled", True):
            continue
        identifier = selection.get("id")
        if not isinstance(identifier, str):
            continue
        path = profiles_dir / (identifier + ".json")
        try:
            profile = load_profile(path)
            if profile["id"] != identifier:
                errors.append(
                    "profiles[{}]: 파일의 id '{}'가 선택한 id '{}'와 다릅니다".format(
                        index, profile["id"], identifier
                    )
                )
                continue
            category = profile["category"]
            if category in categories:
                errors.append(
                    "profiles[{}]: category '{}'는 '{}'와 동시에 활성화할 수 없습니다".format(
                        index, category, categories[category]
                    )
                )
                continue
            categories[category] = identifier
            resolved.append(resolve_profile(profile, selection))
        except ConfigError as error:
            errors.extend(error.errors)
    raise_if_errors(errors)
    return resolved

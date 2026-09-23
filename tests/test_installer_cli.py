"""Tests for safe harness installation and diagnostics."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from harness.cli import _command_diagnostics, main, validate_project
from harness.config import load_config
from harness.install import _project_config, init_project, read_installation, upgrade_project


ROOT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.source_temp = tempfile.TemporaryDirectory(prefix="harness-source-")
        self.project_temp = tempfile.TemporaryDirectory(prefix="harness-project-")
        self.addCleanup(self.source_temp.cleanup)
        self.addCleanup(self.project_temp.cleanup)
        self.source = Path(self.source_temp.name)
        self.project = Path(self.project_temp.name)
        (self.source / "harness").mkdir()
        (self.source / "core").mkdir()
        (self.source / "core/rules.md").write_text("v1\n", encoding="utf-8")
        (self.source / "harness.json").write_text('{"project": "seed"}\n', encoding="utf-8")
        (self.source / "harness/manifest.json").write_text(
            json.dumps({
                "schema_version": 1,
                "release": "1.0.0",
                "include": ["core/*.md", "harness/manifest.json"],
            }),
            encoding="utf-8",
        )

    def test_init_is_dry_run_safe_and_idempotent(self):
        preview = init_project(self.project, self.source, dry_run=True)
        self.assertTrue(preview.ok)
        self.assertFalse((self.project / "core/rules.md").exists())

        first = init_project(self.project, self.source)
        self.assertTrue(first.ok)
        self.assertEqual((self.project / "core/rules.md").read_text(), "v1\n")
        generated = json.loads((self.project / "harness.json").read_text())
        self.assertEqual(generated["schema_version"], 1)
        self.assertEqual(generated["project"]["source_paths"], ["."])
        self.assertEqual(read_installation(self.project)["release"], "1.0.0")

        second = init_project(self.project, self.source)
        self.assertTrue(second.ok)
        self.assertFalse(second.changed)

    def test_init_conflict_aborts_all_writes(self):
        (self.project / "core").mkdir()
        (self.project / "core/rules.md").write_text("local\n", encoding="utf-8")
        result = init_project(self.project, self.source)
        self.assertFalse(result.ok)
        self.assertEqual((self.project / "core/rules.md").read_text(), "local\n")
        self.assertFalse((self.project / ".harness/installation.json").exists())
        self.assertFalse((self.project / "harness/manifest.json").exists())

    def test_upgrade_updates_only_unchanged_managed_files(self):
        init_project(self.project, self.source)
        (self.source / "core/rules.md").write_text("v2\n", encoding="utf-8")
        manifest = json.loads((self.source / "harness/manifest.json").read_text())
        manifest["release"] = "2.0.0"
        (self.source / "harness/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        result = upgrade_project(self.project, self.source)
        self.assertTrue(result.ok)
        self.assertEqual((self.project / "core/rules.md").read_text(), "v2\n")
        self.assertEqual(read_installation(self.project)["release"], "2.0.0")

        (self.project / "core/rules.md").write_text("project edit\n", encoding="utf-8")
        (self.source / "core/rules.md").write_text("v3\n", encoding="utf-8")
        conflict = upgrade_project(self.project, self.source)
        self.assertFalse(conflict.ok)
        self.assertEqual((self.project / "core/rules.md").read_text(), "project edit\n")

    def test_upgrade_never_changes_project_owned_config(self):
        init_project(self.project, self.source)
        (self.project / "harness.json").write_text('{"project": "custom"}\n', encoding="utf-8")
        (self.source / "harness.json").write_text('{"project": "new-seed"}\n', encoding="utf-8")
        result = upgrade_project(self.project, self.source)
        self.assertTrue(result.ok)
        self.assertEqual(json.loads((self.project / "harness.json").read_text())["project"], "custom")

    def test_cli_json_dry_run(self):
        result = main(
            ["--project", str(self.project), "--json", "init", "--dry-run"],
            source_root=self.source,
        )
        self.assertEqual(result, 0)
        self.assertFalse((self.project / ".harness").exists())

    def test_detected_fullstack_config_resolves_for_gradle_and_maven(self):
        for build_system in ("gradle", "maven"):
            with self.subTest(build_system=build_system), tempfile.TemporaryDirectory(
                prefix="detected-fullstack-"
            ) as directory:
                root = Path(directory)
                shutil.copytree(ROOT / "profiles", root / "profiles")
                frontend = root / "frontend"
                backend = root / "backend"
                workflows = root / ".github/workflows"
                frontend.mkdir(parents=True)
                backend.mkdir(parents=True)
                workflows.mkdir(parents=True)
                (frontend / "package.json").write_text(json.dumps({
                    "dependencies": {"next": "15", "react": "19"},
                    "devDependencies": {"typescript": "5"},
                }), encoding="utf-8")
                if build_system == "gradle":
                    (backend / "build.gradle.kts").write_text(
                        'plugins { id("org.springframework.boot") }\n'
                        'runtimeOnly("org.postgresql:postgresql")\n', encoding="utf-8"
                    )
                    (backend / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
                    expected_build = ["./gradlew", "build"]
                else:
                    (backend / "pom.xml").write_text(
                        "<project><parent><artifactId>spring-boot-starter-parent</artifactId></parent>"
                        "<dependency><artifactId>postgresql</artifactId></dependency></project>",
                        encoding="utf-8",
                    )
                    (backend / "mvnw").write_text("#!/bin/sh\n", encoding="utf-8")
                    expected_build = ["./mvnw", "package"]
                (workflows / "deploy.yml").write_text(
                    "uses: aws-actions/configure-aws-credentials@v4\n# deploy to EC2\n",
                    encoding="utf-8",
                )
                (root / "harness.json").write_bytes(_project_config(root))

                resolved = load_config(root)

                profile_ids = {profile["id"] for profile in resolved["profiles"]}
                self.assertEqual(profile_ids, {
                    "frontend-nextjs", "backend-spring", "database-postgresql",
                    "devops-github-actions-ec2",
                })
                checks = {check["id"]: check for check in resolved["checks"]}
                self.assertEqual(checks["backend-build"]["argv"], expected_build)
                self.assertEqual(
                    checks["devops-actions-lint"]["when"]["files_any"],
                    [".github/workflows/*.yml", ".github/workflows/*.yaml"],
                )


class DiagnosticTests(unittest.TestCase):
    def test_validate_reports_all_config_errors(self):
        with tempfile.TemporaryDirectory(prefix="invalid-config-") as directory:
            root = Path(directory)
            (root / "harness.json").write_text(
                '{"schema_version": 999, "unknown": true}', encoding="utf-8"
            )
            results = validate_project(root)
            self.assertGreaterEqual(len(results), 2)
            self.assertTrue(all(item.level == "FAIL" for item in results))

    def test_command_diagnostics_respect_required_flag(self):
        with tempfile.TemporaryDirectory(prefix="check-command-") as directory:
            root = Path(directory)
            config = {"checks": [
                {"id": "required", "cwd": ".", "argv": ["missing-required-command"], "required": True},
                {"id": "optional", "cwd": ".", "argv": ["missing-optional-command"], "required": False},
            ]}
            results = _command_diagnostics(root, config)
            self.assertEqual([item.level for item in results], ["FAIL", "WARN"])


if __name__ == "__main__":
    unittest.main()

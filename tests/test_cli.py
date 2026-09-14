from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from iphone_image.cli.main import cli


@pytest.fixture
def run(config_file: Path):
    runner = CliRunner()

    def invoke(*args: str, config: bool = True):
        argv = (["--config", str(config_file)] if config else []) + list(args)
        return runner.invoke(cli, argv, catch_exceptions=False)

    return invoke


def test_help_works() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "keep media on the iPhone" in result.output


def test_status_on_an_empty_database(run) -> None:
    result = run("status")
    assert result.exit_code == 0
    assert "iPhone Image Manager" in result.output


def test_status_creates_the_database(run, tmp_path: Path) -> None:
    assert not (tmp_path / "db.sqlite").exists()
    assert run("status").exit_code == 0
    assert (tmp_path / "db.sqlite").exists()


def test_status_json_is_parseable(run) -> None:
    result = run("--json", "status")
    assert result.exit_code == 0
    data = json.loads(result.output)
    # Derived, not hardcoded: a literal here breaks on every migration and
    # teaches you to edit the test rather than read it.
    from iphone_image.db.database import discover_migrations

    assert data["schemaVersion"] == len(discover_migrations())
    assert data["assetsOnPhone"] == 0
    assert data["removalPolicy"] == "never"


def test_config_validate_reports_removal_disabled(run) -> None:
    result = run("config", "validate")
    assert result.exit_code == 0
    assert "removal is disabled" in result.output


def test_config_validate_warns_when_removal_is_armed(tmp_path: Path) -> None:
    path = tmp_path / "armed.yaml"
    path.write_text(
        f'database: {{path: "{tmp_path}/db.sqlite"}}\n'
        "remove_from_iphone: {policy: local_verified}\n"
        "safety: {require_cloud_verification: false}\n"
    )
    result = CliRunner().invoke(cli, ["--config", str(path), "config", "validate"])
    assert result.exit_code == 0
    assert "ARMED" in result.output


def test_config_show_json_round_trips(run) -> None:
    result = run("--json", "config", "show")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["remove_from_iphone"]["policy"] == "never"
    assert data["retention"]["screenshots"] == "never"


def test_config_init_writes_a_loadable_file(tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    runner = CliRunner()
    assert runner.invoke(cli, ["config", "init", "--path", str(target)]).exit_code == 0
    assert target.exists()
    # The example we ship must itself be valid.
    result = runner.invoke(cli, ["--config", str(target), "config", "validate"])
    assert result.exit_code == 0


def test_config_init_refuses_to_clobber(tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    target.write_text("version: 1\n")
    result = CliRunner().invoke(cli, ["config", "init", "--path", str(target)])
    assert result.exit_code == 1
    assert target.read_text() == "version: 1\n"


def test_config_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "new.yaml"
    target.write_text("version: 1\n")
    result = CliRunner().invoke(cli, ["config", "init", "--path", str(target), "--force"])
    assert result.exit_code == 0
    assert len(target.read_text()) > 100


def test_invalid_config_exits_two(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("retention:\n  screenshot: 60d\n")
    result = CliRunner().invoke(cli, ["--config", str(path), "status"])
    assert result.exit_code == 2
    assert "retention.screenshot" in result.output


def test_invalid_config_error_is_json_when_asked(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("version: 99\n")
    result = CliRunner().invoke(cli, ["--json", "--config", str(path), "status"])
    assert result.exit_code == 2
    assert json.loads(result.output)["error"]


def test_device_is_not_implemented_yet(run) -> None:
    result = run("device")
    assert result.exit_code == 3, "an unimplemented command must not look like success"


def test_journal_is_empty_on_a_fresh_database(run) -> None:
    result = run("--json", "journal")
    assert result.exit_code == 0
    assert json.loads(result.output)["count"] == 0


def test_no_command_is_a_usage_error_with_help(run) -> None:
    """Click's convention: no subcommand is exit 2, and the help is printed."""
    result = run()
    assert result.exit_code == 2
    assert "Missing command" in result.output


def test_doctor_reports_and_exits_nonzero_on_failure(tmp_path: Path) -> None:
    """A setup that cannot work must not exit 0."""
    path = tmp_path / "bad.yaml"
    path.write_text(
        f'database: {{path: "{tmp_path}/db.sqlite"}}\n'
        f'photos: {{library_path: "{tmp_path}/missing.photoslibrary"}}\n'
        f'archive: {{local_path: "{tmp_path}/archive"}}\n'
    )
    result = CliRunner().invoke(cli, ["--config", str(path), "doctor", "--skip-slow"])
    assert result.exit_code == 1
    assert "NOT READY" in result.output


def test_doctor_json_is_parseable(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        f'database: {{path: "{tmp_path}/db.sqlite"}}\n'
        f'photos: {{library_path: "{tmp_path}/missing.photoslibrary"}}\n'
        f'archive: {{local_path: "{tmp_path}/archive"}}\n'
    )
    result = CliRunner().invoke(cli, ["--json", "--config", str(path), "doctor", "--skip-slow"])
    data = json.loads(result.output)
    assert data["checks"] == len(data["results"])
    assert data["ready"] is False
    assert any(r["status"] == "FAIL" and r["remedy"] for r in data["results"])


def test_doctor_states_its_own_coverage(tmp_path: Path) -> None:
    """A gate that has stopped covering anything must be visible, not reassuring."""
    path = tmp_path / "c.yaml"
    path.write_text(
        f'database: {{path: "{tmp_path}/db.sqlite"}}\n'
        f'photos: {{library_path: "{tmp_path}/missing.photoslibrary"}}\n'
        f'archive: {{local_path: "{tmp_path}/archive"}}\n'
    )
    result = CliRunner().invoke(cli, ["--config", str(path), "doctor", "--skip-slow"])
    assert "checks:" in result.output


def test_unexpected_errors_do_not_dump_a_traceback(tmp_path: Path) -> None:
    """A wall of traceback is not an error message."""
    import subprocess
    import sys

    (tmp_path / "notadb").mkdir()
    config = tmp_path / "c.yaml"
    config.write_text(
        f'database: {{path: "{tmp_path}/notadb"}}\nlogging: {{path: "{tmp_path}/logs"}}\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "iphone_image", "--config", str(config), "status"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 70
    assert "Traceback" not in result.stderr, "the raw traceback reached the user"
    assert "error:" in result.stderr
    log = tmp_path / "logs" / "iphone-image.log"
    assert log.exists() and "Traceback" in log.read_text(), "the trace was not recorded"


def test_the_error_is_stated_once(tmp_path: Path) -> None:
    import subprocess
    import sys

    (tmp_path / "notadb").mkdir()
    config = tmp_path / "c.yaml"
    config.write_text(
        f'database: {{path: "{tmp_path}/notadb"}}\nlogging: {{path: "{tmp_path}/logs"}}\n'
    )
    result = subprocess.run(
        [sys.executable, "-m", "iphone_image", "--config", str(config), "status"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
    )
    assert result.stderr.lower().count("unable to open database file") == 1

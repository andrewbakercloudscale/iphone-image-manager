from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iphone_image.config import Config
from iphone_image.db.database import Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.sqlite").connect()
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """A config that keeps every artefact inside tmp_path."""
    path = tmp_path / "config.yaml"
    path.write_text(
        "version: 1\n"
        f'archive: {{local_path: "{tmp_path}/archive"}}\n'
        f'database: {{path: "{tmp_path}/db.sqlite"}}\n'
        f'logging: {{level: info, path: "{tmp_path}/logs"}}\n'
        f'recycle_bin: {{enabled: true, path: "{tmp_path}/recycle", '
        "retention: 90d, use_macos_trash: true}\n"
    )
    return path


@pytest.fixture
def config(config_file: Path) -> Config:
    from iphone_image.config import load_config

    return load_config(config_file)

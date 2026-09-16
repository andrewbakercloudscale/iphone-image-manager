from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from iphone_image import doctor
from iphone_image.config import Config
from iphone_image.doctor import FAIL, PASS, WARN, Check, run_all

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def build_library(
    root: Path,
    *,
    assets: int = 100,
    videos: int = 10,
    resources: int = 200,
    targeting_local: int = 0,
    newest: datetime | None = None,
    per_year: dict[int, tuple[int, int]] | None = None,
    with_subtype: bool = True,
) -> Path:
    """A minimal stand-in for a Photos library, with only the columns we read."""
    library = root / "Test.photoslibrary"
    (library / "database").mkdir(parents=True)
    db = library / "database" / "Photos.sqlite"
    conn = sqlite3.connect(db)
    subtype_column = ", ZKINDSUBTYPE INTEGER" if with_subtype else ""
    conn.executescript(
        f"CREATE TABLE ZASSET (Z_PK INTEGER PRIMARY KEY, ZTRASHEDSTATE INTEGER, "
        f"ZKIND INTEGER, ZDATECREATED REAL{subtype_column});"
        "CREATE TABLE ZINTERNALRESOURCE (Z_PK INTEGER PRIMARY KEY, "
        "ZLOCALAVAILABILITYTARGET INTEGER);"
    )
    stamp = ((newest or datetime.now(UTC)) - APPLE_EPOCH).total_seconds()
    for i in range(assets):
        conn.execute(
            "INSERT INTO ZASSET (ZTRASHEDSTATE, ZKIND, ZDATECREATED) VALUES (0, ?, ?)",
            (1 if i < videos else 0, stamp),
        )

    # per_year maps a year to (assets, screenshots), for the composition check.
    for year, (count, shots) in (per_year or {}).items():
        at = (datetime(year, 6, 1, tzinfo=UTC) - APPLE_EPOCH).total_seconds()
        for i in range(count):
            if with_subtype:
                conn.execute(
                    "INSERT INTO ZASSET (ZTRASHEDSTATE, ZKIND, ZDATECREATED, ZKINDSUBTYPE) "
                    "VALUES (0, 0, ?, ?)",
                    (at, 10 if i < shots else 0),
                )
            else:
                conn.execute(
                    "INSERT INTO ZASSET (ZTRASHEDSTATE, ZKIND, ZDATECREATED) VALUES (0, 0, ?)",
                    (at,),
                )
    for i in range(resources):
        conn.execute(
            "INSERT INTO ZINTERNALRESOURCE (ZLOCALAVAILABILITYTARGET) VALUES (?)",
            (1 if i < targeting_local else 0,),
        )
    conn.commit()
    conn.close()
    return library


def config_for(library: Path, tmp_path: Path, **kwargs) -> Config:
    return Config(
        photos={"library_path": str(library), **kwargs},
        archive={"local_path": str(tmp_path / "archive")},
        database={"path": str(tmp_path / "db.sqlite")},
    )


# -- the setting that costs the most to get wrong ---------------------------


def test_optimise_mode_is_recognised(tmp_path: Path) -> None:
    library = build_library(tmp_path, resources=200, targeting_local=0)
    check = doctor.check_icloud_mode(config_for(library, tmp_path))
    assert check.status == PASS
    assert "Optimise" in check.detail


def test_download_originals_is_a_failure(tmp_path: Path) -> None:
    """The expensive mistake: it would pull the whole library onto the Mac."""
    library = build_library(tmp_path, resources=200, targeting_local=200)
    check = doctor.check_icloud_mode(config_for(library, tmp_path))
    assert check.status == FAIL
    assert "Optimise Mac Storage" in check.remedy


def test_unknown_schema_fails_rather_than_guessing(tmp_path: Path) -> None:
    library = tmp_path / "Odd.photoslibrary"
    (library / "database").mkdir(parents=True)
    conn = sqlite3.connect(library / "database" / "Photos.sqlite")
    conn.execute("CREATE TABLE ZSOMETHINGELSE (x INTEGER)")
    conn.commit()
    conn.close()
    check = doctor.check_icloud_mode(config_for(library, tmp_path))
    assert check.status == FAIL


# -- sync state --------------------------------------------------------------


def test_zero_videos_is_flagged(tmp_path: Path) -> None:
    library = build_library(tmp_path, assets=100, videos=0)
    check = doctor.check_sync(config_for(library, tmp_path))
    assert check.status == WARN
    assert "video" in check.remedy.lower()


def test_stale_library_is_flagged(tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    library = build_library(tmp_path, assets=100, videos=10, newest=old)
    check = doctor.check_sync(config_for(library, tmp_path))
    assert check.status == WARN
    assert "detached" in check.remedy


def test_incomplete_sync_against_expected_count(tmp_path: Path) -> None:
    library = build_library(tmp_path, assets=100, videos=10)
    check = doctor.check_sync(config_for(library, tmp_path, expected_assets=1000))
    assert check.status == WARN
    assert "Still syncing" in check.remedy


def test_a_healthy_library_passes(tmp_path: Path) -> None:
    library = build_library(tmp_path, assets=1000, videos=100)
    check = doctor.check_sync(config_for(library, tmp_path, expected_assets=1000))
    assert check.status == PASS


def test_empty_library_fails(tmp_path: Path) -> None:
    library = build_library(tmp_path, assets=0, videos=0, resources=0)
    check = doctor.check_sync(config_for(library, tmp_path))
    assert check.status == FAIL


# -- everything else ---------------------------------------------------------


def test_missing_library_fails_with_a_remedy(tmp_path: Path) -> None:
    config = config_for(tmp_path / "nope.photoslibrary", tmp_path)
    check = doctor.check_library(config)
    assert check.status == FAIL
    assert check.remedy


def test_archive_path_must_be_writable(tmp_path: Path) -> None:
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        config = Config(archive={"local_path": str(blocked / "archive")})
        assert doctor.check_archive_path(config).status == FAIL
    finally:
        blocked.chmod(0o700)


def test_disk_check_reports_the_volume(tmp_path: Path) -> None:
    check = doctor.check_disk(Config(archive={"local_path": str(tmp_path / "a")}))
    assert check.status in (PASS, FAIL)
    assert "free" in check.detail


def test_cloud_check_is_skipped_when_cloud_is_off() -> None:
    assert doctor.check_cloud(Config()).status == PASS


# -- the harness itself ------------------------------------------------------


def test_run_all_returns_one_result_per_check(tmp_path: Path) -> None:
    library = build_library(tmp_path)
    results = run_all(config_for(library, tmp_path), skip_slow=True)
    assert len(results) == len(doctor.CHECKS) - 1
    assert all(isinstance(c, Check) for c in results)


def test_a_crashing_check_does_not_take_the_run_down(tmp_path: Path, monkeypatch) -> None:
    """A broken check must report FAIL, never hide the other results."""

    def exploding(_config: Config) -> Check:
        raise RuntimeError("boom")

    monkeypatch.setattr(doctor, "CHECKS", [doctor.check_platform, exploding])
    results = run_all(Config())
    assert len(results) == 2
    assert results[1].status == FAIL
    assert "boom" in results[1].detail


def test_every_failure_carries_a_remedy(tmp_path: Path) -> None:
    """A check that says no without saying what to do is not worth having."""
    library = build_library(tmp_path, assets=100, videos=0, resources=200, targeting_local=200)
    for check in run_all(config_for(library, tmp_path), skip_slow=True):
        if check.status in (FAIL, WARN):
            assert check.remedy, f"{check.name} reported {check.status} with no remedy"


# ---------------------------------------------------------------------------
# A library can be 84% complete and look perfectly healthy
# ---------------------------------------------------------------------------

HEALTHY_YEARS = dict.fromkeys((2019, 2020, 2021, 2022, 2023, 2024), (2000, 200))
GAPPED_YEARS = {
    2019: (2000, 0),  # the phone was in use; no screenshots ever arrived
    2020: (2000, 0),
    2021: (2000, 1),  # one, which is the same thing as none
    2024: (2000, 200),  # and here they are, proving the library can hold them
    2025: (2000, 200),
}


def test_a_library_missing_whole_years_of_screenshots_is_not_ok(tmp_path: Path) -> None:
    """The real failure: 7,684 of the phone's 14,605, every gap before 2024.

    The total looked plausible -- 84% of the assets -- so only the composition
    could show it. doctor called this [ok] for the length of the project.
    """
    library = build_library(tmp_path, assets=20, videos=10, per_year=GAPPED_YEARS)
    check = doctor.check_sync(config_for(library, tmp_path))
    assert check.status == WARN
    assert "2019" in check.detail and "2020" in check.detail and "2021" in check.detail
    assert "2024" not in check.detail, "the years that did arrive are not the problem"


def test_a_complete_library_does_not_trip_the_composition_check(tmp_path: Path) -> None:
    library = build_library(tmp_path, assets=20, videos=10, per_year=HEALTHY_YEARS)
    check = doctor.check_sync(config_for(library, tmp_path, expected_assets=12_000))
    assert check.status == PASS


def test_someone_who_never_screenshots_is_not_a_broken_sync(tmp_path: Path) -> None:
    """No year is screenshot-rich, so there is no baseline to judge against."""
    library = build_library(
        tmp_path, assets=20, videos=10, per_year=dict.fromkeys((2019, 2020, 2021), (2000, 0))
    )
    assert doctor._screenshot_gap(library / "database" / "Photos.sqlite") == []


def test_a_schema_without_the_subtype_column_claims_nothing(tmp_path: Path) -> None:
    """An unreadable schema is not evidence of a complete library."""
    library = build_library(tmp_path, assets=100, videos=10, with_subtype=False)
    assert doctor._screenshot_gap(library / "database" / "Photos.sqlite") is None


def test_completeness_is_never_reported_ok_when_it_was_not_checked(tmp_path: Path) -> None:
    """Absence of a reference is not a pass.

    photos.expected_assets was unset, so the comparison was skipped and the
    check fell through to PASS on a library holding 84% of the phone.
    """
    library = build_library(tmp_path, assets=20, videos=10, per_year=HEALTHY_YEARS)
    check = doctor.check_sync(config_for(library, tmp_path))
    assert check.status == WARN
    assert "completeness not verified" in check.detail
    assert "expected_assets" in check.remedy


def test_a_verified_complete_library_still_passes(tmp_path: Path) -> None:
    """The check must stay usable, not just cautious."""
    library = build_library(tmp_path, assets=20, videos=10, per_year=HEALTHY_YEARS)
    check = doctor.check_sync(config_for(library, tmp_path, expected_assets=12_000))
    assert check.status == PASS

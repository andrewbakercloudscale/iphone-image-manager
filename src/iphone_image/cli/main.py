"""The iphone-image command line.

Safety shape, from docs/SPEC.md section 24: a command without --apply previews,
a command with --apply executes. Nothing in this build can execute anything
destructive yet; the device layer arrives in P2.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from .. import __version__
from ..config import (
    DEFAULT_CONFIG_PATH,
    Config,
    ConfigError,
    RemovalPolicy,
    load_config,
)
from ..db.database import Database, DatabaseError
from ..doctor import FAIL, PASS, WARN, run_all
from ..journal import Journal
from ..logs import get_logger, setup_logging
from ..output import Output, human_bytes
from ..photos.helper import HelperError
from ..retention import format_duration
from ..scanner import scan as run_scan
from ..selector import SelectorError, parse_size, plan_chunk
from .selectors import build_selector, selector_options

EXAMPLE_CONFIG = """\
# iPhone Image Manager configuration.
# Every setting here is shown at its default. Delete what you do not change.
version: 1

archive:
  local_path: "~/Pictures/iPhoneArchive"

organization:
  mode: date                  # date | location | custom
  pattern: "{year}/{month}"

geolocation:
  enabled: true
  reverse_geocode: false      # off by default, it is the only lookup that leaves your Mac

retention:
  screenshots: never          # 7d, 30d, 60d, 90d, 180d, 365d, never
  whatsapp: never

deduplication:
  exact:
    enabled: true
    algorithm: sha256
  near_duplicates:
    enabled: false            # not implemented, see docs/SPEC.md section 14

cloud:
  enabled: false
  provider: none              # google_drive
  remote: ""                  # an rclone remote name; credentials stay in your rclone config
  destination: "iPhone Archive"

remove_from_iphone:
  policy: never               # never | local_verified | cloud_verified
  include_new_campaign_assets: false
  default_batch_limit: 50

safety:
  require_final_scan: true            # cannot be disabled
  require_local_verification: true
  require_cloud_verification: true
  block_suspected_proxies: true       # cannot be disabled, see docs/SAFETY.md
  confirm_phrase_when_icloud_sync: true

recycle_bin:
  enabled: true
  path: "~/.iphone-image/recycle-bin"
  retention: 90d
  use_macos_trash: true

database:
  path: "~/.iphone-image/iphone-image.sqlite"

logging:
  level: info                 # debug | info | warning | error
  path: "~/.iphone-image/logs"

performance:
  local_transfer_workers: 2   # device stability beats throughput
  hash_workers: 4
  cloud_upload_workers: 4
"""


@dataclass
class Context:
    config: Config
    out: Output
    config_path: Path | None
    log_path: Path | None = None

    def database(self, *, create: bool = True) -> Database:
        db = Database(self.config.database.path).connect(create=create)
        db.migrate()
        return db


pass_context = click.make_pass_decorator(Context)


def _fail(out: Output, message: str, code: int = 1) -> None:
    out.error(message)
    sys.exit(code)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    help=f"Configuration file. Default: {DEFAULT_CONFIG_PATH}",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("-v", "--verbose", is_flag=True, help="Also log debug detail to the console.")
@click.version_option(__version__, "-V", "--version", prog_name="iphone-image")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, as_json: bool, verbose: bool) -> None:
    """Inventory, back up, verify and safely offload iPhone media.

    The default behaviour is always to keep media on the iPhone.
    """
    out = Output(as_json=as_json)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        _fail(out, str(exc), code=2)
        return

    log_path = setup_logging(config, command=ctx.invoked_subcommand or "-", verbose=verbose)
    get_logger().info("start: %s", " ".join(sys.argv[1:]) or "(no arguments)")

    ctx.obj = Context(config=config, out=out, config_path=config.source_path, log_path=log_path)


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


@cli.group()
def config() -> None:
    """Inspect and check configuration."""


@config.command("show")
@pass_context
def config_show(ctx: Context) -> None:
    """Print the configuration actually in effect."""
    data = ctx.config.model_dump(mode="json")
    data["source"] = str(ctx.config_path) if ctx.config_path else "built-in defaults"

    def render() -> None:
        ctx.out.title("Configuration")
        ctx.out.pairs([("source", data["source"])])
        for section, values in data.items():
            if not isinstance(values, dict):
                continue
            ctx.out.line()
            ctx.out.line(f"  [{section}]", style="head")
            ctx.out.pairs([(f"  {k}", v) for k, v in values.items()])

    ctx.out.result(data, render)


@config.command("validate")
@pass_context
def config_validate(ctx: Context) -> None:
    """Check the configuration, and report what it means in practice."""
    notes: list[str] = []
    c = ctx.config

    if c.remove_from_iphone.policy is RemovalPolicy.NEVER:
        notes.append(
            "removal is disabled (remove_from_iphone.policy: never). Nothing can be deleted."
        )
    else:
        notes.append(
            f"removal is ARMED (policy: {c.remove_from_iphone.policy}). "
            f"Assets can be deleted once they satisfy it."
        )
    if not c.cloud.enabled:
        notes.append("cloud backup is off. Media stays on this Mac only.")
    if c.retention.screenshots is None and c.retention.whatsapp is None:
        notes.append("no retention windows are set, so no asset ages out of anything.")
    if not c.recycle_bin.enabled:
        notes.append("recycle bin is OFF. Removed assets leave no Mac-side record.")
    if not c.recycle_bin.use_macos_trash:
        notes.append("use_macos_trash is OFF. Archive files would be unlinked, not trashed.")
    if c.geolocation.reverse_geocode:
        notes.append("reverse geocoding is on.")

    retention = {
        "screenshots": format_duration(c.retention.screenshots),
        "whatsapp": format_duration(c.retention.whatsapp),
    }
    data: dict[str, Any] = {
        "valid": True,
        "source": str(ctx.config_path) if ctx.config_path else "built-in defaults",
        "archive": str(c.archive.local_path),
        "database": str(c.database.path),
        "pattern": c.organization.pattern,
        "removalPolicy": str(c.remove_from_iphone.policy),
        "retention": retention,
        "notes": notes,
    }

    def render() -> None:
        ctx.out.title("Configuration check")
        ctx.out.pairs(
            [
                ("source", data["source"]),
                ("archive", data["archive"]),
                ("database", data["database"]),
                ("pattern", data["pattern"]),
                ("removal policy", data["removalPolicy"]),
                ("screenshot retention", retention["screenshots"]),
                ("whatsapp retention", retention["whatsapp"]),
            ]
        )
        ctx.out.line()
        ctx.out.ok("configuration is valid")
        ctx.out.line()
        for note in notes:
            ctx.out.line(f"  - {note}", style="muted")

    ctx.out.result(data, render)


@config.command("init")
@click.option(
    "--path",
    type=click.Path(path_type=Path),
    default=None,
    help=f"Where to write. Default: {DEFAULT_CONFIG_PATH}",
)
@click.option("--force", is_flag=True, help="Overwrite an existing file.")
@pass_context
def config_init(ctx: Context, path: Path | None, force: bool) -> None:
    """Write a commented example configuration file."""
    target = (path or DEFAULT_CONFIG_PATH).expanduser()
    if target.exists() and not force:
        _fail(ctx.out, f"{target} already exists. Pass --force to overwrite it.")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXAMPLE_CONFIG)
    ctx.out.result({"written": str(target)}, lambda: ctx.out.ok(f"wrote {target}"))


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--limit", type=int, default=0, help="Stop after this many assets. For a quick look.")
@pass_context
def scan(ctx: Context, limit: int) -> None:
    """Inventory the Photos library. Read-only, and costs no bandwidth."""
    try:

        def tick(result: Any) -> None:
            ctx.out.line(f"  {result.seen:,} assets ...", style="muted")

        result = run_scan(ctx.config, limit=limit, progress=tick)
    except HelperError as exc:
        _fail(ctx.out, str(exc))
        return

    top = sorted(result.by_channel.items(), key=lambda kv: -kv[1])[:8]
    data = {
        "scanId": result.scan_id,
        "seen": result.seen,
        "inserted": result.inserted,
        "updated": result.updated,
        "disappeared": result.disappeared,
        "totalBytes": result.total_bytes,
        "proxySuspects": result.proxy_suspects,
        "sourceCoverage": round(result.source_coverage, 4),
        "degraded": result.degraded,
        "byChannel": result.by_channel,
    }

    def render() -> None:
        ctx.out.title("Scan")
        ctx.out.pairs(
            [
                ("assets seen", f"{result.seen:,}"),
                ("new", f"{result.inserted:,}"),
                ("updated", f"{result.updated:,}"),
                ("gone from the library", f"{result.disappeared:,}"),
                ("total size", human_bytes(result.total_bytes)),
                ("suspected iCloud proxies", f"{result.proxy_suspects:,}"),
                ("source app known for", f"{result.source_coverage * 100:.0f}% of assets"),
            ]
        )
        if result.degraded:
            ctx.out.line()
            ctx.out.warn(result.degraded)
        ctx.out.line()
        ctx.out.line("  Channels", style="head")
        for channel, count in top:
            ctx.out.line(f"    {channel:<36}{count:>9,}")

    ctx.out.result(data, render)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@cli.command("list")
@selector_options
@click.option("--budget", help="Show what a chunk of this size would take, e.g. 50GB.")
@click.option("--show", type=int, default=15, show_default=True, help="How many rows to print.")
@pass_context
def list_assets(ctx: Context, budget: str | None, show: int, **kwargs: Any) -> None:
    """Preview what a selector matches. Never changes anything."""
    try:
        selector = build_selector(**kwargs)
    except SelectorError as exc:
        _fail(ctx.out, str(exc))
        return

    db = ctx.database()
    where, params = selector.where()
    rows = db.conn.execute(
        f"SELECT a.* FROM assets a WHERE {where} ORDER BY {selector.order_by()}",
        params,
    ).fetchall()
    db.close()

    candidates = [dict(r) for r in rows]
    budget_bytes = parse_size(budget) if budget else None
    plan = plan_chunk(candidates, budget_bytes=budget_bytes, limit=selector.limit)

    total_bytes = sum(int(c.get("size_bytes") or 0) for c in candidates)
    data = {
        "selector": selector.describe(),
        "matched": len(candidates),
        "matchedBytes": total_bytes,
        "chunk": {
            "budget": budget,
            "count": plan.count,
            "bytes": plan.total_bytes,
            "remainingAssets": plan.remaining_assets,
            "remainingBytes": plan.remaining_bytes,
            "skippedTooLarge": len(plan.skipped_too_large),
        },
        "sample": [
            {
                "filename": c.get("filename"),
                "created": c.get("created_at_device"),
                "bytes": c.get("size_bytes"),
                "source": c.get("source_bundle_id"),
                "type": c.get("media_type"),
            }
            for c in plan.included[:show]
        ],
    }

    def render() -> None:
        ctx.out.title("Matching assets")
        ctx.out.pairs([("selector", selector.describe())])
        ctx.out.line()
        ctx.out.table(
            ["FILENAME", "DATE", "TYPE", "SOURCE", "SIZE"],
            [
                (
                    str(c.get("filename") or "?")[:28],
                    str(c.get("created_at_device") or "")[:10],
                    c.get("media_type") or "",
                    (c.get("source_bundle_id") or "(unattributed)").split(".")[-1],
                    human_bytes(c.get("size_bytes")),
                )
                for c in plan.included[:show]
            ],
        )
        if plan.count > show:
            ctx.out.line(f"    ... and {plan.count - show:,} more", style="muted")
        ctx.out.line()
        ctx.out.pairs(
            [
                ("matched", f"{len(candidates):,} assets, {human_bytes(total_bytes)}"),
            ]
        )
        if budget_bytes is not None:
            ctx.out.pairs(
                [
                    ("this chunk", f"{plan.count:,} assets, {human_bytes(plan.total_bytes)}"),
                    (
                        "left for later",
                        f"{plan.remaining_assets:,} assets, {human_bytes(plan.remaining_bytes)}",
                    ),
                ]
            )
            if plan.skipped_too_large:
                ctx.out.line()
                ctx.out.warn(
                    f"{len(plan.skipped_too_large)} asset(s) are larger than the whole "
                    f"budget and were not selected. Raise --budget to include them."
                )
        ctx.out.line()
        ctx.out.line("  Nothing was changed. This command only ever reads.", style="muted")

    ctx.out.result(data, render)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--skip-slow",
    is_flag=True,
    help="Skip checks that may prompt or take time, such as Photos authorisation.",
)
@pass_context
def doctor(ctx: Context, skip_slow: bool) -> None:
    """Check the machine, not just the config, and say what to change."""
    results = run_all(ctx.config, skip_slow=skip_slow)
    failures = [c for c in results if c.status == FAIL]
    warnings = [c for c in results if c.status == WARN]

    data = {
        "checks": len(results),
        "passed": sum(1 for c in results if c.status == PASS),
        "warnings": len(warnings),
        "failures": len(failures),
        "ready": not failures,
        "results": [
            {
                "name": c.name,
                "status": c.status,
                "detail": c.detail,
                "remedy": c.remedy,
                "data": c.data,
            }
            for c in results
        ],
    }

    def render() -> None:
        ctx.out.title("Setup check")
        mark = {PASS: "[ok]", WARN: "[--]", FAIL: "[XX]"}
        style = {PASS: "ok", WARN: "warn", FAIL: "bad"}
        for check in results:
            ctx.out.line(
                f"  {mark[check.status]} {check.name:<24}{check.detail}", style=style[check.status]
            )
        ctx.out.line()

        if failures or warnings:
            ctx.out.line("  What to change", style="head")
            ctx.out.line()
            for check in failures + warnings:
                if not check.remedy:
                    continue
                ctx.out.line(f"  {check.name}:", style=style[check.status])
                for line in textwrap.wrap(check.remedy, width=72):
                    ctx.out.line(f"      {line}")
                ctx.out.line()

        # State the coverage, so a check set that has stopped covering anything
        # is visible rather than reassuring.
        summary = (
            f"{data['checks']} checks: {data['passed']} passed, "
            f"{len(warnings)} warning(s), {len(failures)} failure(s)"
        )
        if failures:
            ctx.out.line(f"  NOT READY. {summary}", style="bad")
        elif warnings:
            ctx.out.line(f"  Usable, with caveats. {summary}", style="warn")
        else:
            ctx.out.ok(f"Ready. {summary}")

    ctx.out.result(data, render)
    sys.exit(1 if failures else 0)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@pass_context
def status(ctx: Context) -> None:
    """Where everything stands."""
    try:
        db = ctx.database()
    except DatabaseError as exc:
        _fail(ctx.out, str(exc))
        return

    counts = db.counts()
    journal = Journal(db)
    pending = journal.pending()

    row = db.conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS b "
        "FROM assets WHERE present_on_phone = 1"
    ).fetchone()
    verified_local = db.conn.execute(
        "SELECT COUNT(*) AS n FROM assets WHERE local_status = 'LOCAL_VERIFIED'"
    ).fetchone()["n"]
    verified_cloud = db.conn.execute(
        "SELECT COUNT(*) AS n FROM assets WHERE cloud_status = 'CLOUD_VERIFIED'"
    ).fetchone()["n"]

    data: dict[str, Any] = {
        "database": str(ctx.config.database.path),
        "schemaVersion": db.schema_version,
        "devices": counts["devices"],
        "assetsOnPhone": row["n"],
        "bytesOnPhone": row["b"],
        "localVerified": verified_local,
        "cloudVerified": verified_cloud,
        "campaigns": counts["campaigns"],
        "removalPolicy": str(ctx.config.remove_from_iphone.policy),
        "pendingOperations": len(pending),
        "tableCounts": counts,
    }
    db.close()

    def render() -> None:
        ctx.out.title("iPhone Image Manager")
        ctx.out.pairs(
            [
                ("database", data["database"]),
                ("schema version", data["schemaVersion"]),
                ("known devices", f"{data['devices']:,}"),
                ("assets on phone", f"{data['assetsOnPhone']:,}"),
                ("storage on phone", human_bytes(data["bytesOnPhone"])),
                ("local verified", f"{data['localVerified']:,}"),
                ("cloud verified", f"{data['cloudVerified']:,}"),
                ("campaigns", f"{data['campaigns']:,}"),
                ("removal policy", data["removalPolicy"]),
            ]
        )
        if data["assetsOnPhone"] == 0:
            ctx.out.line()
            ctx.out.line("  No assets known yet. Device scanning arrives in P2;", style="muted")
            ctx.out.line("  run the transport spike first: python3 spikes/run_p0.py", style="muted")
        if pending:
            ctx.out.line()
            ctx.out.warn(f"{len(pending)} operation(s) started and never finished:")
            for op in pending[:5]:
                ctx.out.line(f"    {op['started_at']}  {op['operation']}", style="warn")

    ctx.out.result(data, render)


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


@cli.command("journal")
@click.option("--limit", default=20, show_default=True, help="How many entries to show.")
@click.option("--pending", "only_pending", is_flag=True, help="Only unfinished operations.")
@pass_context
def journal_cmd(ctx: Context, limit: int, only_pending: bool) -> None:
    """Recent operations, and anything left in flight by a crash."""
    db = ctx.database()
    journal = Journal(db)
    entries = journal.pending() if only_pending else journal.recent(limit)
    db.close()

    data = {"count": len(entries), "entries": entries}

    def render() -> None:
        ctx.out.title("Operations journal")
        ctx.out.table(
            ["STARTED", "OPERATION", "STATUS", "MS", "DETAIL"],
            [
                (
                    e["started_at"],
                    e["operation"],
                    e["status"],
                    e["duration_ms"] if e["duration_ms"] is not None else "-",
                    (e["error"] or e["detail"] or "")[:60],
                )
                for e in entries
            ],
        )

    ctx.out.result(data, render)


# ---------------------------------------------------------------------------
# device (P2)
# ---------------------------------------------------------------------------


@cli.command()
@pass_context
def device(ctx: Context) -> None:
    """Show the connected iPhone. Not implemented until P2."""
    message = (
        "Device support arrives in P2. The transport spike answers the questions "
        "it depends on first: python3 spikes/run_p0.py"
    )
    ctx.out.result(
        {"implemented": False, "message": message},
        lambda: ctx.out.line(f"  {message}", style="muted"),
    )
    sys.exit(3)


def main() -> None:
    """Entry point with a last-resort handler.

    Click reports usage errors itself. This catches everything else, so an
    unexpected failure lands in the log with a stack trace and the user gets a
    sentence plus the path to look at, rather than a wall of traceback.
    """
    try:
        cli.main(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        sys.exit(exc.exit_code)
    except click.Abort:
        click.echo("Aborted.", err=True)
        sys.exit(130)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        click.echo(
            "\nInterrupted. Every operation is journalled before it runs, "
            "so nothing was left half-written.",
            err=True,
        )
        sys.exit(130)
    except Exception as exc:  # last resort, deliberately broad
        logger = get_logger()
        logger.exception("unhandled error: %s", exc, extra={"file_only": True})
        click.echo(f"error: {type(exc).__name__}: {exc}", err=True)
        for handler in logger.handlers:
            target = getattr(handler, "baseFilename", None)
            if target:
                click.echo(f"A stack trace was written to {target}", err=True)
                break
        else:
            click.echo("No log file was available, so nothing was recorded.", err=True)
        sys.exit(70)

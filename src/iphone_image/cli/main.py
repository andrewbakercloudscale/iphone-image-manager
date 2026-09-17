"""The iphone-image command line.

Safety shape, from docs/SPEC.md section 24: a command without --apply previews,
a command with --apply executes. Nothing in this build can execute anything
destructive yet; removal arrives in P10, gated on its own test harness.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import click

from .. import __version__
from .. import cloud as cloud_engine
from .. import release as release_engine
from .. import relocate as relocate_engine
from .. import remove as remove_engine
from .. import sync as sync_engine
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
from ..selector import PROXY_SUSPICION_BLOCK, SelectorError, parse_size, plan_chunk
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
  # Tokens: {source} {year} {month} {day} {type} {device}
  #         {country} {region} {city} {suburb} {camera_make} {camera_model}
  # {source} is the channel as --source names it: camera, whatsapp, screenshot.
  # Changing this only changes where new assets land. Run `iphone-image relocate`
  # to move what is already archived.

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

chunking:
  enabled: true
  chunk_bytes: 15GB           # roughly one overnight run at the measured fetch rate
  free_space_floor: 20GB      # refuse to start a chunk that would breach this
  default_types: [photo]      # video must be asked for by name: it is most of the bytes
  within_type: oldest_first   # oldest is least replaceable if you ever stop half way

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
  cloud_upload_workers: 16    # measured: 4 gave 0.33 MB/s to Drive, 16 gave 2.55
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
# sync
# ---------------------------------------------------------------------------


@cli.command()
@selector_options
@click.option("--budget", help="Override chunking.chunk_bytes for this run, e.g. 15GB.")
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    help="Actually fetch. Without this, sync only shows the plan.",
)
@pass_context
def sync(ctx: Context, budget: str | None, apply_: bool, **kwargs: Any) -> None:
    """Fetch one chunk of originals into the archive.

    Without --apply this prints the plan and fetches nothing.

    Video is never fetched unless you ask for it with --type video. It is the
    bulk of most libraries and is deliberately excluded from the default.
    """
    config = ctx.config
    try:
        selector = build_selector(**kwargs)
    except SelectorError as exc:
        _fail(ctx.out, str(exc))
        return

    # No --type means the configured default, which excludes video on purpose.
    types = [selector.media_type] if selector.media_type else config.chunking.default_types
    parsed_budget = parse_size(budget) if budget else None
    budget_bytes: int = parsed_budget if parsed_budget is not None else config.chunking.chunk_bytes

    plans: list[tuple[str, Any]] = []
    remaining_budget = budget_bytes
    for media_type in types:
        scoped = replace(selector, media_type=media_type)
        chunk, _ = sync_engine.plan(config, scoped, budget_bytes=remaining_budget)
        plans.append((media_type, chunk))
        remaining_budget -= chunk.total_bytes
        if remaining_budget <= 0:
            break

    planned = sum(c.count for _, c in plans)
    planned_bytes = sum(c.total_bytes for _, c in plans)
    # Named, not merely counted: docs/SAFETY.md section 2 requires suspected
    # proxies to be surfaced wherever they are acted on. They are backed up
    # like anything else; the block on them applies to removal.
    proxies = sum(
        1
        for _, chunk in plans
        for asset in chunk.included
        if float(asset.get("proxy_suspicion") or 0) >= PROXY_SUSPICION_BLOCK
    )
    left_assets = sum(c.remaining_assets for _, c in plans)
    left_bytes = sum(c.remaining_bytes for _, c in plans)
    probe = ctx.database()
    rate = sync_engine.observed_rate(probe)
    probe.close()
    hours = sync_engine.estimate_hours(planned_bytes, rate) if planned_bytes else 0.0
    rate_note = (
        f"{rate:.2f} MB/s, measured here"
        if rate
        else f"{sync_engine.FALLBACK_MB_S:.2f} MB/s, assumed until "
        "this install has fetched something"
    )

    data: dict[str, Any] = {
        "applied": apply_,
        "types": types,
        "budget": budget_bytes,
        "planned": planned,
        "plannedBytes": planned_bytes,
        "remainingAssets": left_assets,
        "remainingBytes": left_bytes,
        "estimatedHours": round(hours, 1),
        "suspectedProxies": proxies,
        "rateMbPerSecond": round(rate, 2) if rate else None,
        "rateIsMeasured": rate is not None,
    }

    if not apply_:

        def render_plan() -> None:
            ctx.out.title("Sync plan")
            ctx.out.pairs(
                [
                    ("selector", selector.describe()),
                    ("types", ", ".join(types)),
                    ("budget", human_bytes(budget_bytes)),
                ]
            )
            ctx.out.line()
            for media_type, chunk in plans:
                ctx.out.line(
                    f"    {media_type:<10}{chunk.count:>8,} assets  "
                    f"{human_bytes(chunk.total_bytes):>12}"
                )
            ctx.out.line()
            ctx.out.pairs(
                [
                    ("this chunk", f"{planned:,} assets, {human_bytes(planned_bytes)}"),
                    ("left for later", f"{left_assets:,} assets, {human_bytes(left_bytes)}"),
                    ("rate", rate_note),
                    ("estimated time", f"{hours:.1f} hours"),
                ]
            )
            ctx.out.line()
            if proxies:
                ctx.out.line(
                    f"  {proxies:,} of these look like iCloud proxies rather than "
                    f"originals. They are backed up anyway, and can never be removed "
                    f"from the phone.",
                    style="muted",
                )
            if "video" not in types:
                ctx.out.line("  Video is excluded. Ask for it with --type video.", style="muted")
            ctx.out.line("  NOTHING HAS BEEN FETCHED. Add --apply to run it.", style="warn")

        ctx.out.result(data, render_plan)
        return

    # -- apply -------------------------------------------------------------

    total = sync_engine.SyncResult()
    remaining_budget = budget_bytes
    for media_type in types:
        if remaining_budget <= 0:
            break
        scoped = replace(selector, media_type=media_type)

        def tick(asset: dict[str, Any], result: Any) -> None:
            done = result.fetched + result.failed
            ctx.out.line(
                f"  [{done:>5,}] {str(asset.get('filename') or '?')[:34]:<36}"
                f"{human_bytes(result.bytes_fetched):>11}  {result.rate_text}",
                style="muted",
            )

        try:
            outcome = sync_engine.run(config, scoped, budget_bytes=remaining_budget, on_asset=tick)
        except (sync_engine.SyncError, HelperError) as exc:
            _fail(ctx.out, str(exc))
            return

        remaining_budget -= outcome.bytes_fetched
        total.planned += outcome.planned
        total.fetched += outcome.fetched
        total.failed += outcome.failed
        total.skipped_existing += outcome.skipped_existing
        total.missing_recovered += outcome.missing_recovered
        total.partials_swept += outcome.partials_swept
        total.bytes_fetched += outcome.bytes_fetched
        total.seconds += outcome.seconds
        total.network_bytes += outcome.network_bytes
        total.network_seconds += outcome.network_seconds
        total.local_bytes += outcome.local_bytes
        total.local_count += outcome.local_count
        total.remaining_assets += outcome.remaining_assets
        total.remaining_bytes += outcome.remaining_bytes
        total.failures.extend(outcome.failures)
        for key, value in outcome.by_type.items():
            total.by_type[key] = total.by_type.get(key, 0) + value

        if outcome.stopped_early:
            # Whatever ended one type ends the run. Moving on to video after an
            # outage stopped the photos would just fail 10 more times.
            total.stopped_early = outcome.stopped_early
            break

    data.update(
        {
            "fetched": total.fetched,
            "failed": total.failed,
            "skippedExisting": total.skipped_existing,
            "bytesFetched": total.bytes_fetched,
            # The download rate, from downloads alone. A run that was entirely
            # local reports null here rather than a disk read in disguise.
            "mbPerSecond": round(total.rate_mb_s, 2) or None,
            "networkBytes": total.network_bytes,
            "networkSeconds": round(total.network_seconds, 3),
            "alreadyLocal": total.local_count,
            "remainingAssets": total.remaining_assets,
            "remainingBytes": total.remaining_bytes,
            "stoppedEarly": total.stopped_early,
            "failures": total.failures[:20],
        }
    )

    def render_result() -> None:
        ctx.out.title("Sync")
        ctx.out.pairs(
            [
                ("fetched", f"{total.fetched:,} assets, {human_bytes(total.bytes_fetched)}"),
                ("already present", f"{total.skipped_existing:,}"),
                ("re-fetched (was missing)", f"{total.missing_recovered:,}"),
                ("leftover partials swept", f"{total.partials_swept:,}"),
                ("failed", f"{total.failed:,}"),
                ("rate", total.rate_text),
                (
                    "left for later",
                    f"{total.remaining_assets:,} assets, {human_bytes(total.remaining_bytes)}",
                ),
                ("archive", str(config.archive.local_path)),
            ]
        )
        if total.failures:
            ctx.out.line()
            ctx.out.line("  Failures", style="head")
            for failure in total.failures[:10]:
                ctx.out.line(
                    f"    {str(failure.get('filename'))[:30]:<32}{failure.get('error')}",
                    style="warn",
                )
            if len(total.failures) > 10:
                ctx.out.line(f"    ... and {len(total.failures) - 10:,} more", style="warn")
        if total.stopped_early:
            ctx.out.line()
            ctx.out.warn(total.stopped_early)
        ctx.out.line()
        ctx.out.line(
            "  Nothing was removed from the library. sync only ever copies.", style="muted"
        )

    ctx.out.result(data, render_result)
    if total.failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# relocate
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    help="Actually move the files. Without this, relocate only shows the plan.",
)
@click.option("--show", type=int, default=10, show_default=True, help="How many moves to print.")
@pass_context
def relocate(ctx: Context, apply_: bool, show: int) -> None:
    """Re-file the archive to match the configured root and pattern.

    Change `archive.local_path` or `organization.pattern` first, then run this:
    it moves what is already archived to where the tool would put it now, and
    rewrites the ledger to match.

    Files are renamed, never copied and deleted, so this is instant on one
    volume and refused across two. Interrupting is safe: the ledger is updated
    per file, so re-running finishes the job.
    """
    config = ctx.config
    db = ctx.database()
    try:
        moves, preview = relocate_engine.plan(config, db)
    finally:
        db.close()

    data: dict[str, Any] = {
        "applied": apply_,
        "root": str(config.archive.local_path),
        "pattern": config.organization.pattern,
        "examined": preview.examined,
        "alreadyInPlace": preview.already_in_place,
        "planned": preview.planned,
        "missing": preview.missing,
        "bytes": sum(m.size_bytes for m in moves),
        "sample": [{"from": str(m.source), "to": str(m.destination)} for m in moves[:show]],
    }

    def render_common() -> None:
        ctx.out.pairs(
            [
                ("archive root", str(config.archive.local_path)),
                ("pattern", config.organization.pattern),
            ]
        )
        ctx.out.line()
        for move in moves[:show]:
            ctx.out.line(f"    {move.source}", style="muted")
            ctx.out.line(f"      -> {move.destination}")
        if preview.planned > show:
            ctx.out.line(f"    ... and {preview.planned - show:,} more", style="muted")

    if not apply_:

        def render_plan() -> None:
            ctx.out.title("Relocation plan")
            render_common()
            ctx.out.line()
            ctx.out.pairs(
                [
                    ("archived files", f"{preview.examined:,}"),
                    ("already in place", f"{preview.already_in_place:,}"),
                    ("to move", f"{preview.planned:,}, {human_bytes(data['bytes'])}"),
                    ("recorded but not on disk", f"{preview.missing:,}"),
                ]
            )
            ctx.out.line()
            if preview.missing:
                ctx.out.warn(
                    f"{preview.missing:,} asset(s) are recorded as archived but their file "
                    f"is gone. relocate leaves them alone; the next sync fetches them again."
                )
            if not preview.planned:
                ctx.out.ok("nothing to move, the archive already matches the configuration")
                return
            ctx.out.line("  NOTHING HAS MOVED. Add --apply to run it.", style="warn")

        ctx.out.result(data, render_plan)
        return

    result = relocate_engine.run(config)
    data.update(
        {
            "moved": result.moved,
            "failed": result.failed,
            "missing": result.missing,
            "bytesMoved": result.bytes_moved,
            "directoriesRemoved": result.directories_removed,
            "failures": result.failures[:20],
        }
    )

    def render_result() -> None:
        ctx.out.title("Relocation")
        render_common()
        ctx.out.line()
        ctx.out.pairs(
            [
                ("moved", f"{result.moved:,} files, {human_bytes(result.bytes_moved)}"),
                ("already in place", f"{result.already_in_place:,}"),
                ("recorded but not on disk", f"{result.missing:,}"),
                ("failed", f"{result.failed:,}"),
                ("empty folders removed", f"{result.directories_removed:,}"),
            ]
        )
        if result.failures:
            ctx.out.line()
            ctx.out.line("  Failures", style="head")
            for failure in result.failures[:10]:
                ctx.out.line(f"    {failure['file']}", style="warn")
                ctx.out.line(f"      {failure['error']}", style="warn")
        ctx.out.line()
        ctx.out.line("  Files were renamed, not copied. Nothing was deleted.", style="muted")

    ctx.out.result(data, render_result)
    if result.failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# cloud
# ---------------------------------------------------------------------------


@cli.command()
@selector_options
@click.option("--apply", "apply_", is_flag=True, help="Actually upload. Without it, plan only.")
@pass_context
def cloud(ctx: Context, apply_: bool, **kwargs: Any) -> None:
    """Mirror verified archive files to cloud storage.

    Uploads, then asks the remote what it holds and compares SHA256 against the
    ledger. Only a match marks an asset verified, because the local copy is
    released on the strength of that record.
    """
    config = ctx.config
    try:
        selector = build_selector(**kwargs)
    except SelectorError as exc:
        _fail(ctx.out, str(exc))
        return

    db = ctx.database()
    try:
        uploads = cloud_engine.plan(config, selector, db)
    finally:
        db.close()

    total_bytes = sum(u.size_bytes for u in uploads)
    data: dict[str, Any] = {
        "applied": apply_,
        "remote": config.cloud.remote,
        "destination": config.cloud.destination,
        "planned": len(uploads),
        "plannedBytes": total_bytes,
    }

    if not apply_:

        def render_plan() -> None:
            ctx.out.title("Cloud plan")
            ctx.out.pairs(
                [
                    ("selector", selector.describe()),
                    ("remote", f"{config.cloud.remote}:{config.cloud.destination}"),
                    ("to upload", f"{len(uploads):,} files, {human_bytes(total_bytes)}"),
                ]
            )
            ctx.out.line()
            for upload in uploads[:8]:
                ctx.out.line(f"    {upload.relative}", style="muted")
            if len(uploads) > 8:
                ctx.out.line(f"    ... and {len(uploads) - 8:,} more", style="muted")
            ctx.out.line()
            if not uploads:
                ctx.out.ok("everything selected is already verified in the cloud")
                return
            ctx.out.line("  NOTHING HAS BEEN UPLOADED. Add --apply to run it.", style="warn")

        ctx.out.result(data, render_plan)
        return

    def tick(channel: str, count: int) -> None:
        ctx.out.line(f"  uploading {count:,} file(s) from {channel} ...", style="muted")

    def progress(line: str) -> None:
        ctx.out.line(f"  {line}", style="muted")

    try:
        result = cloud_engine.run(config, selector, on_channel=tick, on_progress=progress)
    except cloud_engine.CloudError as exc:
        _fail(ctx.out, str(exc))
        return

    data.update(
        {
            "uploaded": result.uploaded,
            "verified": result.verified,
            "failed": result.failed,
            "bytesUploaded": result.bytes_uploaded,
            "mbPerSecond": round(result.rate_mb_s, 2) or None,
            "batches": result.batches,
            "batchesDone": result.batches_done,
            "stoppedEarly": result.stopped_early or None,
            "failures": result.failures[:20],
        }
    )

    def render_result() -> None:
        ctx.out.title("Cloud")
        ctx.out.pairs(
            [
                ("destination", f"{config.cloud.remote}:{config.cloud.destination}"),
                ("uploaded", f"{result.uploaded:,} files, {human_bytes(result.bytes_uploaded)}"),
                ("verified by hash", f"{result.verified:,}"),
                ("folders banked", f"{result.batches_done:,} of {result.batches:,}"),
                ("failed", f"{result.failed:,}"),
                ("rate", f"{result.rate_mb_s:.2f} MB/s" if result.seconds else "-"),
            ]
        )
        if result.stopped_early:
            ctx.out.line()
            ctx.out.line(f"  {result.stopped_early}", style="warn")
        if result.failures:
            ctx.out.line()
            ctx.out.line("  Failures", style="head")
            for failure in result.failures[:10]:
                ctx.out.line(f"    {failure['file']}", style="warn")
                ctx.out.line(f"      {failure['error']}", style="warn")
        ctx.out.line()
        ctx.out.line(
            "  Nothing local was deleted. Releasing the Mac copy is a separate step.",
            style="muted",
        )
        if result.batches_done < result.batches:
            ctx.out.line(
                f"  {result.verified:,} asset(s) are verified and recorded. Re-run to "
                f"continue; what is banked is not sent again.",
                style="muted",
            )

    ctx.out.result(data, render_result)
    if result.failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# remove-from-iphone
# ---------------------------------------------------------------------------


def _removal_phrase(count: int) -> str:
    """The phrase the user must type, tied to this plan's size.

    `docs/SAFETY.md` section 3: an explicit typed confirmation while iCloud
    sync is on, and "a `--yes` style flag is not sufficient". Binding the
    phrase to the count means a command line copied from an earlier, smaller
    run does not authorise a larger one.
    """
    return f"remove {count} photos from my iphone"


@cli.command(name="remove-from-iphone")
@selector_options
@click.option("--apply", "apply_", is_flag=True, help="Actually remove. Without it, plan only.")
@click.option("--confirm", default="", help="The typed confirmation phrase. Required with --apply.")
@click.pass_obj
def remove_from_iphone(ctx: Context, apply_: bool, confirm: str, **kwargs: Any) -> None:
    """Delete assets from the iPhone, once every safety condition is proved.

    A scan is taken in this same run and the plan is computed against it. The
    local copy is re-hashed, the remote is asked what it holds now, and an
    asset whose group is incomplete -- a Live Photo whose motion half was
    never archived -- is never removable.

    Assets go to Recently Deleted on the device and stay restorable there for
    30 days. Storage is not reclaimed until that empties.
    """
    config = ctx.config
    try:
        selector = build_selector(**kwargs)
    except SelectorError as exc:
        _fail(ctx.out, str(exc))
        return

    # Before the scan, not after: scanning takes minutes and writes to the
    # ledger, and refusing afterwards would spend both for nothing.
    if config.remove_from_iphone.policy == RemovalPolicy.NEVER:
        _fail(
            ctx.out,
            "remove_from_iphone.policy is 'never', so nothing is removed from the phone.\n"
            "  That is the default and it is doing its job. To change it, edit\n"
            "  the policy in your config to 'cloud_verified' -- which requires a\n"
            "  hash-verified copy in the cloud before anything leaves the device.",
        )
        return

    ctx.out.line()
    ctx.out.line(
        "  This deletes photographs from your iPhone. Because the library is "
        "iCloud-synced, they leave every device signed in to that account.",
        style="warn",
    )
    ctx.out.line(
        "  They go to Recently Deleted and are restorable there for 30 days.",
        style="muted",
    )
    ctx.out.line()
    ctx.out.line(
        "  Scanning the library first. Nothing is planned against stale state.", style="muted"
    )

    db = ctx.database()
    try:
        scan_result = run_scan(config)
        eligible, preview = remove_engine.plan(config, db, selector, scan_id=scan_result.scan_id)
    except (remove_engine.RemoveError, cloud_engine.CloudError, HelperError) as exc:
        _fail(ctx.out, str(exc))
        return
    finally:
        db.close()

    removable = sum(c.size_bytes for c in eligible)
    data: dict[str, Any] = {
        "applied": apply_,
        "scanId": preview.scan_id,
        "examined": preview.examined,
        "eligible": preview.eligible,
        "eligibleBytes": removable,
        "blocked": preview.blocked,
        "blockedReasons": preview.blocks,
        "blockedAssets": preview.blocked_assets[:200],
    }

    def render_blocks() -> None:
        if not preview.blocks:
            return
        ctx.out.line()
        ctx.out.line("  Not removable, and why", style="head")
        for reason, count in sorted(preview.blocks.items(), key=lambda kv: -kv[1]):
            ctx.out.line(f"    {count:>7,}  {reason}", style="muted")

    if not apply_:

        def render_plan() -> None:
            ctx.out.title("Removal plan")
            ctx.out.pairs(
                [
                    ("scan taken just now", f"#{preview.scan_id}"),
                    ("assets examined", f"{preview.examined:,}"),
                    ("safe to remove", f"{preview.eligible:,}, {human_bytes(removable)}"),
                    ("blocked", f"{preview.blocked:,}"),
                ]
            )
            render_blocks()
            ctx.out.line()
            if not eligible:
                ctx.out.ok("nothing is safe to remove yet")
                return
            ctx.out.line(
                "  Storage is reclaimed after Recently Deleted is emptied, up to 30 days.",
                style="muted",
            )
            ctx.out.line("  NOTHING HAS BEEN REMOVED. To run it:", style="warn")
            ctx.out.line(
                f'    remove-from-iphone --apply --confirm "{_removal_phrase(preview.eligible)}"',
                style="warn",
            )

        ctx.out.result(data, render_plan)
        return

    if not eligible:
        _fail(ctx.out, "nothing is safe to remove, so there is nothing to apply")
        return

    expected = _removal_phrase(preview.eligible)
    if config.safety.confirm_phrase_when_icloud_sync and confirm.strip().lower() != expected:
        render_blocks()
        _fail(
            ctx.out,
            f"--apply needs the confirmation phrase for this exact plan. Re-run with:\n"
            f'    --confirm "{expected}"\n'
            f"  The count is part of the phrase so an older command cannot authorise "
            f"a larger removal than the one it was written for.",
        )
        return

    def tick(result: Any) -> None:
        ctx.out.line(
            f"  removed {result.removed:,} of {preview.eligible:,}, "
            f"{human_bytes(result.bytes_removed)}",
            style="muted",
        )

    try:
        result = remove_engine.run(config, selector, on_progress=tick)
    except (remove_engine.RemoveError, cloud_engine.CloudError, HelperError) as exc:
        _fail(ctx.out, str(exc))
        return

    data.update(
        {
            "removed": result.removed,
            "alreadyGone": result.already_gone,
            "failed": result.failed,
            "bytesRemoved": result.bytes_removed,
            "failures": result.failures[:20],
        }
    )

    def render_result() -> None:
        ctx.out.title("Removed from iPhone")
        ctx.out.pairs(
            [
                ("removed", f"{result.removed:,} files, {human_bytes(result.bytes_removed)}"),
                ("already gone", f"{result.already_gone:,}"),
                ("failed", f"{result.failed:,}"),
            ]
        )
        if result.failures:
            ctx.out.line()
            ctx.out.line("  Failures", style="head")
            for failure in result.failures[:10]:
                ctx.out.line(f"    {failure['file']}: {failure['error']}", style="warn")
        ctx.out.line()
        ctx.out.line(
            "  These are in Recently Deleted on the phone for 30 days. Storage is "
            "reclaimed when that empties, or when you empty it yourself.",
            style="muted",
        )
        ctx.out.line(
            "  If any of this was wrong, recover it from the phone now, not later.",
            style="muted",
        )

    ctx.out.result(data, render_result)
    if result.failed:
        sys.exit(1)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


@cli.command()
@selector_options
@click.option("--apply", "apply_", is_flag=True, help="Actually release. Without it, plan only.")
@pass_context
def release(ctx: Context, apply_: bool, **kwargs: Any) -> None:
    """Free disk by trashing local copies that are verified in the cloud.

    The remote is asked what it holds in this same run, and only a matching
    hash makes an asset eligible: a ledger row is a claim about when it was
    written, and this deletes the only other copy.

    Files go to the macOS Trash, never unlinked, so everything is restorable
    from Finder.
    """
    config = ctx.config
    try:
        selector = build_selector(**kwargs)
    except SelectorError as exc:
        _fail(ctx.out, str(exc))
        return

    db = ctx.database()
    try:
        eligible, preview = release_engine.plan(config, db, selector)
    except (release_engine.ReleaseError, cloud_engine.CloudError) as exc:
        _fail(ctx.out, str(exc))
        return
    finally:
        db.close()

    freeable = sum(c.size_bytes for c in eligible)
    data: dict[str, Any] = {
        "applied": apply_,
        "examined": preview.examined,
        "eligible": preview.eligible,
        "eligibleBytes": freeable,
        "blocked": preview.blocked,
        "blockedReasons": preview.blocks,
    }

    def render_blocks() -> None:
        if not preview.blocks:
            return
        ctx.out.line()
        ctx.out.line("  Not released, and why", style="head")
        for reason, count in sorted(preview.blocks.items(), key=lambda kv: -kv[1]):
            ctx.out.line(f"    {count:>7,}  {reason}", style="muted")

    if not apply_:

        def render_plan() -> None:
            ctx.out.title("Release plan")
            ctx.out.pairs(
                [
                    ("archived files examined", f"{preview.examined:,}"),
                    ("safe to release", f"{preview.eligible:,}, {human_bytes(freeable)}"),
                    ("blocked", f"{preview.blocked:,}"),
                ]
            )
            render_blocks()
            ctx.out.line()
            if not eligible:
                ctx.out.ok("nothing is safe to release yet")
                return
            ctx.out.line("  Files go to the macOS Trash, restorable from Finder.", style="muted")
            ctx.out.line("  NOTHING HAS BEEN RELEASED. Add --apply to run it.", style="warn")

        ctx.out.result(data, render_plan)
        return

    def tick(result: Any) -> None:
        ctx.out.line(
            f"  released {result.released:,} of {preview.eligible:,}, "
            f"{human_bytes(result.bytes_freed)} freed",
            style="muted",
        )

    try:
        result = release_engine.run(config, selector, on_progress=tick)
    except (release_engine.ReleaseError, cloud_engine.CloudError, HelperError) as exc:
        _fail(ctx.out, str(exc))
        return

    data.update(
        {
            "released": result.released,
            "bytesFreed": result.bytes_freed,
            "failed": result.failed,
            "failures": result.failures[:20],
        }
    )

    def render_result() -> None:
        ctx.out.title("Release")
        ctx.out.pairs(
            [
                ("released", f"{result.released:,} files"),
                ("disk freed", human_bytes(result.bytes_freed)),
                ("blocked", f"{result.blocked:,}"),
                ("failed", f"{result.failed:,}"),
            ]
        )
        render_blocks()
        if result.failures:
            ctx.out.line()
            ctx.out.line("  Failures", style="head")
            for failure in result.failures[:10]:
                ctx.out.line(f"    {failure['file']}: {failure['error']}", style="warn")
        ctx.out.line()
        ctx.out.line(
            "  Everything released is in the macOS Trash, restorable from Finder.",
            style="muted",
        )

    ctx.out.result(data, render_result)
    if result.failed:
        sys.exit(1)


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
            ctx.out.line("  No assets known yet. Inventory the Photos library:", style="muted")
            ctx.out.line("    iphone-image scan", style="muted")
            ctx.out.line("  It reads only, and costs no bandwidth.", style="muted")
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
# device
# ---------------------------------------------------------------------------


@cli.command()
@pass_context
def device(ctx: Context) -> None:
    """Show the Photos library this ledger is bound to.

    There is no connected iPhone to show, and there will not be one. P0
    measured ImageCaptureCore over USB reaching 1.9% of the library with
    deletion refused outright, so the tool works against the Mac's Photos
    library through PhotoKit and iCloud is what carries the assets to it.
    See spikes/P0-transport.md.

    The ledger still calls a library a device, because that is the thing a
    scan and an asset belong to.
    """
    db = ctx.database()
    configured = str(ctx.config.photos.library_path)

    rows = db.conn.execute(
        "SELECT d.id, d.udid AS path, d.name, d.product_kind, "
        "       d.first_seen_at, d.last_seen_at, "
        "       (SELECT COUNT(*) FROM assets a "
        "          WHERE a.device_id = d.id AND a.present_on_phone = 1) AS assets, "
        "       (SELECT COALESCE(SUM(a.size_bytes), 0) FROM assets a "
        "          WHERE a.device_id = d.id AND a.present_on_phone = 1) AS bytes "
        "FROM devices d ORDER BY d.last_seen_at DESC"
    ).fetchall()

    libraries: list[dict[str, Any]] = []
    for row in rows:
        scan_row = db.conn.execute(
            "SELECT id, started_at, finished_at, status, assets_seen "
            "FROM scans WHERE device_id = ? ORDER BY id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        libraries.append(
            {
                "path": row["path"],
                "name": row["name"],
                "kind": row["product_kind"],
                "configured": row["path"] == configured,
                "firstSeen": row["first_seen_at"],
                "lastSeen": row["last_seen_at"],
                "assets": row["assets"],
                "bytes": row["bytes"],
                "lastScan": dict(scan_row) if scan_row else None,
            }
        )
    db.close()

    scanned = any(library["configured"] for library in libraries)
    data: dict[str, Any] = {
        "transport": "photokit",
        "configuredLibrary": configured,
        "scanned": scanned,
        "libraries": libraries,
    }

    def render() -> None:
        ctx.out.title("Photos library")
        ctx.out.pairs(
            [
                ("transport", "PhotoKit, against this Mac's Photos library"),
                ("configured library", configured),
            ]
        )
        for library in libraries:
            last_scan = library["lastScan"]
            ctx.out.line()
            ctx.out.line(
                f"  {library['path']}" + ("" if library["configured"] else "  (not configured)"),
                style="head" if library["configured"] else "muted",
            )
            ctx.out.pairs(
                [
                    ("  assets", f"{library['assets']:,}"),
                    ("  size", human_bytes(library["bytes"])),
                    ("  first scanned", library["firstSeen"]),
                    ("  last seen", library["lastSeen"]),
                    (
                        "  last scan",
                        f"{last_scan['status']} at "
                        f"{last_scan['finished_at'] or last_scan['started_at']}, "
                        f"{last_scan['assets_seen']:,} assets"
                        if last_scan
                        else "never",
                    ),
                ]
            )
        if not scanned:
            ctx.out.line()
            ctx.out.line("  This library has never been scanned. Inventory it:", style="muted")
            ctx.out.line("    iphone-image scan", style="muted")

    ctx.out.result(data, render)


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

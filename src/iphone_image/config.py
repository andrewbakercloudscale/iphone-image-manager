"""Configuration model and loader.

Unknown keys are an error, not a warning. A typo like `retention: screenshot:`
would otherwise silently leave the real setting at its default, and the user
would believe a retention policy was in force when none was.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    ValidationError,
    field_validator,
    model_validator,
)

from .organize.paths import validate_pattern
from .retention import format_duration, parse_duration
from .selector import MEDIA_TYPES, SelectorError, parse_size

DEFAULT_CONFIG_PATH = Path("~/.iphone-image/config.yaml").expanduser()

CONFIG_VERSION = 1


def _expand(value: Any) -> Any:
    return Path(str(value)).expanduser() if value is not None else value


def _to_bytes(value: Any) -> Any:
    """Accept '15GB' as readily as a raw byte count."""
    if value is None or isinstance(value, int):
        return value
    try:
        return parse_size(str(value))
    except SelectorError as exc:
        raise ValueError(str(exc)) from exc


SizeBytes = Annotated[
    int,
    BeforeValidator(_to_bytes),
    PlainSerializer(lambda n: f"{n // 1024**3}GB" if n % 1024**3 == 0 else str(n), return_type=str),
]

Duration = Annotated[
    timedelta | None,
    BeforeValidator(parse_duration),
    PlainSerializer(format_duration, return_type=str),
]

ExpandedPath = Annotated[Path, BeforeValidator(_expand)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class OrganizationMode(StrEnum):
    DATE = "date"
    LOCATION = "location"
    CUSTOM = "custom"


class RemovalPolicy(StrEnum):
    NEVER = "never"
    LOCAL_VERIFIED = "local_verified"
    CLOUD_VERIFIED = "cloud_verified"


class CloudProviderName(StrEnum):
    GOOGLE_DRIVE = "google_drive"
    NONE = "none"


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------


class ArchiveConfig(Strict):
    local_path: ExpandedPath = Path("~/Pictures/iPhoneArchive").expanduser()


class PhotosConfig(Strict):
    """The macOS Photos library this tool reads."""

    library_path: ExpandedPath = Path("~/Pictures/Photos Library.photoslibrary").expanduser()
    #: Expected final asset count, from the Photos app on the phone. Used by
    #: `doctor` to tell a half-finished iCloud sync from a finished one.
    expected_assets: int = Field(default=0, ge=0)


class OrganizationConfig(Strict):
    mode: OrganizationMode = OrganizationMode.DATE
    pattern: str = "{year}/{month}"

    #: For the {event} token: how few photos a place can have and still earn its
    #: own folder. Below it an asset falls back to its month, which sorts beside
    #: the named folders rather than into a junk drawer. Measured on a real
    #: archive, 1 gave 314 folders of which half held fewer than ten photos.
    event_min_photos: int = Field(default=10, ge=1)

    #: A gap longer than this starts a new visit. Cape Town in March and again
    #: in November is two trips, not one eight-month span.
    event_gap_days: int = Field(default=45, ge=1)

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, value: str) -> str:
        problems = validate_pattern(value)
        if problems:
            raise ValueError("; ".join(problems))
        return value


class GeolocationConfig(Strict):
    enabled: bool = True
    reverse_geocode: bool = False  # off by default: it is the only thing that could leave the Mac


class RetentionConfig(Strict):
    screenshots: Duration = None
    whatsapp: Duration = None


class ExactDedupeConfig(Strict):
    enabled: bool = True
    algorithm: str = "sha256"

    @field_validator("algorithm")
    @classmethod
    def _check_algorithm(cls, value: str) -> str:
        if value != "sha256":
            raise ValueError("only sha256 is supported")
        return value


class NearDedupeConfig(Strict):
    enabled: bool = False

    @field_validator("enabled")
    @classmethod
    def _not_yet(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "near duplicate detection is not implemented, see docs/SPEC.md section 14"
            )
        return value


class DeduplicationConfig(Strict):
    exact: ExactDedupeConfig = Field(default_factory=ExactDedupeConfig)
    near_duplicates: NearDedupeConfig = Field(default_factory=NearDedupeConfig)


class CloudConfig(Strict):
    enabled: bool = False
    provider: CloudProviderName = CloudProviderName.NONE
    remote: str = ""  # the rclone remote name, credentials stay in the user's rclone config
    destination: str = "iPhone Archive"

    @model_validator(mode="after")
    def _check(self) -> CloudConfig:
        if self.enabled:
            if self.provider is CloudProviderName.NONE:
                raise ValueError("cloud.enabled is true but no cloud.provider is set")
            if not self.remote.strip():
                raise ValueError(
                    "cloud.enabled is true but cloud.remote is empty. "
                    "Set it to an rclone remote name, for example 'gdrive'."
                )
        return self


class RemoveFromIphoneConfig(Strict):
    policy: RemovalPolicy = RemovalPolicy.NEVER
    include_new_campaign_assets: bool = False
    default_batch_limit: int = Field(default=50, ge=1, le=10_000)


class SafetyConfig(Strict):
    require_final_scan: bool = True
    require_local_verification: bool = True
    require_cloud_verification: bool = True
    block_suspected_proxies: bool = True
    confirm_phrase_when_icloud_sync: bool = True

    @field_validator("require_final_scan", "block_suspected_proxies")
    @classmethod
    def _not_negotiable(cls, value: bool, info: Any) -> bool:
        if not value:
            raise ValueError(f"safety.{info.field_name} cannot be turned off. See docs/SAFETY.md.")
        return value


class RecycleBinConfig(Strict):
    enabled: bool = True
    path: ExpandedPath = Path("~/.iphone-image/recycle-bin").expanduser()
    retention: Duration = timedelta(days=90)
    use_macos_trash: bool = True


class ChunkingConfig(Strict):
    """How much work one run does before stopping at a safe point."""

    enabled: bool = True

    #: 15 GB is roughly one overnight run at the measured iCloud fetch rate of
    #: 0.45 to 1.0 MB/s. Sized by wall clock rather than by disk, because at that
    #: rate 50 GB is well over a day and offers far fewer safe stopping points.
    chunk_bytes: SizeBytes = 15 * 1024**3

    #: Refuse to start a chunk that would take free space below this.
    free_space_floor: SizeBytes = 20 * 1024**3

    #: What `sync` fetches when no --type is given. Video is deliberately absent:
    #: it is 83.5 GB from 3% of the items in the library this was built against,
    #: so it must be asked for by name rather than arriving by default.
    default_types: list[str] = Field(default_factory=lambda: ["photo"])

    within_type: str = "oldest_first"

    @field_validator("default_types")
    @classmethod
    def _known_types(cls, value: list[str]) -> list[str]:
        unknown = [v for v in value if v not in MEDIA_TYPES]
        if unknown:
            raise ValueError(
                f"unknown type(s) {', '.join(unknown)}. Known: {', '.join(sorted(MEDIA_TYPES))}"
            )
        if not value:
            raise ValueError("chunking.default_types cannot be empty")
        return value

    @field_validator("within_type")
    @classmethod
    def _known_order(cls, value: str) -> str:
        if value not in ("oldest_first", "newest_first"):
            raise ValueError("chunking.within_type must be oldest_first or newest_first")
        return value


class DatabaseConfig(Strict):
    path: ExpandedPath = Path("~/.iphone-image/iphone-image.sqlite").expanduser()


class LoggingConfig(Strict):
    level: LogLevel = LogLevel.INFO
    path: ExpandedPath = Path("~/.iphone-image/logs").expanduser()


class PerformanceConfig(Strict):
    local_transfer_workers: int = Field(default=2, ge=1, le=16)
    hash_workers: int = Field(default=4, ge=1, le=32)
    cloud_upload_workers: int = Field(default=4, ge=1, le=32)


class Config(Strict):
    version: int = CONFIG_VERSION
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    photos: PhotosConfig = Field(default_factory=PhotosConfig)
    organization: OrganizationConfig = Field(default_factory=OrganizationConfig)
    geolocation: GeolocationConfig = Field(default_factory=GeolocationConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    deduplication: DeduplicationConfig = Field(default_factory=DeduplicationConfig)
    cloud: CloudConfig = Field(default_factory=CloudConfig)
    remove_from_iphone: RemoveFromIphoneConfig = Field(default_factory=RemoveFromIphoneConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    recycle_bin: RecycleBinConfig = Field(default_factory=RecycleBinConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)

    #: Where this config was loaded from. None means built-in defaults.
    source_path: Path | None = Field(default=None, exclude=True)

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: int) -> int:
        if value != CONFIG_VERSION:
            raise ValueError(
                f"unsupported config version {value}, this build understands {CONFIG_VERSION}"
            )
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> Config:
        problems: list[str] = []

        if self.organization.mode is OrganizationMode.LOCATION and not self.geolocation.enabled:
            problems.append(
                "organization.mode is 'location' but geolocation.enabled is false, "
                "so every asset would land in Unknown Location"
            )

        needs_location = any(
            token in self.organization.pattern
            for token in ("{country}", "{region}", "{city}", "{suburb}")
        )
        if needs_location and not self.geolocation.reverse_geocode:
            problems.append(
                "organization.pattern uses a place token but geolocation.reverse_geocode "
                "is false, so no asset can ever resolve a country or city"
            )

        if (
            self.remove_from_iphone.policy is RemovalPolicy.CLOUD_VERIFIED
            and not self.cloud.enabled
        ):
            problems.append(
                "remove_from_iphone.policy is 'cloud_verified' but cloud.enabled is false, "
                "so no asset could ever become eligible"
            )

        if (
            self.safety.require_cloud_verification
            and not self.cloud.enabled
            and self.remove_from_iphone.policy is not RemovalPolicy.NEVER
        ):
            problems.append(
                "safety.require_cloud_verification is true but cloud.enabled is false, "
                "so removal could never proceed"
            )

        if problems:
            raise ValueError("; ".join(problems))
        return self


# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when a configuration file cannot be loaded or is invalid."""


def _describe(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        where = ".".join(str(p) for p in item["loc"]) or "(root)"
        message = item["msg"].removeprefix("Value error, ")
        if item["type"] == "extra_forbidden":
            message = "unknown setting. Check the spelling against docs/SPEC.md section 26."
        lines.append(f"  {where}: {message}")
    return "\n".join(lines)


def load_config(path: Path | None = None, *, required: bool = False) -> Config:
    """Load configuration, falling back to defaults when no file exists."""
    target = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH

    if not target.exists():
        if required or path is not None:
            raise ConfigError(f"No configuration file at {target}")
        return Config()

    try:
        raw = yaml.safe_load(target.read_text()) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{target} is not valid YAML:\n  {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{target} must contain a YAML mapping, found {type(raw).__name__}")

    try:
        config = Config(**raw)
    except ValidationError as exc:
        raise ConfigError(f"{target} is not valid:\n{_describe(exc)}") from exc

    config.source_path = target
    return config

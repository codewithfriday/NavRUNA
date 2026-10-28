"""Database tables (SRS section 13.3). All times are naive UTC."""
from datetime import date, datetime, timezone

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


utc_now = now


def utc_from_ts(ts: float) -> datetime:
    return datetime.fromtimestamp(float(ts), timezone.utc).replace(tzinfo=None)


ROLES = ("admin", "analyst", "reviewer", "viewer")


# ------------------------------------------------------------------ core: users, audit, settings, jobs
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    # SRS FR-CORE-031: viewers may be limited to aerodromes / units (empty = all)
    scope: Mapped[list] = mapped_column(JSON, default=list)
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    language: Mapped[str] = mapped_column(String(2), default="en")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditLog(Base):
    """Append-only (FR-CORE-032): rows are never updated or deleted by the application."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64))
    obj_type: Mapped[str] = mapped_column(String(32), default="")
    obj_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class ParamSet(Base):
    """Versioned parameter sets (FR-CORE-011). Exactly one is active."""
    __tablename__ = "param_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    author: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(32), index=True)
    period_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|done|error|stopped
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    rows_read: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    param_version: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(64), default="scheduler")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Watermark(Base):
    """Last NavTrace pushed_at processed per job (DR-001)."""
    __tablename__ = "watermarks"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ------------------------------------------------------------------ reference data
class RefVersion(Base):
    __tablename__ = "ref_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)   # runways|sectors|points|airlines
    version: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RefRunway(Base):
    __tablename__ = "ref_runways"
    __table_args__ = (UniqueConstraint("icao", "runway"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4), index=True)
    runway: Mapped[str] = mapped_column(String(4))
    thr_lat: Mapped[float] = mapped_column(Float)
    thr_lon: Mapped[float] = mapped_column(Float)
    thr_elev_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    true_brg: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    arp_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    arp_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    arp_elev_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    issues: Mapped[list] = mapped_column(JSON, default=list)   # DR-004/005 findings
    derived: Mapped[list] = mapped_column(JSON, default=list)  # fields filled in by the platform
    version: Mapped[int] = mapped_column(Integer, default=1)


class Aerodrome(Base):
    """Monitored aerodromes (NFR-017: adding one needs only reference data)."""
    __tablename__ = "aerodromes"
    icao: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    monitored: Mapped[bool] = mapped_column(Boolean, default=False)
    utc_offset: Mapped[int] = mapped_column(Integer, default=7)   # WIB 7, WITA 8, WIT 9
    unit: Mapped[str] = mapped_column(String(64), default="")      # approach unit, for viewer scope


class RefSector(Base):
    __tablename__ = "ref_sectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(8))     # UTA | TMA | CTA
    unit: Mapped[str] = mapped_column(String(64), default="")
    lower_raw: Mapped[str] = mapped_column(String(64), default="")
    upper_raw: Mapped[str] = mapped_column(String(64), default="")
    lower_ft: Mapped[float] = mapped_column(Float, default=0)
    upper_ft: Mapped[float] = mapped_column(Float, default=60000)
    override_lower_ft: Mapped[float | None] = mapped_column(Float, nullable=True)  # DR-006
    override_upper_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    needs_override: Mapped[bool] = mapped_column(Boolean, default=False)
    airspace_class: Mapped[str] = mapped_column(String(16), default="")
    geometry: Mapped[dict] = mapped_column(JSON, default=dict)  # GeoJSON (Multi)Polygon
    area_nm2: Mapped[float] = mapped_column(Float, default=0)
    declared_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FR-CXI-014
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RefPoint(Base):
    __tablename__ = "ref_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="fix")   # fix | navaid
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)


class Airline(Base):
    __tablename__ = "airlines"
    designator: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    country: Mapped[str] = mapped_column(String(32), default="")


# ------------------------------------------------------------------ weather (BMKG)
class WxReport(Base):
    __tablename__ = "wx_reports"
    __table_args__ = (UniqueConstraint("icao", "observed", "kind"), Index("ix_wx_icao_obs", "icao", "observed"))
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    observed: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(8), default="METAR")   # METAR | SPECI
    received: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    raw: Mapped[str] = mapped_column(Text, default="")
    parse_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    wind_dir: Mapped[float | None] = mapped_column(Float, nullable=True)   # degrees true, None = VRB/calm
    wind_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    gust_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    vis_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather: Mapped[str] = mapped_column(String(64), default="")
    ts: Mapped[bool] = mapped_column(Boolean, default=False)
    vcts: Mapped[bool] = mapped_column(Boolean, default=False)
    precip: Mapped[int] = mapped_column(Integer, default=0)     # 0 none, 1 light, 2 moderate, 3 heavy
    cb: Mapped[bool] = mapped_column(Boolean, default=False)
    tcu: Mapped[bool] = mapped_column(Boolean, default=False)
    ceiling_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    dew_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    qnh_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_shear: Mapped[str] = mapped_column(String(64), default="")


class TafReport(Base):
    __tablename__ = "taf_reports"
    __table_args__ = (UniqueConstraint("icao", "issued"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4), index=True)
    issued: Mapped[datetime] = mapped_column(DateTime)
    raw: Mapped[str] = mapped_column(Text, default="")


# ------------------------------------------------------------------ GA module
class GaApproach(Base):
    """One row per approach by a flight to a runway end (SRS 13.3, ga_approach)."""
    __tablename__ = "ga_approach"
    __table_args__ = (Index("ix_ga_icao_day", "icao", "day"), Index("ix_ga_gufi", "gufi"))
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    runway: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    gufi: Mapped[str] = mapped_column(String(64))
    callsign: Mapped[str] = mapped_column(String(16), default="")
    registration: Mapped[str] = mapped_column(String(16), default="")
    icao24: Mapped[str] = mapped_column(String(8), default="")
    operator: Mapped[str] = mapped_column(String(8), default="")
    aircraft_type: Mapped[str] = mapped_column(String(8), default="")
    adep: Mapped[str] = mapped_column(String(4), default="")
    ades: Mapped[str] = mapped_column(String(4), default="")
    t_start: Mapped[datetime] = mapped_column(DateTime)
    t_end: Mapped[datetime] = mapped_column(DateTime)
    t_thr: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # estimated threshold / lowest time
    approach_no: Mapped[int] = mapped_column(Integer, default=1)
    outcome: Mapped[str] = mapped_column(String(16))    # landed | go_around | touch_and_go | low_approach | other
    min_height_ft: Mapped[float] = mapped_column(Float)
    min_dist_nm: Mapped[float] = mapped_column(Float)
    # go-around details (FR-GA-007/008)
    ga_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ga_dist_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    ga_height_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    ga_band: Mapped[str] = mapped_column(String(8), default="")       # high | mid | low
    ga_outcome: Mapped[str] = mapped_column(String(16), default="")   # landed_same | landed_other | diverted | unknown
    ga_delay_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    ga_extra_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    repeated: Mapped[bool] = mapped_column(Boolean, default=False)
    n_approaches: Mapped[int] = mapped_column(Integer, default=1)
    # gates (FR-GA-009): {"6": {...}, "4": ..., "2": ..., "h1000": ..., "h500": ...}
    gates: Mapped[dict] = mapped_column(JSON, default=dict)
    unstable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # sequence (FR-GA-010)
    ttl_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    dtl_nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    since_dep_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    # arrival efficiency, used by EWS
    extra_40nm: Mapped[float | None] = mapped_column(Float, nullable=True)
    holds: Mapped[int] = mapped_column(Integer, default=0)
    max_gap_s: Mapped[float] = mapped_column(Float, default=0)
    # weather (FR-GA-011)
    wx_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    head_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    cross_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    gust_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    vis_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    ts: Mapped[bool] = mapped_column(Boolean, default=False)
    cb: Mapped[bool] = mapped_column(Boolean, default=False)
    precip: Mapped[int] = mapped_column(Integer, default=0)
    qnh_hpa: Mapped[float | None] = mapped_column(Float, nullable=True)
    # validation (FR-GA-015..017)
    validation: Mapped[str] = mapped_column(String(16), default="")   # "" | pending | confirmed | rejected
    validated_by: Mapped[str] = mapped_column(String(64), default="")
    validation_note: Mapped[str] = mapped_column(Text, default="")
    added_manually: Mapped[bool] = mapped_column(Boolean, default=False)
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)
    param_version: Mapped[int] = mapped_column(Integer, default=0)
    ref_version: Mapped[int] = mapped_column(Integer, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class GaDeparture(Base):
    __tablename__ = "ga_departure"
    __table_args__ = (Index("ix_gadep_icao_day", "icao", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    runway: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    gufi: Mapped[str] = mapped_column(String(64))
    callsign: Mapped[str] = mapped_column(String(16), default="")
    operator: Mapped[str] = mapped_column(String(8), default="")
    t_dep: Mapped[datetime] = mapped_column(DateTime)


class GaCoverage(Base):
    """DR-010 low-level coverage per aerodrome, runway end and day."""
    __tablename__ = "ga_coverage"
    __table_args__ = (UniqueConstraint("icao", "runway", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    runway: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    approaches: Mapped[int] = mapped_column(Integer, default=0)
    reached_1000: Mapped[int] = mapped_column(Integer, default=0)
    share: Mapped[float] = mapped_column(Float, default=0)
    flights_seen: Mapped[int] = mapped_column(Integer, default=0)
    gap_share: Mapped[float] = mapped_column(Float, default=0)
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)


class MlModel(Base):
    __tablename__ = "ml_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))   # ga | ews
    scope: Mapped[str] = mapped_column(String(32))  # aerodrome or "pooled"
    version: Mapped[int] = mapped_column(Integer, default=1)
    path: Mapped[str] = mapped_column(String(256), default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    card: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="draft")   # draft | approved | retired
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ------------------------------------------------------------------ SPI engine
class SpiDefinition(Base):
    __tablename__ = "spi_definition"
    __table_args__ = (UniqueConstraint("code", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_unit: Mapped[str] = mapped_column(String(64), default="")
    owner_person: Mapped[str] = mapped_column(String(64), default="")
    module: Mapped[str] = mapped_column(String(8), default="GA")
    formula: Mapped[str] = mapped_column(String(48))
    formula_params: Mapped[dict] = mapped_column(JSON, default=dict)
    unit: Mapped[str] = mapped_column(String(32), default="")
    scale: Mapped[float] = mapped_column(Float, default=1.0)
    breakdowns: Mapped[list] = mapped_column(JSON, default=list)   # ["aerodrome", "aerodrome+runway", ...]
    periods: Mapped[list] = mapped_column(JSON, default=lambda: ["W", "M"])
    direction: Mapped[str] = mapped_column(String(8), default="up_bad")   # up_bad | down_bad
    baseline_method: Mapped[str] = mapped_column(String(16), default="rolling")  # rolling | previous | last_year
    baseline_window: Mapped[int] = mapped_column(Integer, default=90)            # days
    alert_method: Mapped[str] = mapped_column(String(16), default="poisson")     # doc9859 | poisson | ewma | fixed | none
    alert_params: Mapped[dict] = mapped_column(JSON, default=dict)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    min_denominator: Mapped[float] = mapped_column(Float, default=0)
    classification: Mapped[str] = mapped_column(String(12), default="public")
    nasp_goal: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft | approved | retired | rejected
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    backfill_months: Mapped[int] = mapped_column(Integer, default=24)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    change_note: Mapped[str] = mapped_column(Text, default="")


class SpiObservation(Base):
    __tablename__ = "spi_observation"
    __table_args__ = (UniqueConstraint("spi_code", "spi_version", "period", "period_start", "breakdown_key"),
                      Index("ix_obs_lookup", "spi_code", "period", "breakdown_key", "period_start"))
    id: Mapped[int] = mapped_column(primary_key=True)
    spi_code: Mapped[str] = mapped_column(String(16))
    spi_version: Mapped[int] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(1))     # D | W | M
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    breakdown_key: Mapped[str] = mapped_column(String(96), default="")   # "" = total; "aerodrome=WIII|runway=25R"
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    numerator: Mapped[float | None] = mapped_column(Float, nullable=True)
    denominator: Mapped[float | None] = mapped_column(Float, nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    levels: Mapped[dict] = mapped_column(JSON, default=dict)
    alert_state: Mapped[int] = mapped_column(Integer, default=0)   # 0 within, 1..3 level
    reliable: Mapped[bool] = mapped_column(Boolean, default=True)  # min denominator met
    incomplete: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_share: Mapped[float | None] = mapped_column(Float, nullable=True)   # FR-GA-018
    source: Mapped[str] = mapped_column(String(16), default="computed")         # computed | manual
    note: Mapped[str] = mapped_column(Text, default="")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    data_version: Mapped[str] = mapped_column(String(32), default="")


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alert_group", "source", "ref", "breakdown_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16))      # SPI | GA | TDM | EWS | JOB | COVERAGE
    ref: Mapped[str] = mapped_column(String(48))         # SPI code, cluster set, cell id, job name
    breakdown_key: Mapped[str] = mapped_column(String(96), default="")
    level: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text, default="")
    public_message: Mapped[str] = mapped_column(Text, default="")   # no identifiers (FR-CORE-021)
    link: Mapped[str] = mapped_column(String(256), default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_sent: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_level: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(12), default="open")   # open | acknowledged | closed
    ack_by: Mapped[str] = mapped_column(String(64), default="")
    ack_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ack_comment: Mapped[str] = mapped_column(Text, default="")
    delivery: Mapped[list] = mapped_column(JSON, default=list)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="srb")
    period: Mapped[str] = mapped_column(String(7))          # YYYY-MM
    title: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft | review | released
    path: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    released_by: Mapped[str] = mapped_column(String(64), default="")
    released_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    data_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


# ------------------------------------------------------------------ TDM
class TdmRoute(Base):
    __tablename__ = "tdm_routes"
    __table_args__ = (UniqueConstraint("adep", "ades"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    adep: Mapped[str] = mapped_column(String(4))
    ades: Mapped[str] = mapped_column(String(4))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    unit: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TdmClusterSet(Base):
    __tablename__ = "tdm_cluster_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("tdm_routes.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    window_from: Mapped[date] = mapped_column(Date)
    window_to: Mapped[date] = mapped_column(Date)
    n_flights: Mapped[int] = mapped_column(Integer, default=0)
    n_unclustered: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class TdmCluster(Base):
    __tablename__ = "tdm_clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("tdm_cluster_sets.id", ondelete="CASCADE"), index=True)
    label: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(64), default="")
    n: Mapped[int] = mapped_column(Integer, default=0)
    share: Mapped[float] = mapped_column(Float, default=0)
    medoid: Mapped[list] = mapped_column(JSON, default=list)      # [[lon, lat, alt_ft], ...]
    corridor: Mapped[dict] = mapped_column(JSON, default=dict)    # {"p5": [...], "p95": [...]} NM offsets
    profile: Mapped[dict] = mapped_column(JSON, default=dict)     # {"p5", "p50", "p95"} ft
    retired: Mapped[bool] = mapped_column(Boolean, default=False)


class TdmCriticalPoint(Base):
    __tablename__ = "tdm_critical_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("tdm_routes.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default="fix")   # fix | navaid | boundary | tma | manual
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    in_tma: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TdmFlight(Base):
    __tablename__ = "tdm_flights"
    __table_args__ = (Index("ix_tdmf_route_day", "route_id", "day"), UniqueConstraint("route_id", "gufi"))
    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("tdm_routes.id", ondelete="CASCADE"))
    gufi: Mapped[str] = mapped_column(String(64))
    callsign: Mapped[str] = mapped_column(String(16), default="")
    operator: Mapped[str] = mapped_column(String(8), default="")
    day: Mapped[date] = mapped_column(Date)
    t_dep: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    set_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cluster_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    off_pattern: Mapped[bool] = mapped_column(Boolean, default=False)
    mean_dist_nm: Mapped[float] = mapped_column(Float, default=0)
    max_dev_nm: Mapped[float] = mapped_column(Float, default=0)
    flag_lateral: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_vertical: Mapped[bool] = mapped_column(Boolean, default=False)
    track: Mapped[list] = mapped_column(JSON, default=list)   # simplified [[lon, lat, alt]] for maps


class TdmPoint(Base):
    __tablename__ = "tdm_points"
    __table_args__ = (Index("ix_tdmp_cp", "cp_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    flight_id: Mapped[int] = mapped_column(ForeignKey("tdm_flights.id", ondelete="CASCADE"), index=True)
    cp_id: Mapped[int] = mapped_column(Integer)
    lateral_nm: Mapped[float] = mapped_column(Float)
    alt_diff_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    flag_lateral: Mapped[bool] = mapped_column(Boolean, default=False)
    flag_vertical: Mapped[bool] = mapped_column(Boolean, default=False)


# ------------------------------------------------------------------ CXI
class CxiSectorHour(Base):
    __tablename__ = "cxi_sector_hour"
    __table_args__ = (UniqueConstraint("sector_id", "hour"), Index("ix_cxi_hour", "hour"))
    id: Mapped[int] = mapped_column(primary_key=True)
    sector_id: Mapped[int] = mapped_column(Integer)          # 0 = outside controlled sectors
    hour: Mapped[datetime] = mapped_column(DateTime)
    entries: Mapped[int] = mapped_column(Integer, default=0)
    peak: Mapped[int] = mapped_column(Integer, default=0)
    occupancy: Mapped[float] = mapped_column(Float, default=0)
    flight_hours: Mapped[float] = mapped_column(Float, default=0)
    interaction_h: Mapped[float] = mapped_column(Float, default=0)
    vertical_h: Mapped[float] = mapped_column(Float, default=0)
    horizontal_h: Mapped[float] = mapped_column(Float, default=0)
    speed_h: Mapped[float] = mapped_column(Float, default=0)
    crossings: Mapped[int] = mapped_column(Integer, default=0)
    proximity: Mapped[int] = mapped_column(Integer, default=0)
    level_changes: Mapped[int] = mapped_column(Integer, default=0)
    heading_changes: Mapped[int] = mapped_column(Integer, default=0)
    adjusted_density: Mapped[float] = mapped_column(Float, default=0)
    structural_index: Mapped[float] = mapped_column(Float, default=0)
    pru_score: Mapped[float] = mapped_column(Float, default=0)
    index: Mapped[float | None] = mapped_column(Float, nullable=True)
    top5: Mapped[bool] = mapped_column(Boolean, default=False)
    param_version: Mapped[int] = mapped_column(Integer, default=0)


# ------------------------------------------------------------------ EWS
class EwsCell(Base):
    __tablename__ = "ews_cells"
    __table_args__ = (UniqueConstraint("icao", "day", "block", "operator"), Index("ix_ews_day", "day"))
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    block: Mapped[int] = mapped_column(Integer)          # 0..5 = 00-04, 04-08 ... UTC; -1 = whole day
    operator: Mapped[str] = mapped_column(String(8))     # "*" = all operators
    movements: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[list] = mapped_column(JSON, default=list)
    alert: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(16), default="")    # real | known_cause | false_alarm
    label_cause: Mapped[str] = mapped_column(String(32), default="")
    label_by: Mapped[str] = mapped_column(String(64), default="")
    label_note: Mapped[str] = mapped_column(Text, default="")
    model_version: Mapped[int] = mapped_column(Integer, default=0)


class EwsCoverage(Base):
    __tablename__ = "ews_coverage"
    __table_args__ = (UniqueConstraint("icao", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    icao: Mapped[str] = mapped_column(String(4))
    day: Mapped[date] = mapped_column(Date)
    flights: Mapped[int] = mapped_column(Integer, default=0)
    expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap_share: Mapped[float] = mapped_column(Float, default=0)
    low_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)

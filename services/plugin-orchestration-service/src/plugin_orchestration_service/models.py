from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("orchestration")

NODE_ID_SELF = "self"


class PluginManifest(Base):
    """Manifest eines "dazustellbaren" Elements (Konzept 3.8: Connector,
    Rendering-Backend, Regel-Plugin, ...). `resource_cpu_cores`/
    `resource_ram_mb` bleiben `None`, wenn das Manifest keine statischen
    Richtwerte deklariert - die Cold-Start-Platzierung (siehe `placement.py`)
    faellt dann auf den Median beobachteter `PluginResourceReport`-Werte
    zurueck. `plugin_type` ist bewusst der Primaerschluessel (nicht
    `(plugin_type, version)`) - ein erneutes `POST /plugins` fuer denselben
    Typ ist ein Upsert (neue Version ersetzt die alte Manifest-Deklaration),
    kein paralleles Nebeneinander mehrerer Manifest-Versionen."""

    __tablename__ = "plugin_manifest"

    plugin_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    scaling_type: Mapped[str] = mapped_column(String(32))
    resource_cpu_cores: Mapped[float | None] = mapped_column(Float, default=None)
    resource_ram_mb: Mapped[float | None] = mapped_column(Float, default=None)
    load_profile: Mapped[str | None] = mapped_column(String(64), default=None)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PluginResourceReport(Base):
    """Selbstmeldung einer laufenden Plugin-Instanz ueber ihre eigene, mit
    `psutil.Process()` gemessene Ressourcennutzung - analog zum Registry-
    Heartbeat-Prinzip (3.2a), aber fuer Ressourcenverbrauch statt
    Erreichbarkeit. `instance_id` ist Primaerschluessel (Upsert je Instanz,
    kein Verlauf) - fuer die Median-Berechnung in `placement.py` zaehlt nur
    der zuletzt gemeldete, noch "frische" Wert je Instanz."""

    __tablename__ = "plugin_resource_report"

    instance_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plugin_type: Mapped[str] = mapped_column(String(128), index=True)
    # Gleiche Einheit wie `PluginManifest.resource_cpu_cores` (Kerne, nicht
    # Prozent) - macht Manifest-Wert und beobachteten Median direkt
    # vergleichbar, ohne Umrechnung an der Verwendungsstelle.
    cpu_cores: Mapped[float] = mapped_column(Float)
    ram_mb: Mapped[float] = mapped_column(Float)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClusterNode(Base):
    """Ressourcen-Stichprobe eines Knotens (3.8: "eigener Mechanismus", da
    die vollwertige Sensor-Infrastruktur aus 10.1 erst Phase 11 existiert,
    siehe P10-S0-Befund). Der eigene Host (`node_id = NODE_ID_SELF`) wird
    periodisch per `psutil` aktualisiert (siehe `sampler.py`); weitere Knoten
    koennen sich seit P10-S2 ueber `POST /nodes/{node_id}` selbst melden
    (kein echter zweiter Host in der real existierenden Docker-Compose-
    Umgebung, aber dieselbe Selbstmelde-Logik, die ein kuenftiger zweiter
    Host nutzen wuerde - Praezedenzfall `PluginResourceReport`). Die
    First-Fit-Platzierung (`placement.py`) waehlt ueber alle Zeilen."""

    __tablename__ = "cluster_node"

    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    cpu_cores: Mapped[float] = mapped_column(Float)
    total_ram_mb: Mapped[float] = mapped_column(Float)
    cpu_usage_percent: Mapped[float] = mapped_column(Float)
    available_ram_mb: Mapped[float] = mapped_column(Float)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlacementDecision(Base):
    """Auditierte Platzierungsentscheidung (3.8: "Auch der Plugin
    Orchestration Service selbst ist ein Service, dessen Entscheidungen
    auditiert werden") - persistiert als lokales Read-Modell fuer
    `GET /placements` UND als `orchestration.placement.decided`-Event an den
    Event-Bus publiziert (audit-service konsumiert `orchestration.>`, siehe
    P10-S1-Aenderung an dessen `settings.subjects`)."""

    __tablename__ = "placement_decision"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    plugin_type: Mapped[str] = mapped_column(String(128), index=True)
    node_id: Mapped[str | None] = mapped_column(String(128), default=None)
    estimated_cpu_cores: Mapped[float] = mapped_column(Float)
    estimated_ram_mb: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    placement_allowed: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str | None] = mapped_column(String(256), default=None)
    dependency_status: Mapped[dict] = mapped_column(JSON, default=dict)
    # Plattform-Scheduler-Erkennung (3.8, P10-S2): haelt fest, ob eine
    # angeschlossene Plattform-Scheduler-Plattform die Entscheidung getroffen
    # hat oder der FFD-Fallback - siehe `platform_scheduler.py`.
    placement_method: Mapped[str] = mapped_column(String(32), default="ffd")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

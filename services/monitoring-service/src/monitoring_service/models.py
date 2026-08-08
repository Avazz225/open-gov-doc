from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("monitoring")

GLOBAL_DEFAULT_KEY = "__global__"


class SensorConfigEntry(Base):
    """Sensor-Aktivierungskonfiguration (10.1, P11-S1) - ein Sonderschlüssel
    `__global__` trägt die Grundeinstellung ("alles"/"nichts" überwachen),
    jeder andere Schlüssel ist ein sensorspezifischer Override, der die
    Grundeinstellung für genau diesen Sensor überschreibt. Bewusst keine
    Anbindung an 7.3 (Konfigurationsexport existiert erst P12-S3, siehe
    P11-S0-Befund) - eigenständige, auditierte Persistenz bis dahin."""

    __tablename__ = "sensor_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)

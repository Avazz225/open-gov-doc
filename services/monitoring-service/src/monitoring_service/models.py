from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("monitoring")

GLOBAL_DEFAULT_KEY = "__global__"


class SensorConfigEntry(Base):
    """Sensor activation configuration (10.1, P11-S1) - a special key
    `__global__` holds the base setting ("monitor everything"/"nothing"),
    every other key is a sensor-specific override that overrides the
    base setting for exactly that sensor. Deliberately no
    connection to 7.3 (configuration export only exists from P12-S3, see
    P11-S0 finding) - standalone, audited persistence until then."""

    __tablename__ = "sensor_config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)

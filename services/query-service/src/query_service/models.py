from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("query")

MANIPULATION_MODE_STATUS_ID = 1


class ManipulationModeStatus(Base):
    """Schutzschalter fuer die Manipulationsseite der Query-Konsole (6.1,
    Punkt 1, seit P8-S2) - Singleton-Zeile, gleiches Muster wie
    `permission-service`s `SystemMaintenanceMode`. Genuin service-eigener
    Zustand (kein Read-Modell eines fremden Service) - reversiert die
    P8-S1-Entscheidung "keine eigene Datenhaltung" nicht, die sich nur gegen
    das Duplizieren fremder Daten richtete."""

    __tablename__ = "manipulation_mode_status"

    id: Mapped[int] = mapped_column(primary_key=True, default=MANIPULATION_MODE_STATUS_ID)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_by: Mapped[str | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

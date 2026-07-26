from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


def make_declarative_base(schema: str) -> type[DeclarativeBase]:
    """Erzeugt eine Declarative-Base, deren Tabellen fest an ``schema`` gebunden sind.

    Durchsetzung der Schema-pro-Service-Konvention (3.1): jeder Service ruft dies
    einmal mit seinem eigenen Schemanamen auf, alle seine Modelle erben davon.
    """

    class Base(DeclarativeBase):
        metadata = MetaData(schema=schema)

    return Base

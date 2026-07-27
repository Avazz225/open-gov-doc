from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RenderOutput:
    rendition_type: str
    target_filename: str
    target_content_type: str
    data: bytes


class Renderer(ABC):
    """Eine Regel "Quellformat -> Zielformat" (2.4/3.7), analog zum
    Backend-Plugin-Prinzip des Storage Service (3.3/3.8): neue Formate/
    Fähigkeiten werden ergänzt, indem eine weitere `Renderer`-Klasse in
    `renderers/__init__.py` registriert wird, ohne bestehende Renderer oder
    die Pipeline (`pipeline.py`) zu ändern."""

    rendition_type: str

    @abstractmethod
    def supports(self, *, content_type: str | None, filename: str) -> bool: ...

    @abstractmethod
    async def render(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> RenderOutput: ...

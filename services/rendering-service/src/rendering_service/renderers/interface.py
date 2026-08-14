from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RenderOutput:
    rendition_type: str
    target_filename: str
    target_content_type: str
    data: bytes


class Renderer(ABC):
    """A rule "source format -> target format" (2.4/3.7), analogous to the
    backend plugin principle of the Storage Service (3.3/3.8): new formats/
    capabilities are added by registering another `Renderer` class in
    `renderers/__init__.py`, without changing existing renderers or the
    pipeline (`pipeline.py`)."""

    rendition_type: str

    @abstractmethod
    def supports(self, *, content_type: str | None, filename: str) -> bool: ...

    @abstractmethod
    async def render(
        self, data: bytes, *, filename: str, content_type: str | None
    ) -> RenderOutput: ...

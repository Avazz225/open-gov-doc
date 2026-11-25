import asyncio
import os
from io import BytesIO

from PIL import Image

from rendering_service.renderers.interface import Renderer, RenderOutput

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff"}
_THUMBNAIL_SIZE = (256, 256)


class ThumbnailRenderer(Renderer):
    """Preview thumbnail for raster images (3.7) - real image processing via
    Pillow, not a placeholder."""

    rendition_type = "thumbnail"

    def supports(self, *, content_type: str | None, filename: str) -> bool:
        if content_type and content_type.startswith("image/"):
            return True
        return os.path.splitext(filename)[1].lower() in _IMAGE_EXTENSIONS

    async def render(self, data: bytes, *, filename: str, content_type: str | None) -> RenderOutput:
        # Pillow in a thread (P95-S1f): it would block the event loop, and
        # with parallel renditions the other ones with it.
        return await asyncio.to_thread(self._render, data, filename)

    def _render(self, data: bytes, filename: str) -> RenderOutput:
        image = Image.open(BytesIO(data))
        image.load()
        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")
        image.thumbnail(_THUMBNAIL_SIZE)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        stem = os.path.splitext(filename)[0] or "thumbnail"
        return RenderOutput(
            rendition_type=self.rendition_type,
            target_filename=f"{stem}_thumbnail.png",
            target_content_type="image/png",
            data=buffer.getvalue(),
        )

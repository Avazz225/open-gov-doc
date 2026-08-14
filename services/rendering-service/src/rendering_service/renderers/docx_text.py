import os
from io import BytesIO

from docx import Document

from rendering_service.renderers.interface import Renderer, RenderOutput

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocxTextExtractionRenderer(Renderer):
    """Rendition for Word documents (2.4): text extraction to .txt, so that
    the content remains accessible even if the Office rendering component
    fails - exactly the example named in the concept ".docx -> always a
    .txt rendition"."""

    rendition_type = "substitute_text"

    def supports(self, *, content_type: str | None, filename: str) -> bool:
        if content_type == _DOCX_CONTENT_TYPE:
            return True
        return filename.lower().endswith(".docx")

    async def render(self, data: bytes, *, filename: str, content_type: str | None) -> RenderOutput:
        document = Document(BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        stem = os.path.splitext(filename)[0] or "dokument"
        return RenderOutput(
            rendition_type=self.rendition_type,
            target_filename=f"{stem}.txt",
            target_content_type="text/plain; charset=utf-8",
            data=text.encode("utf-8"),
        )

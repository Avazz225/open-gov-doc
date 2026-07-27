import os
from io import BytesIO

from docx import Document

from rendering_service.renderers.interface import Renderer, RenderOutput

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class DocxTextExtractionRenderer(Renderer):
    """Ersatzdarstellung für Word-Dokumente (2.4): Textextraktion nach .txt,
    damit der Inhalt auch bei einem Ausfall der Office-Rendering-Komponente
    zugänglich bleibt - genau das im Konzept genannte Beispiel ".docx -> immer
    .txt-Ersatzdarstellung"."""

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

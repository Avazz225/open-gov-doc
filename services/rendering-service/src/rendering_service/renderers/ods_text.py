import os
from io import BytesIO

from odf.opendocument import load
from odf.table import Table, TableCell, TableRow
from odf.teletype import extractText

from rendering_service.renderers.interface import Renderer, RenderOutput

_ODS_CONTENT_TYPE = "application/vnd.oasis.opendocument.spreadsheet"


class OdsTextExtractionRenderer(Renderer):
    """Rendition for OpenDocument spreadsheets (2.4): text extraction to
    .txt, same pattern as Word/PowerPoint (`DocxTextExtractionRenderer`/
    `PptxTextExtractionRenderer`) - previously there was no renderer at all
    for .ods, so no rendition/preview was ever produced for it (user
    feedback: ".ods preview doesn't work"). A real .ods->.pdf conversion
    would need an external Office rendering component, as with .pptx - see
    the reasoning there."""

    rendition_type = "substitute_text"

    def supports(self, *, content_type: str | None, filename: str) -> bool:
        if content_type == _ODS_CONTENT_TYPE:
            return True
        return filename.lower().endswith(".ods")

    async def render(self, data: bytes, *, filename: str, content_type: str | None) -> RenderOutput:
        document = load(BytesIO(data))
        sheet_texts = []
        for sheet in document.spreadsheet.getElementsByType(Table):
            row_texts = []
            for row in sheet.getElementsByType(TableRow):
                cell_texts = [extractText(cell) for cell in row.getElementsByType(TableCell)]
                if any(cell_texts):
                    row_texts.append("\t".join(cell_texts))
            sheet_name = sheet.getAttribute("name") or "Tabelle"
            sheet_texts.append(f"--- {sheet_name} ---\n" + "\n".join(row_texts))
        stem = os.path.splitext(filename)[0] or "tabelle"
        return RenderOutput(
            rendition_type=self.rendition_type,
            target_filename=f"{stem}.txt",
            target_content_type="text/plain; charset=utf-8",
            data="\n\n".join(sheet_texts).encode("utf-8"),
        )

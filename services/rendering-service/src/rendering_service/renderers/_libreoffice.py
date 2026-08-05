import asyncio
import os
import shutil
import tempfile
from pathlib import Path

_BINARY_CANDIDATES = (
    "soffice",
    "libreoffice",
    "/opt/libreoffice26.2/program/soffice",
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
)

_CONVERT_TIMEOUT_SECONDS = 90


class ConversionError(Exception):
    pass


def _resolve_binary() -> str:
    for candidate in _BINARY_CANDIDATES:
        if os.path.sep in candidate:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    raise ConversionError(
        "Kein LibreOffice-Binary gefunden (weder auf PATH noch unter den bekannten "
        "Installationspfaden) - Konvertierung nicht möglich."
    )


async def convert_via_libreoffice(data: bytes, *, filename: str, pdf_a: bool = True) -> bytes:
    """Konvertiert beliebige LibreOffice-lesbare Dokumente (Office-Formate,
    Text, RTF, ...) zu PDF über einen `soffice --headless`-Subprozess (5.6,
    seit P7-S3). Jeder Aufruf bekommt ein isoliertes `UserInstallation`-Profil
    (`-env:UserInstallation=file://...`) - ohne das verweigert ein zweiter,
    parallel laufender `soffice`-Prozess mit einer Profil-Sperre den Start
    ("Another instance is running"), da LibreOffice sonst ein einziges,
    geteiltes Nutzerprofil verwendet. `pdf_a=True` nutzt LibreOffices eigenen
    PDF/A-1b-Export-Filter (eingebettete Fonts/XMP-Metadaten) - technisch
    konformer als reines PDF, aber weiterhin nicht unabhängig
    veraPDF-validiert."""
    binary = _resolve_binary()
    suffix = Path(filename).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="dms-lo-") as tmp_root:
        tmp_root_path = Path(tmp_root)
        in_dir = tmp_root_path / "in"
        out_dir = tmp_root_path / "out"
        profile_dir = tmp_root_path / "profile"
        in_dir.mkdir()
        out_dir.mkdir()

        input_path = in_dir / f"input{suffix}"
        input_path.write_bytes(data)

        if pdf_a:
            convert_filter = 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":1}}'
        else:
            convert_filter = "pdf"

        args = [
            "--headless",
            "--norestore",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to",
            convert_filter,
            "--outdir",
            str(out_dir),
            str(input_path),
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_CONVERT_TIMEOUT_SECONDS
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise ConversionError(
                    f"LibreOffice-Konvertierung von {filename!r} nach "
                    f"{_CONVERT_TIMEOUT_SECONDS}s abgebrochen (Timeout)."
                ) from exc
        except FileNotFoundError as exc:
            raise ConversionError(f"LibreOffice-Binary {binary!r} nicht ausführbar.") from exc

        if process.returncode != 0:
            raise ConversionError(
                f"LibreOffice-Konvertierung von {filename!r} fehlgeschlagen "
                f"(exit={process.returncode}): {stderr.decode(errors='replace').strip()}"
            )

        output_path = out_dir / "input.pdf"
        if not output_path.is_file():
            raise ConversionError(
                f"LibreOffice-Konvertierung von {filename!r} lieferte keine PDF-Datei "
                f"(stdout: {stdout.decode(errors='replace').strip()})"
            )
        result = output_path.read_bytes()
        if not result.startswith(b"%PDF"):
            raise ConversionError(
                f"LibreOffice-Konvertierung von {filename!r} lieferte keine gültige PDF-Datei."
            )
        return result

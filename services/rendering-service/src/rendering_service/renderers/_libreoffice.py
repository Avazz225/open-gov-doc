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


class _ProfilePool:
    """Reused LibreOffice user profiles (P95-S1f). Building a fresh profile
    is most of a small conversion's cost - about 960 ms against 200-260 ms
    with a reused one, measured in the container. Each profile serves one
    `soffice` at a time (a second process on the same profile refuses to
    start), so the pool holds as many as conversions may run in parallel.
    A conversion that failed or timed out discards its profile: a killed
    process can leave a lock behind that would block the next one."""

    def __init__(self, size: int) -> None:
        self._root = Path(tempfile.gettempdir()) / f"dms-lo-profiles-{os.getpid()}"
        self._free: asyncio.Queue[Path] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._size = size

    def _queue(self) -> asyncio.Queue[Path]:
        # Created lazily inside the running event loop, and again if the
        # loop changed (tests run one loop per test; a queue is bound to the
        # loop it first waited in).
        loop = asyncio.get_running_loop()
        if self._free is None or self._loop is not loop:
            self._loop = loop
            self._free = asyncio.Queue()
            for index in range(self._size):
                self._free.put_nowait(self._root / str(index))
        return self._free

    async def acquire(self) -> Path:
        return await self._queue().get()

    def release(self, profile: Path, *, healthy: bool) -> None:
        if not healthy:
            shutil.rmtree(profile, ignore_errors=True)
        self._queue().put_nowait(profile)


_profiles = _ProfilePool(max(1, int(os.environ.get("DMS_LIBREOFFICE_PROFILES", "4"))))


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
    """Converts any LibreOffice-readable document (Office formats, text,
    RTF, ...) to PDF via a `soffice --headless` subprocess (5.6, since
    P7-S3). Each call gets an isolated `UserInstallation` profile
    (`-env:UserInstallation=file://...`) - without this, a second, parallel
    running `soffice` process refuses to start due to a profile lock
    ("Another instance is running"), since LibreOffice otherwise uses a
    single, shared user profile. Since P95-S1f the profile comes from a
    small pool of reused ones (`_ProfilePool`, `DMS_LIBREOFFICE_PROFILES`,
    default 4 - the consumer's default concurrency). `pdf_a=True` uses LibreOffice's own
    PDF/A-1b export filter (embedded fonts/XMP metadata) - technically more
    compliant than plain PDF, but still not independently veraPDF-validated."""
    binary = _resolve_binary()
    suffix = Path(filename).suffix or ".bin"

    with tempfile.TemporaryDirectory(prefix="dms-lo-") as tmp_root:
        tmp_root_path = Path(tmp_root)
        in_dir = tmp_root_path / "in"
        out_dir = tmp_root_path / "out"
        in_dir.mkdir()
        out_dir.mkdir()

        input_path = in_dir / f"input{suffix}"
        input_path.write_bytes(data)

        if pdf_a:
            convert_filter = 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":1}}'
        else:
            convert_filter = "pdf"

        profile_dir = await _profiles.acquire()
        healthy = False
        try:
            stdout, stderr, returncode = await _run_soffice(
                binary,
                [
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation=file://{profile_dir}",
                    "--convert-to",
                    convert_filter,
                    "--outdir",
                    str(out_dir),
                    str(input_path),
                ],
                filename,
            )
            healthy = returncode == 0
        finally:
            _profiles.release(profile_dir, healthy=healthy)

        if returncode != 0:
            raise ConversionError(
                f"LibreOffice-Konvertierung von {filename!r} fehlgeschlagen "
                f"(exit={returncode}): {stderr.decode(errors='replace').strip()}"
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


async def _run_soffice(binary: str, args: list[str], filename: str) -> tuple[bytes, bytes, int]:
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"LibreOffice-Binary {binary!r} nicht ausführbar.") from exc
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
    return stdout, stderr, process.returncode

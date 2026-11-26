"""Key handling: where the licensor's key pair lives, and guards that keep the
private key out of git.

- The default key home is `$OG_LICENSE_HOME`, else `~/.config/og-doc-license/`.
- `keygen` refuses to write into a git work tree at all.
- Using a private key that sits in a git work tree and isn't ignored there is
  refused as well; the only exception is the explicit development key (`--dev`),
  which is git-ignored by `*.pem`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PRIVATE_KEY_NAME = "private.pem"
PUBLIC_KEY_NAME = "public.pem"
ISSUED_LOG_NAME = "issued.jsonl"

# The licence-service test fixture (git-ignored, exists only on developer
# machines). Its public half is license-service's built-in default, so tokens
# signed with it are accepted by every installation that hasn't set
# DMS_LICENSE_PUBLIC_KEY_PEM - only for the dev stack.
DEV_PRIVATE_KEY_RELATIVE = Path("services/license-service/tests/fixtures/dev_private_key.pem")


class KeyError_(Exception):
    """A key problem the user has to resolve (shown without a traceback)."""


def key_home() -> Path:
    configured = os.environ.get("OG_LICENSE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path("~/.config/og-doc-license").expanduser()


def git_work_tree(path: Path) -> Path | None:
    """The git work tree containing `path` (or its nearest existing parent), if any."""
    probe = path if path.is_dir() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    for candidate in (probe, *probe.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def is_git_ignored(path: Path, work_tree: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(work_tree), "check-ignore", "-q", str(path)],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def generate_key_pair(
    directory: Path, *, bits: int = 3072, force: bool = False
) -> tuple[Path, Path]:
    directory = directory.expanduser().resolve()
    tree = git_work_tree(directory)
    if tree is not None:
        raise KeyError_(
            f"{directory} liegt im Git-Arbeitsbaum {tree}. Den privaten Schlüssel nie dort "
            "ablegen - ein Verzeichnis außerhalb wählen (Standard: ~/.config/og-doc-license)."
        )
    private_path = directory / PRIVATE_KEY_NAME
    public_path = directory / PUBLIC_KEY_NAME
    if private_path.exists() and not force:
        raise KeyError_(
            f"{private_path} existiert bereits. Ein neues Schlüsselpaar macht alle damit "
            "ausgestellten Lizenzen ungültig, sobald Installationen den neuen öffentlichen "
            "Schlüssel bekommen (Rotation: ADR 0084). Mit --force überschreiben."
        )
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Create with 0600 from the start instead of chmod after writing.
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(private_pem)
    public_path.write_bytes(public_pem_from_private(private_pem).encode("ascii"))
    return private_path, public_path


def public_pem_from_private(private_pem: bytes) -> str:
    key = serialization.load_pem_private_key(private_pem, password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def find_dev_key(start: Path | None = None) -> Path:
    """The development key of the repository this is run in."""
    here = (start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        path = candidate / DEV_PRIVATE_KEY_RELATIVE
        if path.exists():
            return path
    raise KeyError_(
        f"Entwicklungsschlüssel {DEV_PRIVATE_KEY_RELATIVE} nicht gefunden (nur auf "
        "Entwicklungsrechnern vorhanden, nie im Git). Im dms-Verzeichnis ausführen."
    )


def load_private_key(path: Path | None, *, dev: bool) -> tuple[str, Path, bool]:
    """Returns (pem, path, is_dev). Refuses a non-dev key that git could pick up."""
    if dev:
        resolved = find_dev_key()
        return resolved.read_text(encoding="ascii"), resolved, True
    resolved = (path or key_home() / PRIVATE_KEY_NAME).expanduser().resolve()
    if not resolved.exists():
        raise KeyError_(
            f"Privater Schlüssel {resolved} fehlt. Erst `og-license keygen` ausführen oder "
            "--key angeben."
        )
    tree = git_work_tree(resolved)
    if tree is not None and not is_git_ignored(resolved, tree):
        raise KeyError_(
            f"{resolved} liegt im Git-Arbeitsbaum {tree} und ist nicht ignoriert. Den "
            "Schlüssel aus dem Repository entfernen und außerhalb ablegen."
        )
    mode = resolved.stat().st_mode & 0o777
    if mode & 0o077:
        raise KeyError_(
            f"{resolved} ist für andere lesbar (Modus {oct(mode)}). `chmod 600 {resolved}`."
        )
    return resolved.read_text(encoding="ascii"), resolved, False


def load_public_key(path: Path | None, *, dev: bool) -> str:
    if dev:
        return public_pem_from_private(find_dev_key().read_bytes())
    if path is not None:
        return path.expanduser().read_text(encoding="ascii")
    configured = os.environ.get("DMS_LICENSE_PUBLIC_KEY_PEM")
    if configured:
        return configured
    default = key_home() / PUBLIC_KEY_NAME
    if default.exists():
        return default.read_text(encoding="ascii")
    raise KeyError_(
        "Kein öffentlicher Schlüssel: --public-key angeben, DMS_LICENSE_PUBLIC_KEY_PEM setzen, "
        f"{default} anlegen (keygen) oder --dev für den Entwicklungs-Stack."
    )

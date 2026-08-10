#!/usr/bin/env python3
"""Packt das .oxt-Erweiterungspaket (ein gewöhnliches ZIP mit fester
Verzeichnisstruktur, siehe https://wiki.documentfoundation.org/Documentation/DevGuide/Extensions).
Kein Node/npm-artiges Build-Tooling nötig - eine reine Standardbibliotheks-
Zip-Erstellung reicht für ein .oxt-Paket vollständig aus.

    python3 build.py [ziel-datei.oxt]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Reihenfolge/Auswahl bewusst explizit statt eines rekursiven "alles außer
# tests/" - verhindert, dass versehentlich Entwicklungsdateien (build.py
# selbst, README.md, .gitignore-artige Reste) im Paket landen.
INCLUDE_FILES = [
    "META-INF/manifest.xml",
    "description.xml",
    "Addons.xcu",
    "description/description-de.txt",
    "description/description-en.txt",
    "registration/icon.png",
]
INCLUDE_DIRS = ["python"]


def build(output_path: Path) -> None:
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for rel_path in INCLUDE_FILES:
            archive.write(ROOT / rel_path, rel_path)
        for rel_dir in INCLUDE_DIRS:
            for path in sorted((ROOT / rel_dir).rglob("*.py")):
                archive.write(path, path.relative_to(ROOT))
    print(f"geschrieben: {output_path} ({output_path.stat().st_size} Bytes)")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "OgDocAddin.oxt"
    build(target)

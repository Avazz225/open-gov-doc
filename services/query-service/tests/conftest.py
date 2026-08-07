import sys
from pathlib import Path

import pytest

# `--import-mode=importlib` (workspace-weit, siehe pyproject.toml) verzichtet
# bewusst auf `tests/__init__.py`-Dateien - ein `tests.fixtures.x`-Dotted-
# Import waere daher nicht zuverlaessig aufloesbar. Stattdessen wird das
# `tests/fixtures`-Verzeichnis fuer die Dauer der Testsession direkt auf
# `sys.path` gelegt, damit `load_parser_plugin("fake_parser_plugin")`
# (flacher Modulname) genau den echten `importlib.import_module`-Pfad einer
# spaeteren echten Plugin-Installation nachbildet.
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _fixtures_on_syspath():
    sys.path.insert(0, str(FIXTURES_DIR))
    yield
    sys.path.remove(str(FIXTURES_DIR))

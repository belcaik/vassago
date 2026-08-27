"""Load the hyphen-named caldir scripts as importable modules.

The scripts read `os.environ` at import time (ROOT, GOOGLE_DIR and STATE_FILE
are module-level), so a test that needs its own calendar tree must set the env
vars first and then load a *fresh* module instance. `load_script` never caches:
each call re-executes the file.
"""

from __future__ import annotations

import importlib.util
import itertools
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CALDIR_DIR = REPO_ROOT / "caldir"

_counter = itertools.count()


def load_script(filename: str, env: dict | None = None):
    """Execute `caldir/<filename>` as a module, with `env` applied first."""
    if env:
        os.environ.update(env)

    path = CALDIR_DIR / filename
    stem = filename.replace("-", "_")
    if stem.endswith(".py"):
        stem = stem[: -len(".py")]
    name = "_vassago_%s_%d" % (stem, next(_counter))

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)

    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)

    return module


def write(path, text):
    """Write an iCalendar fixture with CRLF line endings, as the scripts do."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\n", "\r\n"), encoding="utf-8", newline="")
    return path

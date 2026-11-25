"""Where the data files live.

The package must work both from a source checkout (`pip install -e .`) and
from a normal install, where the code sits in site-packages and the repo's
`data/` folder is somewhere else (for example `/app/data` in the container).
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_DATA = Path(__file__).resolve().parents[2] / "data"


def data_dir() -> Path:
    """`$BROKER_DATA_DIR` if set, else the checkout's `data/`, else `./data`."""
    configured = os.environ.get("BROKER_DATA_DIR")
    if configured:
        return Path(configured)
    if _REPO_DATA.is_dir():
        return _REPO_DATA
    return Path.cwd() / "data"

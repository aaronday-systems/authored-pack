from __future__ import annotations

import sys
from pathlib import Path


# Ensure repo-root package imports (e.g., `import eps`) work under plain `pytest`
# without requiring a manual `PYTHONPATH=.`
ROOT = Path(__file__).resolve().parents[1]
root_s = str(ROOT)
if root_s not in sys.path:
    sys.path.insert(0, root_s)

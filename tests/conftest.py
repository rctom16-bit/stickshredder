"""Shared fixtures and sys.path setup for StickShredder tests."""

import sys
from pathlib import Path

# Make `src/` importable so `from core.config import ...` works.
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

"""Expose standalone scripts when unittest discovers this test package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

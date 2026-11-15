"""Fail-closed guard shared by tools that open restored data for writing."""
import json
from pathlib import Path

MARKER = {"version": 1, "purpose": "convergence-recovery-rehearsal", "production": False}

def require_disposable_clone(value, required=()):
    root = Path(value).absolute()
    if not root.is_dir() or root.resolve() != root:
        raise SystemExit("Recovery directory must be a real directory without symlinks")
    marker = root / ".disposable-recovery-clone.json"
    for entry in (marker, *root.rglob("*")):
        if entry.is_symlink():
            raise SystemExit("Recovery clone must not contain symlinks")
        if entry.is_dir():
            continue
        if not entry.is_file() or entry.stat().st_nlink != 1:
            raise SystemExit("Recovery requires regular, unlinked clone files and marker")
    if json.loads(marker.read_text()) != MARKER:
        raise SystemExit("Missing explicit disposable recovery clone authorization")
    for relative in required:
        if not (root / relative).exists():
            raise SystemExit("Required restored data is missing: " + relative)
    return root

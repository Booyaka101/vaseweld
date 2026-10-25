"""Replace generated files only after their complete contents have been written."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, *, newline: str | None = None) -> None:
    # Follow an existing symlink, as write_text does. The temporary file must be
    # beside the target so replacement never crosses filesystems.
    target = path.resolve()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline=newline, prefix=".vaseweld-",
            dir=target.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        if target.exists():
            temporary.chmod(target.stat().st_mode)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

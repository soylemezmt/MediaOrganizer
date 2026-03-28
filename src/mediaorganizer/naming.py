from pathlib import Path
from typing import Optional, Tuple

# ============================================================
# DESTINATION NAME HANDLING
# ============================================================

def resolve_destination_path(dest_dir: Path, original_name: str, source_size: int) -> Tuple[Optional[Path], str]:
    """
    Aynı isim ve aynı boyut varsa None döner -> skip
    Aynı isim ama farklı boyut varsa _01, _02 ekler
    """

    base = Path(original_name).stem
    ext = Path(original_name).suffix
    candidate = dest_dir / original_name

    if not candidate.exists():
        return candidate, "copy"

    try:
        if candidate.stat().st_size == source_size:
            return None, "skip_same_name_same_size"
    except Exception:
        pass

    index = 1
    while True:
        new_name = f"{base}_{index:02d}{ext}"
        new_path = dest_dir / new_name

        if not new_path.exists():
            return new_path, "copy_renamed"

        try:
            if new_path.stat().st_size == source_size:
                return None, "skip_same_name_same_size"
        except Exception:
            pass

        index += 1
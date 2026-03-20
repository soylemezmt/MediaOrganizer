from pathlib import Path
from .config import SUPPORTED_EXTENSIONS

def is_supported_media_file(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS
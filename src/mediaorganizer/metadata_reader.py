import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List

from .date_parsing import parse_date_string
from .config import EXIFTOOL_DATE_TAGS

# ============================================================
# EXIFTOOL SUPPORT
# ============================================================

def chunked(lst: List[Path], size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def read_metadata_dates_with_exiftool(files: List[Path]) -> Dict[Path, Optional[datetime]]:
    """
    ExifTool ile toplu metadata okur.
    ExifTool PATH'te yoksa boş döner.
    """
    result: Dict[Path, Optional[datetime]] = {f: None for f in files}

    try:
        subprocess.run(
            ["exiftool", "-ver"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
    except Exception:
        print("UYARI: exiftool bulunamadı. Metadata tarihi okunamayacak.")
        return result

    for group in chunked(files, 300):
        cmd = [
            "exiftool",
            "-j",
            "-n",
            "-DateTimeOriginal",
            "-CreateDate",
            "-MediaCreateDate",
            "-TrackCreateDate",
            "-CreationDate",
            "-ModifyDate",
            "-FileModifyDate",
        ] + [str(f) for f in group]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
            data = json.loads(proc.stdout)
        except Exception as e:
            print(f"UYARI: exiftool okuma hatası: {e}")
            continue

        for item in data:
            src = item.get("SourceFile")
            if not src:
                continue

            p = Path(src)
            chosen = None

            for tag in EXIFTOOL_DATE_TAGS:
                value = item.get(tag)
                if value:
                    chosen = parse_date_string(str(value))
                    if chosen:
                        break

            result[p] = chosen

    return result
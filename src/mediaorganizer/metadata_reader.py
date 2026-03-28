import json
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from .date_parsing import parse_date_string
from .config import EXIFTOOL_DATE_TAGS
from .exiftool_utils import exiftool_run, exiftool_run_with_files


def chunked(lst: List[Path], size: int):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _probe_exiftool() -> bool:
    try:
        probe = exiftool_run(["-ver"], check=False, text=True)
        return probe.returncode == 0
    except Exception:
        return False


def read_metadata_dates_with_exiftool(
    files: List[Path],
    selected_tag: str = "DateTimeOriginal",
) -> Dict[Path, Optional[datetime]]:
    result: Dict[Path, Optional[datetime]] = {f: None for f in files}

    if not files or not _probe_exiftool():
        return result

    for group in chunked(files, 100):
        

        try:
            proc = exiftool_run_with_files(
                [
                    "-j",
                    "-n",
                    "-DateTimeOriginal",
                    "-CreateDate",
                    "-MediaCreateDate",
                    "-TrackCreateDate",
                    "-CreationDate",
                    "-ModifyDate",
                    "-FileModifyDate",
                ],
                group,
                check=False,
                text=True,
            )

            if proc.stderr.strip():
                print(f"UYARI: exiftool stderr: {proc.stderr.strip()}")

            if not proc.stdout.strip():
                continue

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

            preferred_value = item.get(selected_tag)
            if preferred_value:
                chosen = parse_date_string(str(preferred_value))

            if chosen is None:
                for tag in EXIFTOOL_DATE_TAGS:
                    value = item.get(tag)
                    if value:
                        chosen = parse_date_string(str(value))
                        if chosen is not None:
                            break

            result[p] = chosen

    return result


def read_location_fields_with_exiftool(files: List[Path]) -> Dict[Path, Dict[str, Optional[str]]]:
    result: Dict[Path, Dict[str, Optional[str]]] = {
        f: {
            "country": None,
            "city": None,
            "gps_lat": None,
            "gps_lon": None,
        }
        for f in files
    }

    if not files or not _probe_exiftool():
        return result

    for group in chunked(files, 100):
        cmd = [
            "exiftool",
            "-charset", "filename=cp1254",
            "-j",
            "-n",
            "-Country",
            "-City",
            "-Location",
            "-Sub-location",
            "-GPSLatitude",
            "-GPSLongitude",
        ] + [str(f) for f in group]

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="cp1254",
                errors="replace",
                check=False,
            )

            if proc.stderr.strip():
                print(f"UYARI: exiftool stderr: {proc.stderr.strip()}")

            if not proc.stdout.strip():
                continue

            data = json.loads(proc.stdout)
        except Exception as e:
            print(f"UYARI: exiftool konum okuma hatası: {e}")
            continue

        for item in data:
            src = item.get("SourceFile")
            if not src:
                continue

            p = Path(src)
            result[p] = {
                "country": item.get("Country"),
                "city": item.get("City") or item.get("Location") or item.get("Sub-location"),
                "gps_lat": item.get("GPSLatitude"),
                "gps_lon": item.get("GPSLongitude"),
            }

    return result

def read_exiftool_date_fields(files: List[Path]) -> Dict[Path, Dict[str, Optional[str]]]:
    result: Dict[Path, Dict[str, Optional[str]]] = {
        f: {
            "DateTimeOriginal": None,
            "CreateDate": None,
            "MediaCreateDate": None,
            "TrackCreateDate": None,
            "CreationDate": None,
            "ModifyDate": None,
            "FileModifyDate": None,
        }
        for f in files
    }

    if not files or not _probe_exiftool():
        return result

    for group in chunked(files, 100):

        try:
            proc = exiftool_run_with_files(
                [
                    "-j",
                    "-DateTimeOriginal",
                    "-CreateDate",
                    "-MediaCreateDate",
                    "-TrackCreateDate",
                    "-CreationDate",
                    "-ModifyDate",
                    "-FileModifyDate",
                ],
                group,
                check=False,
                text=True,
            )

            if proc.stderr.strip():
                print(f"UYARI: exiftool stderr: {proc.stderr.strip()}")

            if not proc.stdout.strip():
                continue

            data = json.loads(proc.stdout)
        except Exception as e:
            print(f"UYARI: exiftool detay tarih okuma hatası: {e}")
            continue

        for item in data:
            src = item.get("SourceFile")
            if not src:
                continue

            p = Path(src)
            if p not in result:
                continue

            for tag in result[p].keys():
                value = item.get(tag)
                result[p][tag] = "" if value is None else str(value)

    return result
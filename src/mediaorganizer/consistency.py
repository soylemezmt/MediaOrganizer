import csv
import os

from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple

from .file_types import is_supported_media_file
from .date_parsing import extract_date_from_text
from .folder_date import extract_date_from_folder_hierarchy
from .metadata_reader import read_metadata_dates_with_exiftool
from .logging_utils import vprint


def collect_files(source_dir: Path) -> List[Path]:
    files = []
    for root, _, filenames in os.walk(source_dir):
        for fn in filenames:
            p = Path(root) / fn
            if is_supported_media_file(p):
                files.append(p)
    return files


def get_filesystem_date(path: Path) -> Optional[datetime]:
    try:
        stat = path.stat()
        try:
            return datetime.fromtimestamp(stat.st_ctime)
        except Exception:
            return datetime.fromtimestamp(stat.st_mtime)
    except Exception:
        return None


def get_all_date_sources(
    file_path: Path,
    metadata_date: Optional[datetime]
) -> Dict[str, Optional[datetime]]:
    return {
        "metadata": metadata_date,
        "filename": extract_date_from_text(file_path.name),
        "folder": extract_date_from_folder_hierarchy(file_path),
        "filesystem": get_filesystem_date(file_path),
    }


def normalize_date(
    dt: Optional[datetime],
    compare_level: str
) -> Optional[Tuple[int, ...]]:
    if dt is None:
        return None

    if compare_level == "year":
        return (dt.year,)
    if compare_level == "month":
        return (dt.year, dt.month)
    if compare_level == "day":
        return (dt.year, dt.month, dt.day)

    raise ValueError(f"Geçersiz compare_level: {compare_level}")


def format_datetime(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_normalized(norm: Optional[Tuple[int, ...]]) -> str:
    if norm is None:
        return ""

    if len(norm) == 1:
        return f"{norm[0]:04d}"
    if len(norm) == 2:
        return f"{norm[0]:04d}-{norm[1]:02d}"
    if len(norm) == 3:
        return f"{norm[0]:04d}-{norm[1]:02d}-{norm[2]:02d}"

    return ",".join(str(x) for x in norm)


def analyze_date_consistency(
    dates: Dict[str, Optional[datetime]],
    checked_sources: List[str],
    compare_level: str = "month"
) -> Tuple[
    bool,
    Dict[str, Optional[Tuple[int, ...]]],
    List[str],
    Dict[Tuple[int, ...], List[str]],
    str
]:
    """
    Döner:
      (
        is_inconsistent,
        normalized_values,
        non_empty_sources,
        grouped_sources,
        status
      )

    status:
      - ALL_EMPTY
      - ONLY_ONE_SOURCE
      - OK
      - INCONSISTENT
    """
    normalized: Dict[str, Optional[Tuple[int, ...]]] = {}
    non_empty_sources: List[str] = []
    grouped_sources: Dict[Tuple[int, ...], List[str]] = {}

    for src in checked_sources:
        dt = dates.get(src)
        norm = normalize_date(dt, compare_level)
        normalized[src] = norm

        if norm is not None:
            non_empty_sources.append(src)
            grouped_sources.setdefault(norm, []).append(src)

    if len(non_empty_sources) == 0:
        return False, normalized, non_empty_sources, grouped_sources, "ALL_EMPTY"

    if len(non_empty_sources) == 1:
        return False, normalized, non_empty_sources, grouped_sources, "ONLY_ONE_SOURCE"

    is_inconsistent = len(grouped_sources) > 1
    status = "INCONSISTENT" if is_inconsistent else "OK"

    return is_inconsistent, normalized, non_empty_sources, grouped_sources, status


def build_conflicting_sources_text(
    grouped_sources: Dict[Tuple[int, ...], List[str]]
) -> str:
    if len(grouped_sources) <= 1:
        return ""

    parts = []
    for norm_value, sources in sorted(grouped_sources.items()):
        norm_text = format_normalized(norm_value)
        src_text = "|".join(sources)
        parts.append(f"{norm_text}:[{src_text}]")
    return "; ".join(parts)


def find_inconsistent_files(
    source_dir: Path,
    checked_sources: List[str],
    output_csv: Path,
    compare_level: str = "month",
    verbose: bool = False,
    report_all: bool = False
) -> None:
    files = collect_files(source_dir)
    print(f"Toplam bulunan desteklenen dosya sayısı: {len(files)}")

    metadata_dates = read_metadata_dates_with_exiftool(files)

    inconsistent_count = 0
    checked_count = 0
    reported_count = 0

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "file_path",
            "metadata_date",
            "filename_date",
            "folder_date",
            "filesystem_date",
            "normalized_metadata",
            "normalized_filename",
            "normalized_folder",
            "normalized_filesystem",
            "checked_sources",
            "non_empty_sources",
            "compare_level",
            "distinct_value_count",
            "conflicting_sources",
            "status",
        ])

        for i, file_path in enumerate(files, start=1):
            dates = get_all_date_sources(
                file_path=file_path,
                metadata_date=metadata_dates.get(file_path)
            )

            is_inconsistent, normalized, non_empty_sources, grouped_sources, status = analyze_date_consistency(
                dates=dates,
                checked_sources=checked_sources,
                compare_level=compare_level
            )

            checked_count += 1

            if is_inconsistent:
                inconsistent_count += 1

            should_write = report_all or is_inconsistent

            if verbose:
                vprint(verbose, f"\n[FILE] {file_path}")
                for src in ["metadata", "filename", "folder", "filesystem"]:
                    vprint(verbose, f"  {src:10}: {dates[src]}")
                vprint(verbose, f"  checked         : {checked_sources}")
                vprint(verbose, f"  normalized      : { {k: format_normalized(v) for k, v in normalized.items()} }")
                vprint(verbose, f"  non-empty       : {non_empty_sources}")
                vprint(verbose, f"  grouped_sources : {grouped_sources}")
                vprint(verbose, f"  status          : {status}")
                vprint(verbose, f"  report_row      : {'YES' if should_write else 'NO'}")

            if should_write:
                reported_count += 1

                writer.writerow([
                    str(file_path),
                    format_datetime(dates["metadata"]),
                    format_datetime(dates["filename"]),
                    format_datetime(dates["folder"]),
                    format_datetime(dates["filesystem"]),
                    format_normalized(normalized.get("metadata")),
                    format_normalized(normalized.get("filename")),
                    format_normalized(normalized.get("folder")),
                    format_normalized(normalized.get("filesystem")),
                    ",".join(checked_sources),
                    ",".join(non_empty_sources),
                    compare_level,
                    len(grouped_sources),
                    build_conflicting_sources_text(grouped_sources),
                    status,
                ])

            if i % 200 == 0 or i == len(files):
                print(
                    f"İşlenen: {i}/{len(files)} | "
                    f"Kontrol edilen: {checked_count} | "
                    f"Tutarsız: {inconsistent_count} | "
                    f"CSV'ye yazılan: {reported_count}"
                )

    print("\nTarama tamamlandı.")
    print(f"Kontrol edilen dosya: {checked_count}")
    print(f"Tutarsız dosya:       {inconsistent_count}")
    print(f"CSV'ye yazılan satır: {reported_count}")
    print(f"CSV raporu:           {output_csv}")
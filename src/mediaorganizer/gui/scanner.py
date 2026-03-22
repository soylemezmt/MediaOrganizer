import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from mediaorganizer.file_types import is_supported_media_file
from mediaorganizer.consistency import get_all_date_sources, analyze_date_consistency
from mediaorganizer.metadata_reader import (
    read_metadata_dates_with_exiftool,
    read_location_fields_with_exiftool,
)

from .models import MediaRow
from .utils import fmt_year_month


def chunked(seq: list[Path], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class FolderScanner(QObject):
    scan_finished = Signal(list)
    scan_failed = Signal(str)
    progress_changed = Signal(int, int)

    @Slot(list, bool, object, object)
    def scan_folders(self, folders: list[str], recursive: bool, limit=None, options=None) -> None:
        try:
            folder_paths = [Path(f) for f in folders]
            media_files = self._collect_media_files(folder_paths, recursive, limit)

            metadata_tag = "DateTimeOriginal"
            filesystem_time = "ctime"
            show_country = False
            show_city = False

            if options is not None:
                metadata_tag = options.date_sources.metadata_tag
                filesystem_time = options.date_sources.filesystem_time
                show_country = options.columns.show_country
                show_city = options.columns.show_city

            checked_sources = ["metadata", "filename", "folder", "filesystem"]

            rows: list[MediaRow] = []
            total = len(media_files)
            processed = 0
            batch_size = 100

            if total == 0:
                self.progress_changed.emit(0, 0)
                self.scan_finished.emit(rows)
                return

            for group in chunked(media_files, batch_size):
                metadata_map = read_metadata_dates_with_exiftool(
                    group,
                    selected_tag=metadata_tag,
                )

                location_map = (
                    read_location_fields_with_exiftool(group)
                    if (show_country or show_city)
                    else {}
                )

                for p in group:
                    dates = get_all_date_sources(
                        p,
                        metadata_map.get(p),
                        filesystem_preferred=filesystem_time,
                    )

                    is_inconsistent, *_ = analyze_date_consistency(
                        dates=dates,
                        checked_sources=checked_sources,
                        compare_level="month",
                    )

                    loc = location_map.get(p, {})
                    country = str(loc.get("country") or loc.get("gps_lon") or "")
                    city = str(loc.get("city") or loc.get("gps_lat") or "")

                    rows.append(
                        MediaRow(
                            path=p,
                            file_type=p.suffix.lower(),
                            metadata_date=fmt_year_month(dates.get("metadata")),
                            filename_date=fmt_year_month(dates.get("filename")),
                            folder_date=fmt_year_month(dates.get("folder")),
                            filesystem_date=fmt_year_month(dates.get("filesystem")),
                            size_bytes=p.stat().st_size,
                            is_inconsistent=is_inconsistent,
                            country=country,
                            city=city,
                            full_path=str(p.resolve()),
                        )
                    )

                processed += len(group)
                self.progress_changed.emit(processed, total)

            rows.sort(key=lambda r: str(r.path).lower())
            self.scan_finished.emit(rows)

        except Exception as exc:
            self.scan_failed.emit(str(exc))

    def _collect_media_files(self, folders: list[Path], recursive: bool, limit=None) -> list[Path]:
        result: list[Path] = []
        seen: set[Path] = set()

        for folder in folders:
            if not folder.exists() or not folder.is_dir():
                continue

            if recursive:
                for root, _, filenames in os.walk(folder):
                    for filename in filenames:
                        p = Path(root) / filename
                        if is_supported_media_file(p):
                            resolved = p.resolve()
                            if resolved not in seen:
                                seen.add(resolved)
                                result.append(p)
                                if limit is not None and len(result) >= limit:
                                    return result
            else:
                for p in folder.iterdir():
                    if p.is_file() and is_supported_media_file(p):
                        resolved = p.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            result.append(p)
                            if limit is not None and len(result) >= limit:
                                return result

        return result
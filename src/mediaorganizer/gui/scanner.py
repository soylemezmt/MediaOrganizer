import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from mediaorganizer.file_types import is_supported_media_file
from mediaorganizer.consistency import (
    get_all_date_sources,
    analyze_date_consistency,
)
from mediaorganizer.metadata_reader import read_metadata_dates_with_exiftool

from .models import MediaRow
from .utils import fmt_year_month


class FolderScanner(QObject):
    scan_finished = Signal(list)
    scan_failed = Signal(str)

    @Slot(list, bool)
    def scan_folders(self, folders: list[str], recursive: bool) -> None:
        try:
            folder_paths = [Path(f) for f in folders]
            media_files = self._collect_media_files(folder_paths, recursive)

            metadata_map = read_metadata_dates_with_exiftool(media_files) if media_files else {}
            checked_sources = ["metadata", "filename", "folder", "filesystem"]

            rows: list[MediaRow] = []

            for p in media_files:
                try:
                    dates = get_all_date_sources(p, metadata_map.get(p))

                    is_inconsistent, *_ = analyze_date_consistency(
                        dates=dates,
                        checked_sources=checked_sources,
                        compare_level="month",
                    )

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
                        )
                    )
                except Exception:
                    rows.append(
                        MediaRow(
                            path=p,
                            file_type=p.suffix.lower(),
                            metadata_date="",
                            filename_date="",
                            folder_date="",
                            filesystem_date="",
                            size_bytes=0,
                            is_inconsistent=False,
                        )
                    )

            rows.sort(key=lambda r: str(r.path).lower())
            self.scan_finished.emit(rows)

        except Exception as exc:
            self.scan_failed.emit(str(exc))

    def _collect_media_files(self, folders: list[Path], recursive: bool) -> list[Path]:
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
            else:
                for p in folder.iterdir():
                    if p.is_file() and is_supported_media_file(p):
                        resolved = p.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            result.append(p)

        return result
import argparse
from pathlib import Path
import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

from mediaorganizer.file_types import is_supported_media_file
from mediaorganizer.metadata_reader import read_metadata_dates_with_exiftool
from mediaorganizer.date_parsing import extract_date_from_text
from mediaorganizer.folder_date import extract_date_from_folder_hierarchy
from mediaorganizer.naming import resolve_destination_path
from mediaorganizer.logging_utils import vprint

from mediaorganizer.config import DEFAULT_DATE_PRIORITY, UNKNOWN_FOLDER_NAME
from mediaorganizer.organizer import organize_files


def main():
    parser = argparse.ArgumentParser(
        description="Fotoğraf/video dosyalarını YYYY/MM klasör yapısına göre kopyalayarak organize eder."
    )
    parser.add_argument("source", help="Kaynak klasör")
    parser.add_argument("target", help="Hedef klasör")
    parser.add_argument("--dry-run", action="store_true", help="Gerçekte kopyalama yapmaz")
    parser.add_argument("--verbose", action="store_true", help="Detaylı log çıktısı")
    parser.add_argument(
        "--priority",
        default=",".join(DEFAULT_DATE_PRIORITY),
        help="Tarih öncelik sırası"
    )
    parser.add_argument("--log", default="organize_log.csv", help="CSV log dosyası")
    parser.add_argument(
        "--unknown-folder",
        default=UNKNOWN_FOLDER_NAME,
        help="Tarihi bulunamayan dosyaların gideceği klasör adı"
    )

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    target_dir = Path(args.target).resolve()
    log_csv = Path(args.log).resolve()

    priority = [x.strip() for x in args.priority.split(",") if x.strip()]

    organize_files(
        source_dir=source_dir,
        target_dir=target_dir,
        dry_run=args.dry_run,
        priority=priority,
        log_csv=log_csv,
        unknown_folder_name=args.unknown_folder,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
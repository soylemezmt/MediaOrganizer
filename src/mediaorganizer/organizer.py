import os
import csv
import shutil

from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

from .file_types import is_supported_media_file
from .metadata_reader import read_metadata_dates_with_exiftool
from .date_parsing import extract_date_from_text
from .folder_date import extract_date_from_folder_hierarchy
from .naming import resolve_destination_path
from .logging_utils import vprint
from .config import SUPPORTED_EXTENSIONS, DEFAULT_DATE_PRIORITY, UNKNOWN_FOLDER_NAME

# ============================================================
# MAIN ORGANIZER
# ============================================================

def get_filesystem_date(path: Path) -> Optional[datetime]:
    """
    Windows'ta st_ctime çoğu zaman creation time'dır.
    Güvenlik için önce ctime, olmazsa mtime kullanılır.
    """
    try:
        stat = path.stat()
        try:
            return datetime.fromtimestamp(stat.st_ctime)
        except Exception:
            return datetime.fromtimestamp(stat.st_mtime)
    except Exception:
        return None
    
    

def collect_files(source_dir: Path) -> List[Path]:
    files = []
    for root, _, filenames in os.walk(source_dir):
        for fn in filenames:
            p = Path(root) / fn
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(p)
    return files

# ============================================================
# DATE DECISION
# ============================================================

def decide_best_date(
    file_path: Path,
    metadata_date: Optional[datetime],
    priority: List[str],
    verbose: bool = False
) -> Tuple[Optional[datetime], str]:
    """
    priority örn: ["metadata", "filename", "folder", "filesystem"]
    """
    filename_date = extract_date_from_text(file_path.name)
    #folder_date = extract_date_from_text(str(file_path.parent))
    folder_date = extract_date_from_folder_hierarchy(file_path)
    filesystem_date = get_filesystem_date(file_path)

    sources = {
        "metadata": metadata_date,
        "filename": filename_date,
        "folder": folder_date,
        "filesystem": filesystem_date,
    }

    if verbose:
        print(f"\n[FILE] {file_path}")
        print(f"  metadata   : {metadata_date}")
        print(f"  filename   : {filename_date}")
        print(f"  folder     : {folder_date}")
        print(f"  filesystem : {filesystem_date}")
        print(f"  priority   : {priority}")

    for key in priority:
        dt = sources.get(key)
        if dt:
            return dt, key

    return None, "none"


def organize_files(
    source_dir: Path,
    target_dir: Path,
    dry_run: bool,
    priority: List[str],
    log_csv: Path,
    unknown_folder_name: str = UNKNOWN_FOLDER_NAME,
    verbose: bool = False
):
    files = collect_files(source_dir)
    print(f"Toplam bulunan desteklenen dosya sayısı: {len(files)}")

    metadata_dates = read_metadata_dates_with_exiftool(files)

    copied = 0
    skipped = 0
    errors = 0

    with open(log_csv, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "source_path",
            "file_size",
            "chosen_date",
            "date_source",
            "target_folder",
            "target_file",
            "action",
            "error"
        ])

        for i, file_path in enumerate(files, start=1):
            try:
                size = file_path.stat().st_size
                best_date, source_name = decide_best_date(
                    file_path=file_path,
                    metadata_date=metadata_dates.get(file_path),
                    priority=priority,
                    verbose=verbose
                )

                if best_date:
                    year_folder = f"{best_date.year:04d}"
                    month_folder = f"{best_date.month:02d}"
                    dest_folder = target_dir / year_folder / month_folder
                    chosen_date_str = best_date.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    dest_folder = target_dir / unknown_folder_name
                    chosen_date_str = ""

                dest_path, action = resolve_destination_path(dest_folder, file_path.name, size)
                vprint(verbose, f"  → hedef klasör: {dest_folder}")
                vprint(verbose, f"  → aksiyon: {action}")

                if dest_path is None:
                    skipped += 1
                    writer.writerow([
                        str(file_path),
                        size,
                        chosen_date_str,
                        source_name,
                        str(dest_folder),
                        "",
                        action,
                        ""
                    ])
                else:
                    if not dry_run:
                        dest_folder.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file_path, dest_path)
                    copied += 1
                    writer.writerow([
                        str(file_path),
                        size,
                        chosen_date_str,
                        source_name,
                        str(dest_folder),
                        str(dest_path),
                        action if not dry_run else f"DRYRUN_{action}",
                        ""
                    ])

                if i % 200 == 0 or i == len(files):
                    print(f"İşlenen: {i}/{len(files)} | Kopyalanan: {copied} | Atlanan: {skipped} | Hata: {errors}")

            except Exception as e:
                errors += 1
                writer.writerow([
                    str(file_path),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "error",
                    str(e)
                ])

    print("\nİşlem tamamlandı.")
    print(f"Kopyalanan: {copied}")
    print(f"Atlanan:    {skipped}")
    print(f"Hata:       {errors}")
    print(f"Log dosyası: {log_csv}")
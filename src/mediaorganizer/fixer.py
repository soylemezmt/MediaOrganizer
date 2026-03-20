import csv
import shutil
import subprocess
import os
import sys


from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Tuple

from .consistency import (
    collect_files,
    get_all_date_sources,
    analyze_date_consistency,
)
from .metadata_reader import read_metadata_dates_with_exiftool
from .naming import resolve_destination_path
from .logging_utils import vprint


def set_filesystem_times(file_path: Path, dt: datetime) -> Tuple[bool, str]:
    """
    Dosya sistemindeki zamanları günceller:
    - mtime
    - atime
    - Windows'ta creation time
    """
    try:
        ts = dt.timestamp()

        # mtime ve atime
        os.utime(file_path, (ts, ts))

        # Windows creation time
        if sys.platform.startswith("win"):
            import ctypes
            import ctypes.wintypes as wintypes

            FILE_WRITE_ATTRIBUTES = 0x0100

            handle = ctypes.windll.kernel32.CreateFileW(
                str(file_path),
                FILE_WRITE_ATTRIBUTES,
                0,
                None,
                3,
                0,
                None
            )

            if handle == -1:
                return False, "CreateFile failed"

            # Windows FILETIME = 100-nanosecond intervals since Jan 1, 1601
            import datetime as dtmod
            epoch = dtmod.datetime(1601, 1, 1)
            delta = dt - epoch
            filetime = int(delta.total_seconds() * 10**7)

            low = filetime & 0xFFFFFFFF
            high = filetime >> 32

            ft = wintypes.FILETIME(low, high)

            ctypes.windll.kernel32.SetFileTime(
                handle,
                ctypes.byref(ft),  # creation
                ctypes.byref(ft),  # access
                ctypes.byref(ft),  # write
            )

            ctypes.windll.kernel32.CloseHandle(handle)

        return True, ""

    except Exception as e:
        return False, str(e)


def write_metadata_with_exiftool(file_path: Path, dt: datetime, verbose=False) -> Tuple[bool, str]:
    """
    ExifTool ile metadata tarihlerini günceller.
    """
    dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")

    cmd = [
        "exiftool",
        "-overwrite_original",
        f"-DateTimeOriginal={dt_str}",
        f"-CreateDate={dt_str}",
        f"-ModifyDate={dt_str}",
        f"-MediaCreateDate={dt_str}",
        f"-TrackCreateDate={dt_str}",
        str(file_path)
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode == 0:
            if verbose:
                print(f"[METADATA UPDATED] {file_path}")
            return True, result.stdout.strip()
        else:
            return False, result.stderr.strip()

    except Exception as e:
        return False, str(e)


def choose_reference_date(
    dates: Dict[str, Optional[datetime]],
    checked_sources: List[str]
) -> Tuple[Optional[datetime], str]:
    """
    --check ile verilen sıraya göre ilk boş olmayan tarihi seçer.
    """
    for src in checked_sources:
        dt = dates.get(src)
        if dt is not None:
            return dt, src
    return None, "none"


def build_target_folder(root_dir: Path, dt: datetime) -> Path:
    return root_dir / f"{dt.year:04d}" / f"{dt.month:02d}"


def update_filename_date(
    original_name: str,
    target_dt: datetime
) -> Tuple[str, bool]:
    """
    Dosya adında tarih varsa onu düzeltmeye çalışır.
    Tarih yoksa ismi değiştirmez.

    Desteklenen bazı örnekler:
      IMG_20230715_142530.jpg
      2023-07-15 photo.jpg
      2023_07_15_xxx.jpg
      20230715.jpg
    """
    import re

    p = Path(original_name)
    stem = p.stem
    ext = p.suffix

    y = f"{target_dt.year:04d}"
    m = f"{target_dt.month:02d}"
    d = f"{target_dt.day:02d}"

    original_stem = stem

    patterns = [
        # YYYYMMDD_HHMMSS veya YYYYMMDD
        (r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', f"{y}{m}{d}"),
        # YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD
        (r'(?<!\d)(20\d{2})[-_.](\d{2})[-_.](\d{2})(?!\d)', f"{y}-{m}-{d}"),
        # YYYY-M-D benzeri daha gevşek format
        (r'(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})(?!\d)', f"{y}-{m}-{d}"),
    ]

    changed = False

    for pattern, replacement in patterns:
        new_stem, count = re.subn(pattern, replacement, stem, count=1)
        if count > 0:
            stem = new_stem
            changed = True
            break

    new_name = stem + ext
    return new_name, (changed and new_stem != original_stem)

def infer_archive_root(source_dir: Path) -> Path:
    """
    source_dir şu formatlarda olabilir:
      ...\YYYY\MM
      ...\YYYY
      ...\anything

    Amaç:
      YYYY veya YYYY\MM varsa kökü yukarı almak
    """

    try:
        name = source_dir.name
        parent = source_dir.parent.name

        # Case 1: ...\YYYY\MM
        if name.isdigit() and parent.isdigit():
            year = int(parent)
            month = int(name)

            if 1900 <= year <= 2100 and 1 <= month <= 12:
                return source_dir.parent.parent

        # Case 2: ...\YYYY
        if name.isdigit():
            year = int(name)
            if 1900 <= year <= 2100:
                return source_dir.parent

    except Exception:
        pass

    # Default: olduğu gibi bırak
    return source_dir


def fix_inconsistent_files(
    source_dir: Path,
    target_root: Path,
    checked_sources: List[str],
    compare_level: str,
    dry_run: bool,
    output_csv: Path,
    verbose: bool = False,
    rename_filename: bool = True,
    move_folder: bool = True,
    fix_metadata: bool = False,
    fix_filesystem: bool = False,
) -> None:
    files = collect_files(source_dir)
    print(f"Toplam bulunan desteklenen dosya sayısı: {len(files)}")

    metadata_dates = read_metadata_dates_with_exiftool(files)

    fixed_count = 0
    skipped_count = 0
    inconsistent_count = 0
    processed_count = 0

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([
            "source_path",
            "metadata_date",
            "filename_date",
            "folder_date",
            "filesystem_date",
            "checked_sources",
            "status",
            "reference_date",
            "reference_source",
            "target_folder",
            "target_filename",
            "target_path",
            "action",
            "metadata_action",
            "metadata_message",
            "filesystem_action",
            "filesystem_message",
            "error",
        ])

        for i, file_path in enumerate(files, start=1):
            processed_count += 1

            try:
                dates = get_all_date_sources(
                    file_path=file_path,
                    metadata_date=metadata_dates.get(file_path)
                )

                is_inconsistent, normalized, non_empty_sources, grouped_sources, status = analyze_date_consistency(
                    dates=dates,
                    checked_sources=checked_sources,
                    compare_level=compare_level
                )

                if not is_inconsistent:
                    skipped_count += 1
                    continue

                inconsistent_count += 1

                reference_date, reference_source = choose_reference_date(dates, checked_sources)

                if reference_date is None:
                    skipped_count += 1
                    writer.writerow([
                        str(file_path),
                        dates["metadata"].strftime("%Y-%m-%d %H:%M:%S") if dates["metadata"] else "",
                        dates["filename"].strftime("%Y-%m-%d %H:%M:%S") if dates["filename"] else "",
                        dates["folder"].strftime("%Y-%m-%d %H:%M:%S") if dates["folder"] else "",
                        dates["filesystem"].strftime("%Y-%m-%d %H:%M:%S") if dates["filesystem"] else "",
                        ",".join(checked_sources),
                        status,
                        "",
                        "none",
                        "",
                        "",
                        "",
                        "SKIP_NO_REFERENCE_DATE",
                        "",
                    ])
                    continue

                target_folder = file_path.parent
                if move_folder:
                    target_folder = build_target_folder(target_root, reference_date)

                target_filename = file_path.name
                filename_changed = False
                if rename_filename:
                    target_filename, filename_changed = update_filename_date(file_path.name, reference_date)

                file_size = file_path.stat().st_size
                target_path, collision_action = resolve_destination_path(
                    target_folder,
                    target_filename,
                    file_size
                )

                if target_path is None:
                    skipped_count += 1
                    writer.writerow([
                        str(file_path),
                        dates["metadata"].strftime("%Y-%m-%d %H:%M:%S") if dates["metadata"] else "",
                        dates["filename"].strftime("%Y-%m-%d %H:%M:%S") if dates["filename"] else "",
                        dates["folder"].strftime("%Y-%m-%d %H:%M:%S") if dates["folder"] else "",
                        dates["filesystem"].strftime("%Y-%m-%d %H:%M:%S") if dates["filesystem"] else "",
                        ",".join(checked_sources),
                        status,
                        reference_date.strftime("%Y-%m-%d %H:%M:%S"),
                        reference_source,
                        str(target_folder),
                        target_filename,
                        "",
                        "SKIP_DUPLICATE",
                        "",
                    ])
                    continue

                same_path = file_path.resolve() == target_path.resolve()

                if verbose:
                    vprint(verbose, f"\n[FIX] {file_path}")
                    vprint(verbose, f"  status           : {status}")
                    vprint(verbose, f"  reference_date   : {reference_date}")
                    vprint(verbose, f"  reference_source : {reference_source}")
                    vprint(verbose, f"  target_folder    : {target_folder}")
                    vprint(verbose, f"  target_filename  : {target_filename}")
                    vprint(verbose, f"  collision_action : {collision_action}")
                    vprint(verbose, f"  same_path        : {same_path}")

                # Eğer isim ve klasör aynı kalıyorsa yapacak iş yok
                if same_path:
                    skipped_count += 1
                    writer.writerow([
                        str(file_path),
                        dates["metadata"].strftime("%Y-%m-%d %H:%M:%S") if dates["metadata"] else "",
                        dates["filename"].strftime("%Y-%m-%d %H:%M:%S") if dates["filename"] else "",
                        dates["folder"].strftime("%Y-%m-%d %H:%M:%S") if dates["folder"] else "",
                        dates["filesystem"].strftime("%Y-%m-%d %H:%M:%S") if dates["filesystem"] else "",
                        ",".join(checked_sources),
                        status,
                        reference_date.strftime("%Y-%m-%d %H:%M:%S"),
                        reference_source,
                        str(target_folder),
                        target_filename,
                        str(target_path),
                        "SKIP_ALREADY_MATCHES_TARGET",
                        "",
                    ])
                    continue

                action = "DRYRUN_MOVE_RENAME" if dry_run else "MOVE_RENAME"

                if not dry_run:
                    target_folder.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(file_path), str(target_path))

                fixed_count += 1
                metadata_result = ""
                metadata_status = ""

                if fix_metadata and not dry_run:
                    success, msg = write_metadata_with_exiftool(target_path, reference_date, verbose)

                    metadata_status = "UPDATED" if success else "FAILED"
                    metadata_result = msg

                filesystem_status = ""
                filesystem_msg = ""

                if fix_filesystem and not dry_run:
                    success, msg = set_filesystem_times(target_path, reference_date)

                    filesystem_status = "UPDATED" if success else "FAILED"
                    filesystem_msg = msg

                writer.writerow([
                    str(file_path),
                    dates["metadata"].strftime("%Y-%m-%d %H:%M:%S") if dates["metadata"] else "",
                    dates["filename"].strftime("%Y-%m-%d %H:%M:%S") if dates["filename"] else "",
                    dates["folder"].strftime("%Y-%m-%d %H:%M:%S") if dates["folder"] else "",
                    dates["filesystem"].strftime("%Y-%m-%d %H:%M:%S") if dates["filesystem"] else "",
                    ",".join(checked_sources),
                    status,
                    reference_date.strftime("%Y-%m-%d %H:%M:%S"),
                    reference_source,
                    str(target_folder),
                    target_filename,
                    str(target_path),                    
                    action,
                    metadata_status,
                    metadata_result,
                    filesystem_status,
                    filesystem_msg,
                    "",
                ])

            except Exception as e:
                skipped_count += 1
                writer.writerow([
                    str(file_path),
                    "",
                    "",
                    "",
                    "",
                    ",".join(checked_sources),
                    "ERROR",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "ERROR",
                    str(e),
                ])

            if i % 200 == 0 or i == len(files):
                print(
                    f"İşlenen: {i}/{len(files)} | "
                    f"Uyumsuz: {inconsistent_count} | "
                    f"Düzeltilen: {fixed_count} | "
                    f"Atlanan: {skipped_count}"
                )

    print("\nİşlem tamamlandı.")
    print(f"Kontrol edilen dosya: {processed_count}")
    print(f"Uyumsuz dosya:        {inconsistent_count}")
    print(f"Düzeltilen dosya:     {fixed_count}")
    print(f"Atlanan dosya:        {skipped_count}")
    print(f"CSV raporu:           {output_csv}")
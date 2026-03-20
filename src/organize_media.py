import os
import re
import csv
import json
import shutil
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from tabnanny import verbose
from typing import Optional, Dict, List, Tuple

# ============================================================
# CONFIGURATION
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm"
}

# Tarih öncelik sırası: değiştirebilirsiniz
DATE_PRIORITY = ["metadata", "filename", "folder", "filesystem"]


# Metadata içinde bakılacak alanlar (öncelik sırasıyla)
EXIFTOOL_DATE_TAGS = [
    "DateTimeOriginal",
    "CreateDate",
    "MediaCreateDate",
    "TrackCreateDate",
    "CreationDate",
    "ModifyDate",
    "FileModifyDate",
]

UNKNOWN_FOLDER_NAME = "UNKNOWN_DATE"


# ============================================================
# DATE PARSING HELPERS
# ============================================================

def parse_date_string(text: str) -> Optional[datetime]:
    """
    Çok farklı tarih formatlarını yakalamaya çalışır.
    Yalnızca yıl/ay bilgisi bile varsa gün=1 kabul eder.
    """
    if not text:
        return None

    text = text.strip()

    # Sık görülen EXIF / exiftool formatları
    known_formats = [
        "%Y:%m:%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y:%m:%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
        "%Y%m%d_%H%M%S",
        "%Y-%m",
        "%Y/%m",
        "%Y%m",
    ]

    for fmt in known_formats:
        try:
            dt = datetime.strptime(text, fmt)
            return dt
        except ValueError:
            pass

    # Fazladan timezone bilgileri / alt saniyeler / Z son eki gibi durumlar
    cleaned = text.replace("Z", "+0000")
    cleaned = re.sub(r"(\.\d+)", "", cleaned)  # fractional seconds kaldır
    cleaned = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", cleaned)  # +03:00 -> +0300

    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S%z",
    ]:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return dt
        except ValueError:
            pass

    return None


def extract_date_from_text(text: str) -> Optional[datetime]:
    """
    Dosya adı veya klasör yolundan tarih yakalamaya çalışır.
    Örn:
    20240315
    2024-03-15
    2024_03_15
    IMG_20240315_142530
    2024-03
    2024/03
    """
    if not text:
        return None

    candidates = []

    patterns = [
        # 20240315_142530 veya 20240315142530
        r'(?<!\d)(20\d{2})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})(?!\d)',
        # 2024-03-15 14-25-30 / 2024_03_15_14_25_30
        r'(?<!\d)(20\d{2})[-_./](\d{2})[-_./](\d{2})[ T_\-]?(\d{2})[:._\-]?(\d{2})[:._\-]?(\d{2})(?!\d)',
        # 20240315
        r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)',
        # 2024-03-15 / 2024_03_15 / 2024.03.15
        r'(?<!\d)(20\d{2})[-_./](\d{1,2})[-_./](\d{1,2})(?!\d)',
        # 2024-03 / 2024_03
        r'(?<!\d)(20\d{2})[-_./](\d{1,2})(?!\d)',
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, text):
            groups = m.groups()
            try:
                if len(groups) == 6:
                    y, mo, d, h, mi, s = map(int, groups)
                    candidates.append(datetime(y, mo, d, h, mi, s))
                elif len(groups) == 3:
                    y, mo, d = map(int, groups)
                    candidates.append(datetime(y, mo, d))
                elif len(groups) == 2:
                    y, mo = map(int, groups)
                    candidates.append(datetime(y, mo, 1))
            except ValueError:
                pass

    if not candidates:
        return None

    # En uzun/eşleşmesi en spesifik olan adaylar önce geldiği için ilkini alıyoruz
    return candidates[0]

def vprint(verbose: bool, message: str):
    if verbose:
        print(message)


def extract_date_from_folder_hierarchy(path: Path) -> Optional[datetime]:
    """
    Dosyanın bulunduğu klasörden başlayarak üst klasörleri inceler.

    Öncelik:
    1. Tek bir klasör adında doğrudan tarih aranır
       Örn: '2019-01-20 - 2019-01-20'
    2. Ardışık klasörlerde yıl/ay yapısı aranır
       Örn: '2019\\01' veya '2022\\12'
    """
    current = path.parent

    while True:
        # 1) Önce klasör adının kendisinde tarih ara
        candidate = extract_date_from_text(current.name)
        if candidate:
            return candidate

        # 2) Sonra parent/current ikilisinin yıl/ay yapısı olup olmadığını kontrol et
        parent = current.parent
        if parent != current:
            year_match = re.fullmatch(r"(19|20)\d{2}", parent.name)
            month_match = re.fullmatch(r"(0?[1-9]|1[0-2])", current.name)

            if year_match and month_match:
                year = int(parent.name)
                month = int(current.name)
                return datetime(year, month, 1)

        if current.parent == current:
            break

        current = current.parent

    return None


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


# ============================================================
# DESTINATION NAME HANDLING
# ============================================================

def resolve_destination_path(dest_dir: Path, original_name: str, source_size: int) -> Tuple[Optional[Path], str]:
    """
    Aynı isim ve aynı boyut varsa None döner -> skip
    Aynı isim ama farklı boyut varsa _01, _02 ekler
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

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


# ============================================================
# MAIN ORGANIZER
# ============================================================

def collect_files(source_dir: Path) -> List[Path]:
    files = []
    for root, _, filenames in os.walk(source_dir):
        for fn in filenames:
            p = Path(root) / fn
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(p)
    return files


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


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fotoğraf/video dosyalarını YYYY/MM klasör yapısına göre kopyalayarak organize eder."
    )
    parser.add_argument("source", help="Kaynak klasör")
    parser.add_argument("target", help="Hedef klasör")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gerçekte kopyalama yapmaz, sadece ne yapılacağını loglar"
    )
    parser.add_argument(
        "--priority",
        default="metadata,filename,folder,filesystem",
        help="Tarih öncelik sırası. Örn: metadata,filename,folder,filesystem"
    )
    parser.add_argument(
        "--log",
        default="organize_log.csv",
        help="CSV log dosyası adı"
    )
    parser.add_argument(
        "--unknown-folder",
        default=UNKNOWN_FOLDER_NAME,
        help="Tarihi bulunamayan dosyaların gideceği klasör adı"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Detaylı log çıktısı"
    )

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    target_dir = Path(args.target).resolve()
    log_csv = Path(args.log).resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Kaynak klasör bulunamadı: {source_dir}")

    priority = [x.strip() for x in args.priority.split(",") if x.strip()]
    allowed = {"metadata", "filename", "folder", "filesystem"}

    if not priority or any(p not in allowed for p in priority):
        raise SystemExit(
            "Geçersiz priority değeri. Geçerli seçenekler: metadata, filename, folder, filesystem"
        )

    organize_files(
        source_dir=source_dir,
        target_dir=target_dir,
        dry_run=args.dry_run,
        priority=priority,
        log_csv=log_csv,
        unknown_folder_name=args.unknown_folder,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
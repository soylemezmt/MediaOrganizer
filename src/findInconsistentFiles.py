import argparse
from pathlib import Path

from mediaorganizer.consistency import find_inconsistent_files


def main():
    parser = argparse.ArgumentParser(
        description="Seçilen tarih kaynakları arasında uyumsuzluk olan medya dosyalarını bulur."
    )

    parser.add_argument("source", help="Kontrol edilecek kök klasör")

    parser.add_argument(
        "--check",
        default="metadata,filename,folder,filesystem",
        help="Kontrol edilecek kaynaklar. "
             "Örn: metadata,filename,folder,filesystem"
    )

    parser.add_argument(
        "--compare-level",
        default="month",
        choices=["year", "month", "day"],
        help="Karşılaştırma seviyesi: year, month veya day"
    )

    parser.add_argument(
        "--output",
        default="inconsistent_files.csv",
        help="Çıktı CSV dosyası"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Detaylı çıktı ver"
    )

    parser.add_argument(
        "--report-all",
        action="store_true",
        help="Sadece uyumsuzları değil, tüm dosyaları CSV'ye yaz"
    )

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    output_csv = Path(args.output).resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"Kaynak klasör bulunamadı: {source_dir}")

    checked_sources = [x.strip() for x in args.check.split(",") if x.strip()]
    allowed = {"metadata", "filename", "folder", "filesystem"}

    if not checked_sources or any(x not in allowed for x in checked_sources):
        raise SystemExit(
            "Geçersiz --check değeri. Geçerli seçenekler: "
            "metadata, filename, folder, filesystem"
        )

    find_inconsistent_files(
        source_dir=source_dir,
        checked_sources=checked_sources,
        output_csv=output_csv,
        compare_level=args.compare_level,
        verbose=args.verbose,
        report_all=args.report_all
    )


if __name__ == "__main__":
    main()
import argparse
from pathlib import Path

from mediaorganizer.fixer import fix_inconsistent_files


def main():
    parser = argparse.ArgumentParser(
        description="Uyumsuz medya dosyalarını, --check sırasındaki ilk boş olmayan tarih bilgisine göre düzeltir."
    )

    parser.add_argument("source", help="Kök klasör")

    parser.add_argument(
        "--check",
        default="metadata,filename,folder,filesystem",
        help="Karşılaştırılacak ve referans seçiminde kullanılacak kaynaklar. "
             "Sıra önemlidir. Örn: metadata,filename,folder,filesystem"
    )

    parser.add_argument(
        "--compare-level",
        default="month",
        choices=["year", "month", "day"],
        help="Uyumsuzluk tespit seviyesi"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gerçek taşıma/yeniden adlandırma yapmadan sadece rapor üretir"
    )

    parser.add_argument(
        "--output",
        default="fix_inconsistent_files.csv",
        help="CSV log dosyası"
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Detaylı çıktı ver"
    )

    parser.add_argument(
        "--no-rename-filename",
        action="store_true",
        help="Dosya adı düzeltilmesin"
    )

    parser.add_argument(
        "--no-move-folder",
        action="store_true",
        help="Dosya klasörü düzeltilmesin"
    )
    
    parser.add_argument(
        "--target-root",
        default=None,
        help="Düzeltme sonrası YYYY/MM klasörlerinin oluşturulacağı kök klasör. "
            "Verilmezse source klasörü kullanılır."
    )
    
    parser.add_argument(
    "--fix-metadata",
    action="store_true",
    help="Uyumsuz dosyalarda metadata tarihini referans tarihe göre düzelt"
)
    
    parser.add_argument(
    "--fix-filesystem",
    action="store_true",
    help="Dosya sistemindeki tarihleri (creation, modified, access) referans tarihe göre düzelt"
)

    args = parser.parse_args()
    from mediaorganizer.fixer import infer_archive_root
    source_dir = Path(args.source).resolve()
    fix_metadata=args.fix_metadata
    fix_filesystem=args.fix_filesystem
    
    if args.target_root:
        target_root = Path(args.target_root).resolve()
    else:
        target_root = infer_archive_root(source_dir)
        print(f"[INFO] Otomatik target root belirlendi: {target_root}")

    
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

    fix_inconsistent_files(
        source_dir=source_dir,
        target_root=target_root,
        checked_sources=checked_sources,
        compare_level=args.compare_level,
        dry_run=args.dry_run,
        output_csv=output_csv,
        verbose=args.verbose,
        rename_filename=not args.no_rename_filename,
        move_folder=not args.no_move_folder,
        fix_metadata=fix_metadata,
        fix_filesystem=fix_filesystem
    )


if __name__ == "__main__":
    main()
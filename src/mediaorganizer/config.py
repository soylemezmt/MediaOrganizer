# ============================================================
# CONFIGURATION
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif",
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m2ts", ".3gp", ".wmv", ".webm",".mpg", ".mpeg"
}

# Tarih öncelik sırası: değiştirebilirsiniz
DEFAULT_DATE_PRIORITY = ["metadata", "filename", "folder", "filesystem"]

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


# Tarihi bulunamayan dosyaların gideceği klasör adı
UNKNOWN_FOLDER_NAME = "UNKNOWN_DATE"
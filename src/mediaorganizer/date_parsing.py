import re
from datetime import datetime
from typing import Optional

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
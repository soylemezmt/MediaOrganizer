import re
from datetime import datetime
from typing import Optional

# ============================================================
# DATE PARSING HELPERS
# ============================================================

YEAR_RE = r"(1[89]\d{2}|20\d{2})"


def parse_date_string(text: str) -> Optional[datetime]:
    """
    Çok farklı tarih formatlarını yakalamaya çalışır.
    Yalnızca yıl/ay bilgisi bile varsa gün=1 kabul eder.
    """
    if not text:
        return None

    text = text.strip()

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
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    cleaned = text.replace("Z", "+0000")
    cleaned = re.sub(r"(\.\d+)", "", cleaned)
    cleaned = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", cleaned)

    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y:%m:%d %H:%M:%S%z",
    ]:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            pass

    return None


def extract_date_from_text(text: str) -> Optional[datetime]:
    """
    Dosya adı veya klasör yolundan tarih yakalamaya çalışır.

    Desteklenen örnekler:
    20240315
    2024-03-15
    2024_03_15
    IMG_20240315_142530
    2024-03
    2024/03

    Ek özel durumlar:
    YYYY_MM_DD
    YYYY_MM_      -> gün varsayılan 15
    ^YYYY_...     -> ay=07, gün=15
    ^YYYx_...     -> x -> 5 kabul edilir, ay=07, gün=15

    Ayrıca 19xx ve 20xx yılları desteklenir.
    """
    if not text:
        return None

    candidates: list[datetime] = []

    year_re = r"(1[89]\d{2}|20\d{2})"

    patterns = [
        # ÖZEL: dosya adı başında "YYYY_" -> yıl alınır, ay=07, gün=15
        (rf'^{year_re}_', "y_prefix_default"),

        # ÖZEL: dosya adı başında "YYYx_" -> x son hanede ise 5 kabul edilir
        (r'^((?:1[89]\d|20\d))x_', "yx_prefix_default"),

        # ÖZEL: YYYY_MM_DD
        (rf'(?<!\d){year_re}_(0[1-9]|1[0-2])_(0[1-9]|[12]\d|3[01])(?!\d)', "ymd"),

        # ÖZEL: YYYY_MM_ -> gün varsayılan 15
        (rf'(?<!\d){year_re}_(0[1-9]|1[0-2])_(?!\d)', "ym_default_day"),

        # YYYYMMDD_HHMMSS veya YYYYMMDDHHMMSS
        (rf'(?<!\d){year_re}(\d{{2}})(\d{{2}})[_\- ]?(\d{{2}})(\d{{2}})(\d{{2}})(?!\d)', "ymdhms_compact"),

        # YYYY-MM-DD 14-25-30 / YYYY_MM_DD_14_25_30
        (rf'(?<!\d){year_re}[-_./](\d{{2}})[-_./](\d{{2}})[ T_\-]?(\d{{2}})[:._\-]?(\d{{2}})[:._\-]?(\d{{2}})(?!\d)', "ymdhms_sep"),

        # YYYYMMDD
        (rf'(?<!\d){year_re}(\d{{2}})(\d{{2}})(?!\d)', "ymd_compact"),

        # YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD
        (rf'(?<!\d){year_re}[-_./](\d{{1,2}})[-_./](\d{{1,2}})(?!\d)', "ymd_sep"),

        # YYYY-MM / YYYY_MM / YYYY/MM
        (rf'(?<!\d){year_re}[-_./](\d{{1,2}})(?!\d)', "ym"),
    ]

    for pattern, kind in patterns:
        for m in re.finditer(pattern, text):
            groups = m.groups()
            try:
                if kind == "y_prefix_default":
                    y = int(groups[0])
                    candidates.append(datetime(y, 7, 15))

                elif kind == "yx_prefix_default":
                    # örn: 194x_ -> 1945
                    y = int(groups[0] + "5")
                    candidates.append(datetime(y, 7, 15))

                elif kind == "ym_default_day":
                    y = int(groups[0])
                    mo = int(groups[1])
                    candidates.append(datetime(y, mo, 15))

                elif kind == "ymdhms_compact":
                    y = int(groups[0])
                    mo = int(groups[1])
                    d = int(groups[2])
                    h = int(groups[3])
                    mi = int(groups[4])
                    s = int(groups[5])
                    candidates.append(datetime(y, mo, d, h, mi, s))

                elif kind == "ymdhms_sep":
                    y = int(groups[0])
                    mo = int(groups[1])
                    d = int(groups[2])
                    h = int(groups[3])
                    mi = int(groups[4])
                    s = int(groups[5])
                    candidates.append(datetime(y, mo, d, h, mi, s))

                elif kind == "ymd_compact":
                    y = int(groups[0])
                    mo = int(groups[1])
                    d = int(groups[2])
                    candidates.append(datetime(y, mo, d))

                elif kind == "ymd_sep":
                    y = int(groups[0])
                    mo = int(groups[1])
                    d = int(groups[2])
                    candidates.append(datetime(y, mo, d))

                elif kind == "ym":
                    y = int(groups[0])
                    mo = int(groups[1])
                    candidates.append(datetime(y, mo, 1))

            except ValueError:
                pass

    if not candidates:
        return None

    return candidates[0]
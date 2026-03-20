import re
from pathlib import Path
from datetime import datetime
from typing import Optional
from .date_parsing import extract_date_from_text

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
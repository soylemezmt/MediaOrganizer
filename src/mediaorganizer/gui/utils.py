from mediaorganizer.consistency import normalize_date


def fmt_year_month(dt) -> str:
    if dt is None:
        return ""
    try:
        norm = normalize_date(dt, "month")
        if norm is None:
            return ""
        return f"{norm[0]:04d}-{norm[1]:02d}"
    except Exception:
        return ""
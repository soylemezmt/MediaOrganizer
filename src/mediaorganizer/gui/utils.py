from mediaorganizer.consistency import normalize_date
import socket


def check_internet_connection(timeout: float = 1.5) -> bool:
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=timeout)
        return True
    except Exception:
        return False

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
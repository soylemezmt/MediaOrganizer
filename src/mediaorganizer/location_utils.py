from functools import lru_cache
from typing import Optional, Tuple

from geopy.geocoders import Nominatim


_geolocator = Nominatim(user_agent="mediaorganizer")


def _to_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


@lru_cache(maxsize=2048)
def reverse_geocode_country_city(lat: float, lon: float) -> Tuple[str, str]:
    try:
        location = _geolocator.reverse((lat, lon), language="en", exactly_one=True)
        if not location or not getattr(location, "raw", None):
            return "", ""

        address = location.raw.get("address", {})
        country = address.get("country", "") or ""
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
            or ""
        )
        return country, city
    except Exception:
        return "", ""


def infer_country_city_from_gps(gps_lat, gps_lon) -> Tuple[str, str]:
    lat = _to_float(gps_lat)
    lon = _to_float(gps_lon)
    if lat is None or lon is None:
        return "", ""
    return reverse_geocode_country_city(lat, lon)
from math import asin, cos, radians, sin, sqrt


def haversine(lon1, lat1, lon2, lat2):
    """
    Calculate the great circle distance between two points
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    # haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    # Radius of earth in kilometers is 6371
    km = 6371 * c

    return km


def get_language_code(language: str = None) -> str:
    """
    Get the language code for the given language.
    """
    language_codes = {
        "pl": "pl-PL",
        "en": "en-US",
        "__default__": "en-US",
    }
    return language_codes.get(language, language_codes["__default__"])

import base64
import json
import re
import time
from math import asin, cos, radians, sin, sqrt
from typing import Any, Optional


def decode_jwt_payload(token: str) -> Optional[dict]:
    """Decode the payload from a JWT token without verification.

    Args:
        token: JWT token string.

    Returns:
        Decoded payload as dictionary, or None if decoding fails.
    """
    try:
        # JWT format: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None

        # Decode the payload (second part)
        payload_b64 = parts[1]
        # Add padding if needed (base64 requires padding to be multiple of 4)
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def is_token_expiring_soon(
    token: str,
    buffer_seconds: int = 600,
) -> bool:
    """Check if a JWT token is about to expire.

    Args:
        token: JWT access token string.
        buffer_seconds: Time buffer in seconds before expiration
                        to consider token as "expiring soon". Default is 600 (10 minutes).

    Returns:
        True if token is expiring within buffer_seconds or is already expired,
        False if token is still valid beyond the buffer period.
        Returns True if token cannot be decoded (fail-safe behavior).
    """
    payload = decode_jwt_payload(token)
    if payload is None:
        # If we can't decode the token, assume it's expiring to trigger refresh
        return True

    exp = payload.get("exp")
    if exp is None:
        # No expiration claim, assume it's expiring
        return True

    current_time = time.time()
    # Token is "expiring soon" if current time + buffer >= expiration time
    return current_time + buffer_seconds >= exp


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    Args:
        name: String in camelCase format.

    Returns:
        String in snake_case format.
    """
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def convert_keys_to_snake_case(data: Any) -> Any:
    """Recursively convert dictionary keys from camelCase to snake_case.

    Args:
        data: Dictionary, list, or value to convert.

    Returns:
        Data structure with converted keys.
    """
    if isinstance(data, dict):
        return {
            camel_to_snake(k): convert_keys_to_snake_case(v) for k, v in data.items()
        }
    elif isinstance(data, list):
        return [convert_keys_to_snake_case(item) for item in data]
    return data


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

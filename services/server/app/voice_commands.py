"""Strict parser for the initial voice-command vocabulary."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional


class VoiceCommandError(ValueError):
    """Raised when a voice command is incomplete, ambiguous, or unsupported."""


_NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
}

_ORDINAL_WORDS = {
    "first": "1st",
    "second": "2nd",
    "third": "3rd",
    "fourth": "4th",
    "fifth": "5th",
    "sixth": "6th",
    "seventh": "7th",
    "eighth": "8th",
    "ninth": "9th",
    "tenth": "10th",
}


def _tokens(value: str) -> list[str]:
    camel_split = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    return re.findall(r"-?\d+(?:\.\d+)?(?:st|nd|rd|th)?|[a-z]+", camel_split.lower())


def _vehicle_alias(vehicle_id: str) -> tuple[str, ...]:
    tokens = _tokens(vehicle_id.replace("-", " "))
    return tuple(_canonical_vehicle_token(token) for token in tokens)


def _canonical_vehicle_token(token: str) -> str:
    if token in ("jr", "junior"):
        return "junior"
    token = _ORDINAL_WORDS.get(token, token)
    ordinal_or_number = re.fullmatch(r"(\d+)(?:st|nd|rd|th)?", token)
    if ordinal_or_number:
        return str(int(ordinal_or_number.group(1)))
    if token in _NUMBER_WORDS:
        return str(_NUMBER_WORDS[token])
    return token


def _number(text: str) -> Optional[float]:
    if text.isdigit():
        return float(text)
    words = text.replace("-", " ").split()
    if len(words) == 2 and words[0] in ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety") and words[1] in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[words[0]] + _NUMBER_WORDS[words[1]])
    if len(words) == 2 and words == ["one", "hundred"]:
        return 100.0
    return float(_NUMBER_WORDS[words[0]]) if len(words) == 1 and words[0] in _NUMBER_WORDS else None


def _find_vehicle(text_tokens: list[str], vehicles: Iterable[dict[str, Any]]) -> tuple[dict[str, Any], int, int]:
    matches: list[tuple[dict[str, Any], int, int]] = []
    canonical_tokens = [_canonical_vehicle_token(token) for token in text_tokens]
    for vehicle in vehicles:
        vehicle_id = str(vehicle.get("vehicle_id") or "")
        alias = _vehicle_alias(vehicle_id)
        if not alias:
            continue
        for start in range(len(canonical_tokens) - len(alias) + 1):
            if tuple(canonical_tokens[start : start + len(alias)]) == alias:
                matches.append((vehicle, start, start + len(alias)))
                break
    if not matches:
        raise VoiceCommandError("Name a connected vehicle by its vehicle ID.")
    longest = max(end - start for _, start, end in matches)
    matches = [match for match in matches if match[2] - match[1] == longest]
    if len(matches) != 1:
        raise VoiceCommandError("The vehicle name is ambiguous. Say its full vehicle ID.")
    return matches[0]


def _spoken_number_pattern() -> str:
    tens = r"(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    ones = r"(?:one|two|three|four|five|six|seven|eight|nine)"
    words = "|".join(_NUMBER_WORDS)
    return rf"(?:\d+(?:\.\d+)?|{tens}[ -]{ones}|one hundred|{words})"


def _extract_measurement(text: str, subject: str) -> float:
    number = _spoken_number_pattern()
    match = re.search(
        rf"\b{number}\s*(?:meters?|metres?|m)\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        raise VoiceCommandError(f"Specify the {subject} in meters.")
    value_match = re.match(number, match.group(0), re.IGNORECASE)
    value = _number(value_match.group(0).lower()) if value_match else None
    if value is None:
        try:
            value = float(value_match.group(0))
        except (AttributeError, ValueError):
            value = None
    if value is None or value <= 0:
        raise VoiceCommandError(f"The {subject} must be a positive number of meters.")
    return value


def _coordinates(text: str) -> Optional[tuple[float, float]]:
    match = re.search(
        r"(-?\d{1,2}(?:\.\d+)?)\s*(?:,|\band\b|\s)\s*(-?\d{1,3}(?:\.\d+)?)",
        text,
    )
    if not match:
        return None
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise VoiceCommandError("The spoken latitude or longitude is out of range.")
    return latitude, longitude


def _location(
    text: str,
    selected_location: Optional[dict[str, float]],
) -> tuple[float, float]:
    spoken = _coordinates(text)
    if spoken:
        return spoken
    if selected_location and re.search(
        r"\b(?:here|there|this point|that point|selected point|map point|location)\b",
        text,
        re.IGNORECASE,
    ):
        latitude = float(selected_location.get("latitude", float("nan")))
        longitude = float(selected_location.get("longitude", float("nan")))
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            return latitude, longitude
    raise VoiceCommandError("Say latitude and longitude, or select and name a map point.")


def parse_voice_command(
    text: str,
    vehicles: Iterable[dict[str, Any]],
    yp_vehicle_id: Optional[str],
    selected_location: Optional[dict[str, float]] = None,
    confirmation_mode: str = "risky",
) -> dict[str, Any]:
    """Convert an utterance into one previewable command for a connected vehicle."""
    normalized_text = " ".join(_tokens(text))
    text_tokens = normalized_text.split()
    vehicle, start, end = _find_vehicle(text_tokens, vehicles)
    vehicle_id = str(vehicle.get("vehicle_id") or "")
    vehicle_type = str(vehicle.get("vehicle_type") or "").lower()
    if not vehicle.get("connected"):
        raise VoiceCommandError(f"{vehicle_id} is not connected.")
    if vehicle_type == "yp":
        raise VoiceCommandError("Select an unmanned vehicle to command.")

    action_text = " ".join(text_tokens[:start] + text_tokens[end:])
    command: dict[str, Any]
    summary: str
    if re.search(r"\b(?:return|go back|come back)\s+(?:to\s+)?(?:the\s+)?boat\b", action_text):
        if not yp_vehicle_id:
            raise VoiceCommandError("No vehicle has been assigned the YP role.")
        command = {"type": "rtb"}
        summary = f"Return {vehicle_id} to the YP-role vessel ({yp_vehicle_id})."
    elif re.search(r"\bland\s+(?:on|aboard)\s+(?:the\s+)?boat\b", action_text):
        if not yp_vehicle_id:
            raise VoiceCommandError("No vehicle has been assigned the YP role.")
        command = {"type": "land_on_boat"}
        summary = f"Land {vehicle_id} on the YP-role vessel ({yp_vehicle_id})."
    elif re.search(r"\b(?:take\s*off|takeoff|launch)\b", action_text):
        if vehicle_type not in ("uav", "uavf"):
            raise VoiceCommandError("Takeoff commands are only supported for aerial vehicles.")
        altitude = _extract_measurement(action_text, "takeoff altitude")
        if altitude > 120:
            raise VoiceCommandError("Takeoff altitude must not exceed 120 meters.")
        command = {"type": "takeoff", "altitude_m": altitude}
        summary = f"Take off {vehicle_id} to {altitude:g} meters."
    elif re.search(r"\bsearch\b.*\bgrid\b", action_text):
        grid_size = _extract_measurement(action_text, "search grid size")
        if grid_size > 5000:
            raise VoiceCommandError("Search grid size must not exceed 5000 meters.")
        latitude, longitude = _location(action_text, selected_location)
        command = {
            "type": "search_grid",
            "lat": latitude,
            "lon": longitude,
            "grid_size_m": grid_size,
            "swath_m": 20,
            "altitude_m": 30,
        }
        summary = (
            f"Search with {vehicle_id}: {grid_size:g}-meter grid at "
            f"{latitude:.6f}, {longitude:.6f}; 30-meter altitude and 20-meter swath."
        )
    elif re.search(r"\b(?:fly|go|navigate)\s+to\b", action_text):
        latitude, longitude = _location(action_text, selected_location)
        position = vehicle.get("position") or {}
        altitude = float(position.get("altitude") or 0)
        altitude_match = re.search(r"\b(?:altitude|height)\b", action_text, re.IGNORECASE)
        if altitude_match:
            altitude = _extract_measurement(action_text, "waypoint altitude")
            if altitude > 120:
                raise VoiceCommandError("Waypoint altitude must not exceed 120 meters.")
        command = {
            "type": "waypoint",
            "target": {
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude,
            },
        }
        summary = (
            f"Send {vehicle_id} to {latitude:.6f}, {longitude:.6f} "
            f"at {altitude:g} meters altitude."
        )
    elif re.search(r"\b(?:altitude|height)\b|\b(?:climb|descend)\s+to\b", action_text):
        if vehicle_type not in ("uav", "uavf"):
            raise VoiceCommandError("Standalone altitude commands are only supported for aerial vehicles.")
        altitude = _extract_measurement(action_text, "altitude")
        if altitude > 120:
            raise VoiceCommandError("Altitude must not exceed 120 meters.")
        position = vehicle.get("position") or {}
        try:
            latitude = float(position["latitude"])
            longitude = float(position["longitude"])
        except (KeyError, TypeError, ValueError):
            raise VoiceCommandError("A live vehicle position is required to set altitude.")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise VoiceCommandError("The vehicle's current position is invalid.")
        command = {
            "type": "waypoint",
            "target": {
                "latitude": latitude,
                "longitude": longitude,
                "altitude": altitude,
            },
        }
        summary = f"Set {vehicle_id} altitude to {altitude:g} meters at its current position."
    else:
        raise VoiceCommandError("That voice command is not supported yet.")

    return {
        "vehicle_id": vehicle_id,
        "command": command,
        "summary": summary,
        "requires_confirmation": confirmation_mode == "all"
        or (confirmation_mode == "risky" and command["type"] in {
            "takeoff", "search_grid", "waypoint", "rtb", "land_on_boat",
        }),
    }
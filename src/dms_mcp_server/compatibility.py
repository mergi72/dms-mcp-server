from __future__ import annotations

import re


_VERSION_PATTERN = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_PATTERN.match(value.strip())
    if match is None:
        raise ValueError(f"Invalid version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())  # type: ignore[return-value]


def require_supported_bridge_version(current: str, minimum: str) -> None:
    try:
        current_version = _version_tuple(current)
        minimum_version = _version_tuple(minimum)
    except ValueError as exc:
        raise RuntimeError(f"Bridge compatibility check failed: {exc}") from exc
    if current_version < minimum_version:
        raise RuntimeError(
            f"Unsupported bridge version {current!r}; minimum required version is {minimum!r}."
        )

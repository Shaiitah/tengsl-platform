"""Отслеживание изменений состояния зон между циклами опроса."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ZoneState:
    zone_number: int
    raw_value: int | None = None
    consecutive_errors: int = 0


class StateTracker:
    def __init__(self) -> None:
        self._states: dict[int, ZoneState] = {}

    def get(self, zone_number: int) -> ZoneState:
        if zone_number not in self._states:
            self._states[zone_number] = ZoneState(zone_number=zone_number)
        return self._states[zone_number]

    def has_changed(self, zone_number: int, raw_value: int) -> bool:
        state = self.get(zone_number)
        return state.raw_value != raw_value

    def update(self, zone_number: int, raw_value: int) -> None:
        state = self.get(zone_number)
        state.raw_value = raw_value
        state.consecutive_errors = 0

    def mark_error(self, zone_number: int) -> int:
        state = self.get(zone_number)
        state.consecutive_errors += 1
        return state.consecutive_errors

    def clear(self) -> None:
        self._states.clear()
"""Indigo-independent rain-event state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RainState:
    second_detection_seconds: int = 60
    minimum_high_seconds: int = 60
    dry_seconds: int = 60
    day_key: str = ""
    accumulated_seconds: float = 0.0
    detections_today: int = 0
    candidate_at: datetime | None = None
    candidate_off_at: datetime | None = None
    raining_since: datetime | None = None
    last_detection: datetime | None = None
    source_high: bool = False
    high_since: datetime | None = None
    low_since: datetime | None = None
    rain_ended_at: datetime | None = None

    @property
    def is_raining(self) -> bool:
        return self.raining_since is not None

    def detection(self, now: datetime) -> bool:
        """Record one completed pulse. Return True when rain is confirmed."""
        self.advance(now)
        self.detections_today += 1
        self.last_detection = now
        self.low_since = now
        if self.is_raining:
            return False

        if (
            self.candidate_at is None
            or (now - self.candidate_at).total_seconds()
            > self.second_detection_seconds
        ):
            self.candidate_at = now
            self.candidate_off_at = now
            return False

        self.raining_since = self.candidate_at
        self.candidate_at = None
        self.candidate_off_at = None
        return True

    def input_changed(self, is_high: bool, now: datetime) -> bool:
        """Record an RG-9 input transition; return True on confirmation."""
        self.advance(now)
        if is_high == self.source_high:
            return False

        confirmed = self.confirm_sustained_high(now) if not is_high else False
        self.source_high = is_high
        if not is_high:
            self.high_since = None
            self.low_since = now
            if not self.is_raining and self.candidate_at is not None:
                self.candidate_off_at = now
            return confirmed

        self.high_since = now
        self.low_since = None
        self.detections_today += 1
        self.last_detection = now
        if self.is_raining:
            return False
        if self.candidate_at is None:
            self.candidate_at = now
            self.candidate_off_at = None
            return False

        self.raining_since = self.candidate_at
        self.candidate_at = None
        self.candidate_off_at = None
        return True

    def confirm_sustained_high(self, now: datetime) -> bool:
        """Confirm rain when the first high never returns low."""
        self._roll_days(now)
        if (
            self.is_raining
            or not self.source_high
            or self.candidate_at is None
            or self.high_since is None
            or (now - self.high_since).total_seconds() < self.minimum_high_seconds
        ):
            return False
        self.raining_since = self.candidate_at
        self.candidate_at = None
        self.candidate_off_at = None
        return True

    def advance(self, now: datetime) -> bool:
        """Advance timers. Return True when a rain event ends."""
        self._roll_days(now)
        if (
            not self.is_raining
            and self.candidate_at is not None
            and not self.source_high
            and (now - self.candidate_at).total_seconds()
            > self.second_detection_seconds
        ):
            self.candidate_at = None
            self.candidate_off_at = None
        if (
            not self.is_raining
            or self.source_high
            or self.low_since is None
            or (now - self.low_since).total_seconds() < self.dry_seconds
        ):
            return False

        self.accumulated_seconds += max(
            0.0, (self.low_since - self.raining_since).total_seconds()
        )
        self.rain_ended_at = self.low_since
        self.raining_since = None
        return True

    def total_seconds(self, now: datetime) -> int:
        self.advance(now)
        total = self.accumulated_seconds
        if self.is_raining:
            wet_through = now if self.source_high else self.low_since
            if wet_through is None:
                wet_through = self.last_detection
            total += max(
                0.0, (wet_through - self.raining_since).total_seconds()
            )
        return int(total)

    def _roll_days(self, now: datetime) -> None:
        current_key = now.date().isoformat()
        if not self.day_key:
            self.day_key = current_key
            return
        if self.day_key == current_key:
            return

        midnight = datetime.combine(now.date(), datetime.min.time())
        self.day_key = current_key
        self.accumulated_seconds = 0.0
        self.detections_today = 0
        if self.is_raining:
            self.raining_since = midnight
        if self.candidate_at is not None and self.candidate_at < midnight:
            self.candidate_at = None
            self.candidate_off_at = None


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

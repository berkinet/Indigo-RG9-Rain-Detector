"""Indigo-independent rain-event state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class RainState:
    confirmation_seconds: int = 60
    dry_seconds: int = 60
    day_key: str = ""
    accumulated_seconds: float = 0.0
    detections_today: int = 0
    candidate_at: datetime | None = None
    raining_since: datetime | None = None
    last_detection: datetime | None = None

    @property
    def is_raining(self) -> bool:
        return self.raining_since is not None

    def detection(self, now: datetime) -> bool:
        """Record one rising edge. Return True when rain becomes confirmed."""
        self.advance(now)
        self.detections_today += 1
        self.last_detection = now
        if self.is_raining:
            return False

        if (
            self.candidate_at is None
            or (now - self.candidate_at).total_seconds() > self.confirmation_seconds
        ):
            self.candidate_at = now
            return False

        self.raining_since = self.candidate_at
        self.candidate_at = None
        return True

    def advance(self, now: datetime) -> bool:
        """Advance timers. Return True when a rain event ends."""
        self._roll_days(now)
        if (
            not self.is_raining
            and self.candidate_at is not None
            and (now - self.candidate_at).total_seconds() > self.confirmation_seconds
        ):
            self.candidate_at = None
        if (
            not self.is_raining
            or self.last_detection is None
            or (now - self.last_detection).total_seconds() < self.dry_seconds
        ):
            return False

        self.accumulated_seconds += max(
            0.0, (self.last_detection - self.raining_since).total_seconds()
        )
        self.raining_since = None
        return True

    def total_seconds(self, now: datetime) -> int:
        self.advance(now)
        total = self.accumulated_seconds
        if self.is_raining and self.last_detection is not None:
            total += max(
                0.0, (self.last_detection - self.raining_since).total_seconds()
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


def format_duration(seconds: int) -> str:
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

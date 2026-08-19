"""Tests for scheduling (Task 2.8)."""

from datetime import datetime, timezone, timedelta

import pytest

from app.pipeline.scheduler import compute_next_publish_slot


class TestScheduler:
    def test_next_slot_on_publish_day(self):
        # If today is a publish day, and time hasn't passed, should pick today
        now = datetime(2026, 3, 16, 6, 0, 0, tzinfo=timezone.utc)  # Monday 06:00
        slot = compute_next_publish_slot(
            ["monday", "thursday"], "09:00", "UTC", now_utc=now,
        )
        assert "2026-03-16T09:00:00Z" == slot

    def test_bumps_to_next_day_if_too_late(self):
        # Monday at 10:00, publish time is 09:00 — should skip to Thursday
        now = datetime(2026, 3, 16, 10, 0, 0, tzinfo=timezone.utc)  # Monday 10:00
        slot = compute_next_publish_slot(
            ["monday", "thursday"], "09:00", "UTC", now_utc=now,
        )
        assert "2026-03-19T09:00:00Z" == slot  # Thursday

    def test_collision_bumping(self):
        now = datetime(2026, 3, 16, 6, 0, 0, tzinfo=timezone.utc)
        taken = ["2026-03-16T09:00:00Z"]
        slot = compute_next_publish_slot(
            ["monday", "thursday"], "09:00", "UTC",
            now_utc=now, taken_slots=taken,
        )
        assert slot == "2026-03-19T09:00:00Z"  # Bumps to Thursday

    def test_minimum_1_hour_buffer(self):
        # Now is 08:30, publish at 09:00 — only 30 min buffer, should skip
        now = datetime(2026, 3, 16, 8, 30, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            ["monday", "thursday"], "09:00", "UTC", now_utc=now,
        )
        # 09:00 is only 30 min away, need 1 hour buffer → skip to Thursday
        assert "2026-03-19T09:00:00Z" == slot

    def test_single_publish_day(self):
        now = datetime(2026, 3, 16, 6, 0, 0, tzinfo=timezone.utc)  # Monday
        slot = compute_next_publish_slot(
            ["wednesday"], "14:00", "UTC", now_utc=now,
        )
        assert "2026-03-18T14:00:00Z" == slot  # Wednesday

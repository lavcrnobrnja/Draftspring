"""Comprehensive tests for the publish scheduling system."""

import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.pipeline.scheduler import compute_next_publish_slot


class TestTimezoneConversion:
    """The core bug: publish_time must be interpreted in publish_timezone, not UTC."""

    def test_et_time_converts_to_utc_correctly(self):
        """09:00 ET (EDT, UTC-4) should become 13:00 UTC."""
        # Wednesday March 18 2026, 12:00 UTC (8:00 AM ET) — before publish time
        now = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # 09:00 ET = 13:00 UTC, and it's after now+1h (13:00), so it fits
        assert slot == "2026-03-18T13:00:00Z"

    def test_et_time_not_treated_as_utc(self):
        """Regression: old code would schedule at 09:00 UTC instead of 09:00 ET."""
        now = datetime(2026, 3, 18, 4, 0, 0, tzinfo=timezone.utc)  # midnight ET
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # Should be 13:00 UTC (09:00 ET), NOT 09:00 UTC
        assert slot == "2026-03-18T13:00:00Z"
        assert slot != "2026-03-18T09:00:00Z"

    def test_pacific_time_converts_correctly(self):
        """09:00 PT (PDT, UTC-7) should become 16:00 UTC."""
        now = datetime(2026, 3, 18, 10, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="America/Los_Angeles",
            now_utc=now,
        )
        assert slot == "2026-03-18T16:00:00Z"

    def test_london_bst_converts_correctly(self):
        """09:00 London (BST, UTC+1) should become 08:00 UTC during summer."""
        # Late March 2026 — BST is in effect (clocks forward last Sunday of March)
        now = datetime(2026, 3, 30, 5, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["monday"],
            publish_time="09:00",
            publish_timezone="Europe/London",
            now_utc=now,
        )
        assert slot == "2026-03-30T08:00:00Z"

    def test_utc_timezone_no_offset(self):
        """UTC timezone should produce no offset."""
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        assert slot == "2026-03-18T09:00:00Z"

    def test_tokyo_time_converts_correctly(self):
        """09:00 Tokyo (JST, UTC+9) should become 00:00 UTC."""
        now = datetime(2026, 3, 17, 12, 0, 0, tzinfo=timezone.utc)  # 21:00 JST Tue
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="Asia/Tokyo",
            now_utc=now,
        )
        # 09:00 JST Wed = 00:00 UTC Wed
        assert slot == "2026-03-18T00:00:00Z"


class TestDSTTransitions:
    """Scheduling around DST changes must handle offset shifts."""

    def test_spring_forward_et(self):
        """2026 spring forward: March 8, 2:00 AM → 3:00 AM ET.
        Scheduling for March 9 should use EDT (UTC-4), not EST (UTC-5)."""
        # March 7 2026, 12:00 UTC (Saturday)
        now = datetime(2026, 3, 7, 12, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["monday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # March 9 (Mon) 09:00 EDT = 13:00 UTC (EDT is UTC-4)
        assert slot == "2026-03-09T13:00:00Z"

    def test_fall_back_et(self):
        """2026 fall back: Nov 1, 2:00 AM → 1:00 AM ET.
        Scheduling for Nov 2 should use EST (UTC-5), not EDT (UTC-4)."""
        # October 31 2026, 12:00 UTC (Saturday)
        now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["monday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # Nov 2 (Mon) 09:00 EST = 14:00 UTC (EST is UTC-5)
        assert slot == "2026-11-02T14:00:00Z"


class TestDaySelection:
    """Correct days must be picked."""

    def test_picks_next_matching_day(self):
        """If today is Wednesday and days are Mon/Thu, pick Thursday."""
        now = datetime(2026, 3, 18, 10, 0, 0, tzinfo=timezone.utc)  # Wed
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        assert "2026-03-19" in slot  # Thursday

    def test_skips_today_if_past_time(self):
        """If publish time already passed today, go to next matching day."""
        now = datetime(2026, 3, 19, 14, 0, 0, tzinfo=timezone.utc)  # Thu 2pm UTC
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        # 09:00 UTC Thu already passed (it's 14:00), next is Monday
        assert "2026-03-23" in slot  # Monday

    def test_respects_one_hour_buffer(self):
        """Even if today matches, need 1h buffer from now."""
        # Thursday at 08:30 UTC — publish_time is 09:00 UTC
        # 09:00 is only 30 min away, less than 1h buffer
        now = datetime(2026, 3, 19, 8, 30, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        # Should skip today and go to Monday
        assert "2026-03-23" in slot

    def test_today_works_with_sufficient_buffer(self):
        """If 1h+ buffer exists before today's publish time, use today."""
        # Thursday at 07:00 UTC — publish_time is 09:00 UTC (2h away)
        now = datetime(2026, 3, 19, 7, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        assert "2026-03-19" in slot  # Today

    def test_single_day_wraps_to_next_week(self):
        """With only Monday selected, if Monday is past, wrap to next Monday."""
        now = datetime(2026, 3, 16, 14, 0, 0, tzinfo=timezone.utc)  # Mon 2pm
        slot = compute_next_publish_slot(
            publish_days=["monday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        assert "2026-03-23" in slot  # Next Monday

    def test_empty_days_uses_default(self):
        """Empty publish_days should fall back to Mon/Thu."""
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)  # Wed
        slot = compute_next_publish_slot(
            publish_days=[],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
        )
        assert "2026-03-19" in slot  # Thursday (default Mon/Thu)


class TestSlotConflicts:
    """Taken slots must be skipped."""

    def test_skips_taken_slot(self):
        """If the ideal slot is already taken, find the next one."""
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)  # Wed
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
            taken_slots=["2026-03-19T09:00:00Z"],
        )
        # Thursday is taken, next is Monday
        assert "2026-03-23" in slot

    def test_multiple_taken_slots(self):
        """Skip multiple consecutive taken slots."""
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)  # Wed
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="UTC",
            now_utc=now,
            taken_slots=["2026-03-19T09:00:00Z", "2026-03-23T09:00:00Z"],
        )
        # Thu and Mon both taken, next is Thu March 26
        assert "2026-03-26" in slot


class TestTimezoneWithDayBoundary:
    """Edge case: timezone offset can shift which calendar day the slot falls on."""

    def test_late_night_publish_crosses_day_in_utc(self):
        """23:00 ET = 03:00 UTC next day. The UTC date should be correct."""
        now = datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc)  # Wed noon UTC
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="23:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # 23:00 ET Wed = 03:00 UTC Thu (March 19)
        assert slot == "2026-03-19T03:00:00Z"

    def test_early_morning_positive_offset(self):
        """01:00 Tokyo (UTC+9) = 16:00 UTC previous day."""
        now = datetime(2026, 3, 17, 10, 0, 0, tzinfo=timezone.utc)  # Tue
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="01:00",
            publish_timezone="Asia/Tokyo",
            now_utc=now,
        )
        # 01:00 JST Wed = 16:00 UTC Tue (March 17)
        assert slot == "2026-03-17T16:00:00Z"


class TestInvalidTimezone:
    """Gracefully handle bad timezone strings."""

    def test_invalid_tz_falls_back_to_utc(self):
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["wednesday"],
            publish_time="09:00",
            publish_timezone="Not/A/Timezone",
            now_utc=now,
        )
        # Should fall back to UTC
        assert slot == "2026-03-18T09:00:00Z"


class TestFallback:
    """When no matching day found in 14 days (shouldn't happen normally), fallback works."""

    def test_fallback_uses_user_timezone(self):
        """Fallback (no matching days in 14 days) should still respect timezone."""
        now = datetime(2026, 3, 18, 5, 0, 0, tzinfo=timezone.utc)
        slot = compute_next_publish_slot(
            publish_days=["nonexistent_day"],  # No valid days
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        # Default Mon/Thu kicks in since no valid days parsed
        # Actually "nonexistent_day" won't be in WEEKDAY_MAP, so target_days=[] → defaults to Mon/Thu
        assert "T13:00:00Z" in slot  # 09:00 ET = 13:00 UTC


class TestEndToEndScenario:
    """Simulate Lav's actual setup."""

    def test_lav_scenario_mon_thu_9am_et(self):
        """Lav: publish_days=Mon+Thu, publish_time=09:00, tz=America/New_York.
        On Wed March 18 at noon UTC, next slot = Thu March 19 at 09:00 ET = 13:00 UTC."""
        now = datetime(2026, 3, 18, 16, 0, 0, tzinfo=timezone.utc)  # Wed 4pm UTC = noon ET
        slot = compute_next_publish_slot(
            publish_days=["thursday", "monday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        assert slot == "2026-03-19T13:00:00Z"

    def test_lav_scenario_approve_on_thursday(self):
        """Approve on Thu after publish time → next slot is Monday."""
        now = datetime(2026, 3, 19, 18, 0, 0, tzinfo=timezone.utc)  # Thu 2pm ET
        slot = compute_next_publish_slot(
            publish_days=["thursday", "monday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
        )
        assert slot == "2026-03-23T13:00:00Z"  # Mon 09:00 ET = 13:00 UTC


class TestSaturday_PublishBug_Apr11:
    """Regression: all 4 Mon/Thu slots within 14 days were taken.
    
    Old code fell through to a fallback that scheduled 'tomorrow' regardless
    of publish_days, causing a Saturday publish when schedule was Mon/Thu.
    Bug report: Trello 69da5b12.
    """

    def test_all_14day_slots_taken_selects_next_valid_day_not_tomorrow(self):
        """Exact reproduction: 4 Mon/Thu slots taken, must go to Mon Apr 27."""
        # Approval happened at 01:55:38 UTC on Apr 11 (Fri 9:55 PM ET)
        now = datetime(2026, 4, 11, 1, 55, 38, tzinfo=timezone.utc)
        taken = [
            "2026-04-13T13:00:00Z",  # Mon Apr 13
            "2026-04-16T13:00:00Z",  # Thu Apr 16
            "2026-04-20T13:00:00Z",  # Mon Apr 20
            "2026-04-23T13:00:00Z",  # Thu Apr 23
        ]
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
            taken_slots=taken,
        )
        # Must be Mon Apr 27 at 09:00 ET = 13:00 UTC. NOT Saturday Apr 11.
        assert slot == "2026-04-27T13:00:00Z"
        assert "2026-04-11" not in slot  # Never schedule on Saturday

    def test_result_always_falls_on_publish_day(self):
        """No matter how many slots are taken, result must be a valid publish day."""
        now = datetime(2026, 4, 11, 1, 55, 38, tzinfo=timezone.utc)
        # Take up 10 consecutive Mon/Thu slots
        taken = [
            "2026-04-13T13:00:00Z", "2026-04-16T13:00:00Z",
            "2026-04-20T13:00:00Z", "2026-04-23T13:00:00Z",
            "2026-04-27T13:00:00Z", "2026-04-30T13:00:00Z",
            "2026-05-04T13:00:00Z", "2026-05-07T13:00:00Z",
            "2026-05-11T13:00:00Z", "2026-05-14T13:00:00Z",
        ]
        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
            taken_slots=taken,
        )
        # Parse slot back and check it's a Monday (0) or Thursday (3)
        slot_utc = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        slot_local = slot_utc.astimezone(ZoneInfo("America/New_York"))
        assert slot_local.weekday() in (0, 3), f"Slot {slot} is {slot_local.strftime('%A')}, expected Mon or Thu"

    def test_fallback_respects_publish_days(self):
        """Even the ultimate fallback must not schedule on a non-publish day."""
        now = datetime(2026, 4, 10, 5, 0, 0, tzinfo=timezone.utc)  # Fri
        # Take ALL Mon/Thu slots for 90 days (extreme edge case)
        taken = []
        user_tz = ZoneInfo("America/New_York")
        base_local = now.astimezone(user_tz).date()
        for offset in range(90):
            d = base_local + timedelta(days=offset)
            if d.weekday() in (0, 3):  # Mon or Thu
                candidate_local = datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=user_tz)
                candidate_utc = candidate_local.astimezone(timezone.utc)
                taken.append(candidate_utc.isoformat().replace("+00:00", "Z"))

        slot = compute_next_publish_slot(
            publish_days=["monday", "thursday"],
            publish_time="09:00",
            publish_timezone="America/New_York",
            now_utc=now,
            taken_slots=taken,
        )
        # Parse and verify it's still a valid day
        slot_utc = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        slot_local = slot_utc.astimezone(user_tz)
        assert slot_local.weekday() in (0, 3), f"Fallback {slot} is {slot_local.strftime('%A')}, expected Mon or Thu"
        # Verify time is correct
        assert slot_local.hour == 9
        assert slot_local.minute == 0

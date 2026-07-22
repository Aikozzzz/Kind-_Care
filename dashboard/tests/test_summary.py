from datetime import date

from dashboard.components.summary import (
    build_activity_device_html,
    build_activity_history_html,
    build_device_card_html,
    build_health_metric_cards_html,
    build_profile_card_html,
    build_reminder_rows_html,
)


def test_profile_card_matches_resident_overview_and_escapes_content() -> None:
    html = build_profile_card_html(
        {
            "profile": {
                "elderly_id": "E001",
                "full_name": "Margaret <Lee>",
                "date_of_birth": "1948-04-12",
                "emergency_contact_name": "Daniel Lee",
                "emergency_contact_phone": "555-0199",
            },
            "device_status": {"status": "online"},
        },
        today=date(2026, 7, 21),
    )

    assert "ML" in html
    assert "Margaret &lt;Lee&gt;" in html
    assert "Resident ID E001" in html
    assert "Age 78" in html
    assert "Device online" in html
    assert "Profile active" in html
    assert 'class="status-pill device-online"' in html
    assert ">Live<" not in html
    assert "Daniel Lee" in html and "555-0199" in html
    assert "<Lee>" not in html


def test_profile_initials_skip_tokens_without_alphanumeric_characters() -> None:
    html = build_profile_card_html(
        {
            "profile": {
                "elderly_id": "E001",
                "full_name": "--- Margaret Lee",
                "date_of_birth": "1948-04-12",
            },
            "device_status": None,
        },
        today=date(2026, 7, 21),
    )

    assert 'class="resident-avatar" aria-hidden="true">ML</div>' in html


def test_profile_device_badges_have_distinct_safe_semantics() -> None:
    profile = {
        "profile": {
            "elderly_id": "E001",
            "full_name": "Margaret Lee",
            "date_of_birth": "1948-04-12",
        }
    }

    offline = build_profile_card_html(
        {**profile, "device_status": {"status": "offline<script>"}}
    )
    unavailable = build_profile_card_html({**profile, "device_status": None})

    assert "Device status unavailable" in offline
    assert 'class="status-pill device-unavailable"' in offline
    assert 'class="status-pill device-unavailable"' in unavailable
    assert "<script>" not in offline


def test_vital_cards_include_activity_and_measurement_units() -> None:
    html = build_health_metric_cards_html(
        {
            "latest_health": {
                "heart_rate": 129,
                "oxygen_level": 91,
                "temperature": 36.7,
            },
            "latest_activity": {"value": "active"},
        }
    )

    assert 'class="vitals-grid"' in html
    assert "129" in html and "bpm" in html
    assert "91" in html and "%" in html
    assert "36.7" in html and "C" in html
    assert "Active" in html


def test_missing_activity_uses_waiting_copy() -> None:
    html = build_health_metric_cards_html({"latest_activity": None})

    assert "Waiting for activity data" in html
    assert "No recent movement" not in html


def test_device_card_has_clear_online_and_empty_states() -> None:
    online = build_device_card_html(
        {"device_status": {"status": "online", "last_seen": "2026-07-21T10:00:00Z"}}
    )
    empty = build_device_card_html({"device_status": None})

    assert "Device status" in online
    assert "Device online" in online
    assert "Jul 21, 2026 at 10:00 AM UTC" in online
    assert "No device data" in empty


def test_activity_and_device_rows_are_ascii_textual_and_escaped() -> None:
    html = build_activity_device_html(
        {
            "latest_activity": {
                "value": "inactive",
                "recorded_at": "2026-07-17T08:00:00Z<script>",
            },
            "device_status": {
                "status": "offline",
                "last_seen": "2026-07-17T07:55:00Z",
            },
        }
    )
    assert "Activity: inactive" in html
    assert "Device: offline" in html
    assert "status-inactive" in html
    assert "status-offline" in html
    assert "<script>" not in html


def test_activity_and_device_rows_have_explicit_empty_state() -> None:
    html = build_activity_device_html(
        {"latest_activity": None, "device_status": None}
    )
    assert "Activity: no data" in html
    assert "Device: no data" in html


def test_activity_history_uses_caregiver_copy_and_escaped_formatted_times() -> None:
    html = build_activity_history_html(
        [
            {
                "value": "inactive<script>",
                "recorded_at": "2026-07-17T08:00:00Z",
                "received_at": "2026-07-17T08:00:01Z",
            }
        ]
    )

    assert "Activity: inactive&lt;script&gt;" in html
    assert "Observed Jul 17, 2026 at 8:00 AM UTC" in html
    assert "Received Jul 17, 2026 at 8:00 AM UTC" in html
    assert "[-]" not in html
    assert "ACTIVITY HISTORY" not in html
    assert "<script>" not in html


def test_reminder_rows_use_caregiver_copy_and_escaped_formatted_times() -> None:
    html = build_reminder_rows_html(
        [
            {
                "reminder_id": "r-1",
                "medicine_name": "Aspirin<script>",
                "scheduled_for": "2026-07-18T08:00:00Z",
                "status": "missed",
                "instructions": "After food",
            }
        ]
    )

    assert "Missed: Aspirin&lt;script&gt;" in html
    assert "Scheduled Jul 18, 2026 at 8:00 AM UTC" in html
    assert "[x]" not in html
    assert "MISSED" not in html
    assert "reminder-missed" in html
    assert "<script>" not in html


def test_reminder_rows_have_explicit_empty_state() -> None:
    assert "No reminders" in build_reminder_rows_html([])

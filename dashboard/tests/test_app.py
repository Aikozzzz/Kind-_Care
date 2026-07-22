import pytest
from streamlit.testing.v1 import AppTest

from dashboard.api import DashboardAPIError, DashboardUnavailable, KindCareAPI


SUMMARY = {
    "profile": {
        "elderly_id": "E001",
        "full_name": "Margaret Lee",
        "date_of_birth": "1948-04-12",
        "emergency_contact_name": "Daniel Lee",
    },
    "latest_health": {
        "heart_rate": 82,
        "oxygen_level": 97,
        "temperature": 36.8,
        "recorded_at": "2026-07-16T10:30:00Z",
    },
    "current_risk": "normal",
    "current_alert": None,
    "recent_alerts": [],
}
HEALTH = [
    {
        "recorded_at": "2026-07-16T10:30:00Z",
        "heart_rate": 82,
        "oxygen_level": 97,
        "temperature": 36.8,
    }
]


@pytest.fixture(autouse=True)
def active_profiles(monkeypatch) -> None:
    monkeypatch.setattr(
        KindCareAPI,
        "get_profiles",
        lambda self, limit=100: [SUMMARY["profile"]],
    )


def test_streamlit_app_renders_complete_monitoring_page(monkeypatch) -> None:
    summary_calls = []
    monkeypatch.setattr(
        KindCareAPI,
        "get_summary",
        lambda self, elderly_id: summary_calls.append(elderly_id) or SUMMARY,
    )
    monkeypatch.setattr(
        KindCareAPI, "get_health", lambda self, elderly_id, limit=50: HEALTH
    )
    monkeypatch.setattr(
        KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: []
    )
    monkeypatch.setattr(
        KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: []
    )

    def unexpected_device_fetch(*args, **kwargs):
        raise AssertionError("The summary already contains current device status")

    monkeypatch.setattr(KindCareAPI, "get_device_status", unexpected_device_fetch)

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()

    assert not app.exception
    assert app.title[0].value == "Care overview"
    assert app.text_input[0].value == "E001"
    assert app.button[0].label == "Refresh"
    assert summary_calls == ["E001"]


@pytest.mark.parametrize(
    ("view", "title", "expected_content"),
    [
        ("resident", "Resident profile", "Health trends"),
        ("alerts", "Alerts", "Recent alerts"),
        ("medication", "Medication", "Upcoming and recent reminders"),
        ("devices", "Devices", "Current monitoring signals"),
    ],
)
def test_sidebar_view_selection_changes_active_item_and_main_content(
    monkeypatch, view, title, expected_content
) -> None:
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: SUMMARY)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15)
    app.query_params["view"] = view
    app.run()
    markup = "\n".join(markdown.value for markdown in app.markdown)

    assert not app.exception
    assert app.title[0].value == title
    assert f'<a class="active" href="?view={view}&amp;resident=E001"' in markup
    assert expected_content in markup
    assert "Care overview" not in markup


def test_medication_view_does_not_fetch_unrelated_histories(monkeypatch) -> None:
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: SUMMARY)

    def unexpected_history_fetch(*args, **kwargs):
        raise AssertionError("Medication view should use reminders from the summary")

    monkeypatch.setattr(KindCareAPI, "get_health", unexpected_history_fetch)
    monkeypatch.setattr(KindCareAPI, "get_alerts", unexpected_history_fetch)
    monkeypatch.setattr(KindCareAPI, "get_activity", unexpected_history_fetch)

    app = AppTest.from_file("dashboard/app.py", default_timeout=15)
    app.query_params["view"] = "medication"
    app.run()

    assert not app.exception
    assert app.title[0].value == "Medication"


def test_streamlit_reminder_action_calls_rest_and_reruns(monkeypatch) -> None:
    summary = {
        **SUMMARY,
        "upcoming_reminders": [
            {
                "reminder_id": "reminder-1",
                "medicine_name": "Aspirin",
                "scheduled_for": "2026-07-18T08:00:00Z",
                "status": "pending",
            }
        ],
        "recent_reminders": [],
    }
    calls = []
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: summary)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(
        KindCareAPI,
        "mark_reminder_taken",
        lambda self, elderly_id, reminder_id: calls.append((elderly_id, reminder_id))
        or {"status": "taken"},
    )

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    app.button[1].click().run()

    assert calls == [("E001", "reminder-1")]
    assert app.button[1].label == "Mark Aspirin taken (Jul 18, 8:00 AM)"
    assert "reminder-1" not in app.button[1].label
    assert app.success[0].value == "Aspirin reminder marked taken."
    assert not app.exception


def test_streamlit_action_error_is_accessibly_reported(monkeypatch) -> None:
    summary = {
        **SUMMARY,
        "upcoming_reminders": [
            {
                "reminder_id": "reminder-1",
                "medicine_name": "Aspirin",
                "scheduled_for": "2026-07-18T08:00:00Z",
                "status": "pending",
            }
        ],
        "recent_reminders": [],
    }
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: summary)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    def fail(self, elderly_id, reminder_id):
        raise DashboardAPIError("transition conflict")

    monkeypatch.setattr(KindCareAPI, "mark_reminder_taken", fail)
    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    app.button[1].click().run()

    assert "Action failed. transition conflict" in app.error[0].value


def test_streamlit_alert_actions_use_type_and_created_time_without_public_id(monkeypatch) -> None:
    alert_id = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    alerts = [
        {
            "alert_id": alert_id,
            "alert_type": "device_offline",
            "message": "Monitoring device is offline",
            "severity": "warning",
            "status": "unresolved",
            "created_at": "2026-07-18T08:00:00Z",
        }
    ]
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: SUMMARY)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(
        KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: alerts
    )
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    labels = [button.label for button in app.button]

    assert "Acknowledge device offline (Jul 18, 8:00 AM)" in labels
    assert "Resolve device offline (Jul 18, 8:00 AM)" in labels
    assert all(alert_id not in label for label in labels)


def test_risk_banner_uses_current_alert_outside_recent_history(monkeypatch) -> None:
    alert_id = "a74cfda8-d0ef-518e-a671-a2eabca7f6b0"
    summary = {
        **SUMMARY,
        "current_risk": "warning",
        "current_alert": {
            "alert_id": alert_id,
            "alert_type": "device_offline",
            "message": "Old device alert still needs attention",
            "severity": "warning",
            "status": "unresolved",
            "created_at": "2026-07-17T08:00:00Z",
        },
        "recent_alerts": [],
    }
    calls = []
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: summary)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(
        KindCareAPI,
        "update_alert_status",
        lambda self, candidate, status: calls.append((candidate, status)) or {},
    )

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    assert any(
        "Old device alert still needs attention" in markdown.value
        for markdown in app.markdown
    )
    banner = next(button for button in app.button if button.label == "Acknowledge alert")
    banner.click().run()

    assert calls == [(alert_id, "acknowledged")]


def test_malformed_risk_cannot_enter_markup_class(monkeypatch) -> None:
    malformed = 'warning\" onmouseover=\"alert(1)'
    monkeypatch.setattr(
        KindCareAPI,
        "get_summary",
        lambda self, elderly_id: {**SUMMARY, "current_risk": malformed},
    )
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    markup = "\n".join(markdown.value for markdown in app.markdown)

    assert malformed not in markup
    assert "onmouseover" not in markup


def test_search_resolves_exact_name_and_unique_case_insensitive_substring(monkeypatch) -> None:
    profiles = [
        SUMMARY["profile"],
        {**SUMMARY["profile"], "elderly_id": "E002", "full_name": "Robert Chen"},
    ]
    summary_calls = []
    monkeypatch.setattr(KindCareAPI, "get_profiles", lambda self, limit=100: profiles)
    monkeypatch.setattr(
        KindCareAPI,
        "get_summary",
        lambda self, elderly_id: summary_calls.append(elderly_id)
        or {**SUMMARY, "profile": profiles[1]},
    )
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    app.text_input[0].set_value("bert ch").run()
    markup = "\n".join(markdown.value for markdown in app.markdown)

    assert summary_calls[-1] == "E002"
    assert "?view=devices&amp;resident=E002" in markup
    assert not app.error


def test_search_offers_selectbox_for_multiple_name_matches(monkeypatch) -> None:
    profiles = [
        SUMMARY["profile"],
        {**SUMMARY["profile"], "elderly_id": "E002", "full_name": "Margaret Chen"},
    ]
    monkeypatch.setattr(KindCareAPI, "get_profiles", lambda self, limit=100: profiles)
    monkeypatch.setattr(KindCareAPI, "get_summary", lambda self, elderly_id: SUMMARY)
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()
    app.text_input[0].set_value("margaret").run()

    assert app.selectbox[0].label == "Choose a resident"
    assert app.selectbox[0].options == ["Margaret Lee (E001)", "Margaret Chen (E002)"]


def test_search_reports_not_found_and_profile_lookup_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_ELDERLY_ID", "Unknown Resident")
    monkeypatch.setattr(KindCareAPI, "get_profiles", lambda self, limit=100: [])
    not_found = AppTest.from_file("dashboard/app.py", default_timeout=15).run()

    assert "No active resident matches" in not_found.error[0].value

    def unavailable(self, limit=100):
        raise DashboardUnavailable("profile service offline")

    monkeypatch.setattr(KindCareAPI, "get_profiles", unavailable)
    unavailable_app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()

    assert "Resident search is unavailable" in unavailable_app.error[0].value


def test_direct_id_still_loads_when_profile_listing_is_unavailable(monkeypatch) -> None:
    def unavailable(self, limit=100):
        raise DashboardUnavailable("profile service offline")

    summary_calls = []
    monkeypatch.setattr(KindCareAPI, "get_profiles", unavailable)
    monkeypatch.setattr(
        KindCareAPI,
        "get_summary",
        lambda self, elderly_id: summary_calls.append(elderly_id) or SUMMARY,
    )
    monkeypatch.setattr(KindCareAPI, "get_health", lambda self, elderly_id, limit=50: [])
    monkeypatch.setattr(KindCareAPI, "get_alerts", lambda self, elderly_id, limit=20: [])
    monkeypatch.setattr(KindCareAPI, "get_activity", lambda self, elderly_id, limit=50: [])

    app = AppTest.from_file("dashboard/app.py", default_timeout=15).run()

    assert summary_calls == ["E001"]
    assert not app.error
    assert not app.exception

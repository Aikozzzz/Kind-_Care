from dashboard.components.overview import (
    build_attention_html,
    build_global_alerts_html,
    build_overview_stats_html,
    build_resident_directory_html,
)


def _snapshot(
    resident_id: str = "E001",
    name: str = "Margaret Lee",
    risk: str = "normal",
    device: str = "online",
) -> dict[str, object]:
    return {
        "profile": {"elderly_id": resident_id, "full_name": name},
        "summary": {
            "current_risk": risk,
            "device_status": {"status": device},
            "latest_health": {"heart_rate": 82},
            "recent_alerts": [],
        },
    }


def test_overview_stats_prioritize_critical_and_attention_counts() -> None:
    html = build_overview_stats_html(
        [_snapshot(), _snapshot("E002", "Robert Tan", "emergency", "offline")]
    )

    assert "Active residents" in html
    assert ">2<" in html
    assert "Critical alerts" in html
    assert "Needs attention" in html
    assert "Devices offline" in html


def test_attention_cards_escape_content_and_link_to_alerts() -> None:
    html = build_attention_html(
        [_snapshot("E001", "Margaret <Lee>", "warning", "online")]
    )

    assert "Margaret &lt;Lee&gt;" in html
    assert "Needs attention" in html
    assert "attention-warning" in html
    assert "<Lee>" not in html


def test_directory_orders_attention_before_stable() -> None:
    html = build_resident_directory_html(
        [_snapshot("E001", "Stable Resident"), _snapshot("E002", "Critical Resident", "emergency")]
    )

    assert html.index("Critical Resident") < html.index("Stable Resident")
    assert "Heart rate" in html
    assert "Device" in html


def test_global_alerts_has_calm_empty_state_and_alert_links() -> None:
    empty = build_global_alerts_html([_snapshot()])
    assert "No active alerts" in empty

    snapshot = _snapshot("E001", "Margaret Lee", "emergency")
    snapshot["summary"]["current_alert"] = {
        "alert_id": "alert-1",
        "alert_type": "high_heart_rate",
        "severity": "emergency",
        "status": "unresolved",
        "message": "Review this alert",
    }
    html = build_global_alerts_html([snapshot])
    assert "High Heart Rate" in html
    assert "attention-critical" in html

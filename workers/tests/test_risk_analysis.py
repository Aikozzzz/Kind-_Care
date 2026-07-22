import pytest

from analysis.risk_analysis import analyze_health


def event(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "heart_rate": 80,
        "temperature": 36.7,
        "oxygen_level": 97,
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
    }
    data.update(updates)
    return data


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"heart_rate": 49}, "emergency"),
        ({"heart_rate": 50}, "normal"),
        ({"heart_rate": 120}, "normal"),
        ({"heart_rate": 121}, "emergency"),
        ({"oxygen_level": 91}, "emergency"),
        ({"oxygen_level": 92}, "normal"),
        ({"temperature": 38.0}, "normal"),
        ({"temperature": 38.1}, "warning"),
    ],
)
def test_risk_rule_boundaries(
    updates: dict[str, object],
    expected: str,
) -> None:
    assert analyze_health(event(**updates)).risk_level == expected


@pytest.mark.parametrize(
    ("updates", "alert_type", "severity", "message"),
    [
        ({"heart_rate": 49}, "low_heart_rate", "emergency", "Low heart rate detected"),
        ({"heart_rate": 121}, "high_heart_rate", "emergency", "High heart rate detected"),
        ({"oxygen_level": 91}, "low_oxygen_level", "emergency", "Low oxygen level detected"),
        ({"temperature": 38.1}, "high_temperature", "warning", "High temperature detected"),
        ({"medicine_status": "missed"}, "medicine_missed", "warning", "Medicine dose missed"),
        ({"emergency_pressed": True}, "emergency_button", "emergency", "Emergency button pressed"),
    ],
)
def test_each_rule_returns_a_stable_finding(
    updates: dict[str, object],
    alert_type: str,
    severity: str,
    message: str,
) -> None:
    result = analyze_health(event(**updates))

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert (finding.alert_type, finding.severity, finding.message) == (
        alert_type,
        severity,
        message,
    )


def test_emergency_precedes_warning_and_findings_are_deterministic() -> None:
    result = analyze_health(
        event(
            heart_rate=49,
            oxygen_level=91,
            temperature=39,
            medicine_status="missed",
            emergency_pressed=True,
        )
    )

    assert result.risk_level == "emergency"
    assert [finding.alert_type for finding in result.findings] == [
        "low_heart_rate",
        "low_oxygen_level",
        "high_temperature",
        "medicine_missed",
        "emergency_button",
    ]


def test_multiple_warnings_produce_warning_risk() -> None:
    result = analyze_health(event(temperature=39, medicine_status="missed"))
    assert result.risk_level == "warning"


def test_inactive_movement_does_not_alert_in_task_two() -> None:
    result = analyze_health(event(movement_status="inactive"))

    assert result.risk_level == "normal"
    assert result.findings == ()

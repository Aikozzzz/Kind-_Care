from dashboard.components.summary import DASHBOARD_FONT_STACK, build_health_charts


RECORDS = [
    {
        "recorded_at": "2026-07-15T22:30:00Z",
        "heart_rate": 70,
        "oxygen_level": 95,
        "temperature": 36.5,
    },
    {
        "recorded_at": "2026-07-15T22:31:00Z",
        "heart_rate": 72,
        "oxygen_level": 96,
        "temperature": 36.6,
    },
    {
        "recorded_at": "2026-07-16T10:30:00Z",
        "heart_rate": 82,
        "oxygen_level": 97,
        "temperature": 36.8,
    },
    {
        "recorded_at": "2026-07-16T10:31:00Z",
        "heart_rate": 84,
        "oxygen_level": 96,
        "temperature": 36.9,
    },
]


def test_health_charts_configure_all_text_roles_with_dashboard_stack() -> None:
    charts = build_health_charts(RECORDS)

    assert DASHBOARD_FONT_STACK.startswith("Inter, Aptos, Segoe UI")
    for chart in charts:
        config = chart.to_dict()["config"]
        assert config["font"] == DASHBOARD_FONT_STACK
        assert config["axis"]["labelFont"] == DASHBOARD_FONT_STACK
        assert config["axis"]["titleFont"] == DASHBOARD_FONT_STACK
        assert config["legend"]["labelFont"] == DASHBOARD_FONT_STACK
        assert config["legend"]["titleFont"] == DASHBOARD_FONT_STACK
        assert config["title"]["font"] == DASHBOARD_FONT_STACK


def test_health_chart_tooltips_have_explicit_caregiver_labels() -> None:
    vital_chart, temperature_chart = build_health_charts(RECORDS)

    vital_tooltips = vital_chart.to_dict()["encoding"]["tooltip"]
    temperature_tooltips = temperature_chart.to_dict()["encoding"]["tooltip"]
    assert [item["title"] for item in vital_tooltips] == [
        "Recorded at",
        "Measure",
        "Value",
    ]
    assert [item["title"] for item in temperature_tooltips] == [
        "Recorded at",
        "Temperature C",
    ]


def test_health_chart_uses_named_series_and_latest_twelve_hour_window() -> None:
    vital_chart, temperature_chart = build_health_charts(RECORDS)
    vital_spec = vital_chart.to_dict()
    temperature_spec = temperature_chart.to_dict()
    vital_values = vital_spec["datasets"][vital_spec["data"]["name"]]
    temperature_values = temperature_spec["datasets"][temperature_spec["data"]["name"]]

    assert {item["measure"] for item in vital_values} == {
        "Heart rate",
        "Oxygen level",
    }
    assert all("2026-07-15T22:30:00" not in item["recorded_at"] for item in vital_values)
    assert any("2026-07-15T22:31:00" in item["recorded_at"] for item in vital_values)
    assert all(
        "2026-07-15T22:30:00" not in item["recorded_at"]
        for item in temperature_values
    )
    assert any(
        "2026-07-15T22:31:00" in item["recorded_at"]
        for item in temperature_values
    )


def test_health_chart_marks_use_contrasting_light_canvas_colors() -> None:
    vital_chart, temperature_chart = build_health_charts(RECORDS)
    vital_spec = vital_chart.to_dict()
    temperature_spec = temperature_chart.to_dict()

    assert vital_spec["encoding"]["color"]["scale"]["range"] == [
        "#2f7f6d",
        "#4c78df",
    ]
    assert temperature_spec["mark"]["color"] == "#e39a2c"

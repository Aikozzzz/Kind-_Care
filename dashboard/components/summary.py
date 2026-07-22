from datetime import UTC, date, datetime, timedelta
from html import escape

import altair as alt
import pandas as pd
import streamlit as st


DASHBOARD_FONT_STACK = "Inter, Aptos, Segoe UI, sans-serif"


def section_label(marker: str, title: str) -> None:
    marker_text = f"[{escape(marker)}] " if marker else ""
    st.markdown(
        f'<div class="section-label">{marker_text}{escape(title)}</div>',
        unsafe_allow_html=True,
    )


def _initials(full_name: str) -> str:
    initials = []
    for part in full_name.split():
        initial = next((character for character in part if character.isalnum()), "")
        if initial:
            initials.append(initial.upper())
        if len(initials) == 2:
            break
    return "".join(initials) or "KC"


def _age(date_of_birth: object, today: date) -> str:
    try:
        born = date.fromisoformat(str(date_of_birth))
    except ValueError:
        return "Age unavailable"
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return f"Age {years}"


def _utc_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC)


def _caregiver_time(value: object) -> str:
    local = _utc_datetime(value)
    if local is None:
        return "Time unavailable"
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{local.strftime('%b %d, %Y')} at {hour}:{local.strftime('%M %p')} UTC"


def _compact_caregiver_time(value: object) -> str:
    local = _utc_datetime(value)
    if local is None:
        return "time unavailable"
    hour = local.strftime("%I").lstrip("0") or "0"
    return f"{local.strftime('%b %d')}, {hour}:{local.strftime('%M %p')}"


def build_profile_card_html(
    summary: dict[str, object], *, today: date | None = None
) -> str:
    profile = summary["profile"]
    current_day = today or date.today()
    full_name = str(profile["full_name"])
    device = summary.get("device_status") or {}
    device_status = str(device.get("status", "unknown"))
    device_badges = {
        "online": ("Device online", "device-online"),
        "offline": ("Device offline", "device-offline"),
    }
    device_badge, device_class = device_badges.get(
        device_status, ("Device status unavailable", "device-unavailable")
    )
    return f"""
<div class="resident-card" id="resident-profile">
  <div class="resident-avatar" aria-hidden="true">{escape(_initials(full_name))}</div>
  <div>
    <div class="resident-name">{escape(full_name)}</div>
    <div class="resident-meta">Resident ID {escape(str(profile['elderly_id']))} &nbsp;·&nbsp; {escape(_age(profile.get('date_of_birth'), current_day))}</div>
    <div class="resident-badges">
      <span class="status-pill profile-active">Profile active</span>
      <span class="status-pill {device_class}">{escape(device_badge)}</span>
    </div>
  </div>
  <div class="contact-block">
    <div class="contact-label">Emergency contact</div>
    <div class="contact-name">{escape(str(profile.get('emergency_contact_name') or 'Not set'))}</div>
    <div class="contact-phone">{escape(str(profile.get('emergency_contact_phone') or 'No phone provided'))}</div>
  </div>
</div>
"""


def render_profile(summary: dict[str, object]) -> None:
    st.markdown(build_profile_card_html(summary), unsafe_allow_html=True)


def _metric_note(field: str, value: object) -> tuple[str, str]:
    if value is None:
        return "No recent reading", "info"
    numeric = float(value)
    if field == "heart_rate" and (numeric < 50 or numeric > 120):
        return "Outside normal range", "danger"
    if field == "oxygen_level" and numeric < 92:
        return "Below normal range", "warning"
    if field == "temperature" and numeric > 38:
        return "Above normal range", "warning"
    return "Within normal range", "success"


def build_health_metric_cards_html(summary: dict[str, object]) -> str:
    health = summary.get("latest_health") or {}
    activity = summary.get("latest_activity") or {}
    activity_value = str(activity.get("value", "no data"))
    if activity_value == "active":
        activity_note, activity_tone = "Movement detected", "info"
    elif activity_value == "no data":
        activity_note, activity_tone = "Waiting for activity data", "info"
    else:
        activity_note, activity_tone = "No recent movement", "warning"
    metric_specs = []
    for label, field, unit in (
        ("Heart rate", "heart_rate", "bpm"),
        ("Oxygen", "oxygen_level", "%"),
        ("Temperature", "temperature", "C"),
    ):
        value = health.get(field)
        note, tone = _metric_note(field, value)
        metric_specs.append((label, "No data" if value is None else str(value), unit, note, tone))
    metric_specs.append(("Activity", activity_value.title(), "", activity_note, activity_tone))
    cards = "".join(
        f"""
<div class="metric-card">
  <div class="metric-top"><span class="metric-label">{escape(label)}</span><span class="metric-dot {escape(tone)}"></span></div>
  <div class="metric-value">{escape(value)}<span class="metric-unit">{escape(unit)}</span></div>
  <div class="metric-note">{escape(note)}</div>
</div>
"""
        for label, value, unit, note, tone in metric_specs
    )
    return f'<div class="vitals-grid">{cards}</div>'


def render_current_health(summary: dict[str, object]) -> None:
    health = summary.get("latest_health") or {}
    st.markdown(build_health_metric_cards_html(summary), unsafe_allow_html=True)
    if health:
        st.caption(f"Latest reading received {health.get('recorded_at', 'time unavailable')}")


def build_device_card_html(summary: dict[str, object]) -> str:
    device = summary.get("device_status") or {}
    if not device:
        return """
<div class="device-card" id="devices">
  <div class="card-header"><div><div class="card-title">Device status</div><div class="card-caption">No device data</div></div><span class="device-status unavailable">Unavailable</span></div>
</div>
"""
    status = str(device.get("status", "unknown"))
    status_label, status_class = {
        "online": ("Device online", "online"),
        "offline": ("Device offline", "offline"),
    }.get(status, ("Device status unavailable", "unavailable"))
    return f"""
<div class="device-card" id="devices">
  <div class="card-header"><div><div class="card-title">Device status</div><div class="card-caption">Resident monitoring device</div></div><span class="device-status {status_class}">{status_label}</span></div>
  <div class="meta">Last seen {_caregiver_time(device.get('last_seen'))}</div>
</div>
"""


def render_device_card(summary: dict[str, object]) -> None:
    st.markdown(build_device_card_html(summary), unsafe_allow_html=True)


def build_activity_device_html(summary: dict[str, object]) -> str:
    activity = summary.get("latest_activity") or {}
    device = summary.get("device_status") or {}
    activity_value = str(activity.get("value", "no data"))
    device_value = str(device.get("status", "no data"))
    activity_class = "status-active" if activity_value == "active" else (
        "status-inactive" if activity_value == "inactive" else "status-no-data"
    )
    device_class = "status-online" if device_value == "online" else (
        "status-offline" if device_value == "offline" else "status-no-data"
    )
    return f"""
<div class="telemetry-grid">
  <div class="telemetry-row {activity_class}">
    <strong>Activity: {escape(activity_value)}</strong>
    <span class="meta">Last received {_caregiver_time(activity.get('received_at'))}</span>
  </div>
  <div class="telemetry-row {device_class}">
    <strong>Device: {escape(device_value)}</strong>
    <span class="meta">Last seen {_caregiver_time(device.get('last_seen'))}</span>
  </div>
</div>
"""


def render_activity_and_device(summary: dict[str, object]) -> None:
    st.markdown(build_activity_device_html(summary), unsafe_allow_html=True)


def build_activity_history_html(records: list[dict[str, object]]) -> str:
    if not records:
        return '<div class="empty-state">No activity history yet.</div>'
    return "".join(
        f"""
<div class="alert-row">
  <strong>Activity: {escape(str(record.get('value', 'unknown')))}</strong>
  <div class="meta">Observed {_caregiver_time(record.get('recorded_at'))}</div>
  <div class="meta alert-time">Received {_caregiver_time(record.get('received_at'))}</div>
</div>
"""
        for record in records
    )


def render_activity_history(records: list[dict[str, object]]) -> None:
    st.markdown(build_activity_history_html(records), unsafe_allow_html=True)


def build_reminder_rows_html(records: list[dict[str, object]]) -> str:
    if not records:
        return '<div class="empty-state">No reminders in this window.</div>'
    return "".join(
        f"""
<div class="reminder-row reminder-{escape(str(record.get('status', 'pending')))}">
  <strong>{escape(str(record.get('status', 'pending')).title())}: {escape(str(record.get('medicine_name', 'medicine')))}</strong>
  <div>{escape(str(record.get('instructions') or 'No instructions'))}</div>
  <div class="meta">Scheduled {_caregiver_time(record.get('scheduled_for'))}</div>
</div>
"""
        for record in records
    )


def render_reminders(records: list[dict[str, object]], on_taken) -> None:
    if not records:
        st.markdown(build_reminder_rows_html(records), unsafe_allow_html=True)
        return
    for record in records:
        st.markdown(build_reminder_rows_html([record]), unsafe_allow_html=True)
        if record.get("status") in {"pending", "missed"}:
            medicine_name = str(record.get("medicine_name", "medicine"))
            scheduled_for = _compact_caregiver_time(record.get("scheduled_for"))
            reminder_id = str(record["reminder_id"])
            if st.button(
                f"Mark {medicine_name} taken ({scheduled_for})",
                key=f"take-{reminder_id}",
                use_container_width=False,
            ):
                on_taken(reminder_id, medicine_name)


def _measurement(health: dict[str, object], field: str, suffix: str) -> str:
    value = health.get(field)
    return "NO DATA" if value is None else f"{value}{suffix}"


def render_health_charts(records: list[dict[str, object]]) -> None:
    if not records:
        st.markdown('<div class="empty-state">No processed health history yet.</div>', unsafe_allow_html=True)
        return
    vital_chart, _ = build_health_charts(records)
    st.altair_chart(vital_chart, use_container_width=True)


def build_health_charts(
    records: list[dict[str, object]],
) -> tuple[alt.Chart, alt.Chart]:
    frame = pd.DataFrame(records)
    frame["recorded_at"] = pd.to_datetime(frame["recorded_at"], utc=True)
    frame = frame.sort_values("recorded_at")
    frame = frame[frame["recorded_at"] >= frame["recorded_at"].max() - timedelta(hours=12)]

    vital_frame = frame.melt(
        id_vars=["recorded_at"],
        value_vars=["heart_rate", "oxygen_level"],
        var_name="measure",
        value_name="value",
    )
    vital_frame["measure"] = vital_frame["measure"].map(
        {"heart_rate": "Heart rate", "oxygen_level": "Oxygen level"}
    )
    vital_chart = (
        alt.Chart(vital_frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("recorded_at:T", title="Recorded at"),
            y=alt.Y("value:Q", title="BPM / percent", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "measure:N",
                scale=alt.Scale(
                    domain=["Heart rate", "Oxygen level"],
                    range=["#2f7f6d", "#4c78df"],
                ),
                title=None,
            ),
            tooltip=[
                alt.Tooltip("recorded_at:T", title="Recorded at"),
                alt.Tooltip("measure:N", title="Measure"),
                alt.Tooltip("value:Q", title="Value"),
            ],
        )
        .properties(height=260)
    )
    temperature_chart = (
        alt.Chart(frame)
        .mark_line(point=True, color="#e39a2c")
        .encode(
            x=alt.X("recorded_at:T", title="Recorded at"),
            y=alt.Y("temperature:Q", title="Temperature C", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("recorded_at:T", title="Recorded at"),
                alt.Tooltip("temperature:Q", title="Temperature C"),
            ],
        )
        .properties(height=220)
    )
    return _configure_mono(vital_chart), _configure_mono(temperature_chart)


def _configure_mono(chart: alt.Chart) -> alt.Chart:
    return (
        chart.configure(font=DASHBOARD_FONT_STACK)
        .configure_axis(
            labelFont=DASHBOARD_FONT_STACK,
            titleFont=DASHBOARD_FONT_STACK,
        )
        .configure_legend(
            labelFont=DASHBOARD_FONT_STACK,
            titleFont=DASHBOARD_FONT_STACK,
        )
        .configure_title(font=DASHBOARD_FONT_STACK)
    )


def render_alerts(alerts: list[dict[str, object]], on_status=None) -> None:
    if not alerts:
        st.markdown('<div class="empty-state state-success">No recent alerts.</div>', unsafe_allow_html=True)
        return
    for alert in alerts:
        severity = str(alert.get("severity", "warning"))
        severity_label = "Emergency" if severity == "emergency" else "Warning"
        alert_type = str(alert.get("alert_type", "alert")).replace("_", " ")
        created_at = _caregiver_time(alert.get("created_at"))
        compact_created_at = _compact_caregiver_time(alert.get("created_at"))
        st.markdown(
            f"""
<div class="alert-row alert-{escape(severity)}">
  <strong>{severity_label}</strong>
  <div><strong>{escape(alert_type)}</strong><br>
    <span class="alert-message">{escape(str(alert.get('message', '')))}</span></div>
  <div class="meta alert-time">Created {created_at}<br>{escape(str(alert.get('status', '')).title())}</div>
</div>
""",
            unsafe_allow_html=True,
        )
        if on_status is not None and alert.get("status") != "resolved":
            alert_id = str(alert["alert_id"])
            context = f"{alert_type} ({compact_created_at})"
            if alert.get("status") == "unresolved" and st.button(
                f"Acknowledge {context}", key=f"ack-{alert_id}"
            ):
                on_status(alert_id, "acknowledged", alert_type)
            if st.button(f"Resolve {context}", key=f"resolve-{alert_id}"):
                on_status(alert_id, "resolved", alert_type)

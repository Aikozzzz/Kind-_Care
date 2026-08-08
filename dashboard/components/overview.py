from html import escape

import streamlit as st


def _risk_state(summary: dict[str, object]) -> tuple[str, str, str]:
    risk = str(summary.get("current_risk", "normal"))
    device = summary.get("device_status") or {}
    device_state = str(device.get("status", "unknown"))
    if risk == "emergency":
        return "Critical", "critical", "Immediate review required"
    if risk == "warning":
        return "Needs attention", "warning", "Review the latest signals"
    if device_state == "offline":
        return "Offline", "offline", "Device connection needs review"
    if device_state == "unknown":
        return "Unknown", "unknown", "No device status available"
    return "Stable", "stable", "No current concerns"


def _navigate(view: str, resident_id: str) -> None:
    st.query_params.update(view=view, resident=resident_id)
    st.rerun()


def build_overview_stats_html(snapshots: list[dict[str, object]]) -> str:
    critical = 0
    attention = 0
    offline = 0
    for snapshot in snapshots:
        summary = snapshot["summary"]
        state, _, _ = _risk_state(summary)
        critical += state == "Critical"
        attention += state in {"Critical", "Needs attention", "Offline"}
        offline += str((summary.get("device_status") or {}).get("status", "")) == "offline"
    stats = [
        ("Active residents", len(snapshots), "stable", "Currently monitored"),
        ("Critical alerts", critical, "critical", "Needs immediate review"),
        ("Needs attention", attention, "warning", "Residents or devices"),
        ("Devices offline", offline, "offline", "Connection needs review"),
    ]
    cards = "".join(
        f"""
<div class="stat-card stat-{tone}">
  <div class="stat-card-top"><span>{escape(label)}</span><span class="stat-marker" aria-hidden="true"></span></div>
  <strong>{value}</strong>
  <small>{escape(note)}</small>
</div>
"""
        for label, value, tone, note in stats
    )
    return f'<div class="overview-stats" aria-label="Overview summary">{cards}</div>'


def render_overview_stats(snapshots: list[dict[str, object]]) -> None:
    st.markdown(build_overview_stats_html(snapshots), unsafe_allow_html=True)


def build_attention_html(snapshots: list[dict[str, object]]) -> str:
    attention: list[tuple[dict[str, object], dict[str, object], str, str]] = []
    for snapshot in snapshots:
        profile = snapshot["profile"]
        summary = snapshot["summary"]
        state, tone, note = _risk_state(summary)
        if state == "Stable":
            continue
        alert = summary.get("current_alert") or {}
        reason = str(alert.get("message") or note)
        attention.append((profile, summary, state, reason))
    if not attention:
        return '<div class="empty-state state-success"><strong>No residents need attention.</strong><span>New alerts and connection problems will appear here.</span></div>'
    rows = []
    for profile, summary, state, reason in attention:
        resident_id = str(profile.get("elderly_id", ""))
        resident_name = str(profile.get("full_name", "Resident"))
        _, tone, _ = _risk_state(summary)
        rows.append(
            f"""
<div class="attention-card attention-{tone}">
  <span class="attention-marker" aria-hidden="true">!</span>
  <span class="attention-copy"><strong>{escape(resident_name)}</strong><small>{escape(resident_id)} · {escape(reason)}</small></span>
  <span class="attention-state">{escape(state)}<small>Review alerts</small></span>
</div>
"""
        )
    return f'<div class="attention-list">{"".join(rows)}</div>'


def render_attention(snapshots: list[dict[str, object]]) -> None:
    if not any(_risk_state(snapshot["summary"])[0] != "Stable" for snapshot in snapshots):
        st.markdown(build_attention_html(snapshots), unsafe_allow_html=True)
        return
    for index, snapshot in enumerate(snapshots):
        profile = snapshot["profile"]
        summary = snapshot["summary"]
        state, tone, note = _risk_state(summary)
        if state == "Stable":
            continue
        resident_id = str(profile.get("elderly_id", ""))
        resident_name = str(profile.get("full_name", "Resident"))
        alert = summary.get("current_alert") or {}
        reason = str(alert.get("message") or note)
        left, right = st.columns([5, 1], vertical_alignment="center")
        with left:
            st.markdown(
                f'<div class="attention-card attention-{tone}"><span class="attention-marker" aria-hidden="true">!</span><span class="attention-copy"><strong>{escape(resident_name)}</strong><small>{escape(resident_id)} · {escape(reason)}</small></span><span class="attention-state">{escape(state)}<small>Review alerts</small></span></div>',
                unsafe_allow_html=True,
            )
        with right:
            st.button(
                "Review alerts",
                key=f"review-attention-{resident_id}-{index}",
                use_container_width=True,
                on_click=_navigate,
                args=("alerts", resident_id),
            )


def build_resident_directory_html(snapshots: list[dict[str, object]]) -> str:
    if not snapshots:
        return '<div class="empty-state"><strong>No resident summaries available.</strong><span>Check access or try refreshing the dashboard.</span></div>'
    ordered = sorted(
        snapshots,
        key=lambda item: {"Critical": 0, "Needs attention": 1, "Offline": 2, "Unknown": 3, "Stable": 4}.get(
            _risk_state(item["summary"])[0], 5
        ),
    )
    cards = []
    for snapshot in ordered:
        profile = snapshot["profile"]
        summary = snapshot["summary"]
        state, tone, note = _risk_state(summary)
        resident_id = str(profile.get("elderly_id", ""))
        name = str(profile.get("full_name", "Resident"))
        device = summary.get("device_status") or {}
        latest = summary.get("latest_health") or {}
        pulse = latest.get("heart_rate")
        cards.append(
            f"""
<div class="directory-card directory-{tone}">
  <span class="directory-avatar" aria-hidden="true">{escape(''.join(part[0] for part in name.split()[:2]) or 'KC')}</span>
  <span class="directory-main"><strong>{escape(name)}</strong><small>{escape(resident_id)}</small></span>
  <span class="directory-status"><b>{escape(state)}</b><small>{escape(note)}</small></span>
  <span class="directory-reading"><b>{escape(str(pulse) if pulse is not None else 'No data')}</b><small>Heart rate</small></span>
  <span class="directory-device"><b>{escape(str(device.get('status', 'Unknown')).title())}</b><small>Device</small></span>
</div>
"""
        )
    return f'<div class="directory-list">{"".join(cards)}</div>'


def render_resident_directory(snapshots: list[dict[str, object]]) -> None:
    if not snapshots:
        st.markdown(build_resident_directory_html(snapshots), unsafe_allow_html=True)
        return
    ordered = sorted(
        snapshots,
        key=lambda item: {"Critical": 0, "Needs attention": 1, "Offline": 2, "Unknown": 3, "Stable": 4}.get(
            _risk_state(item["summary"])[0], 5
        ),
    )
    for index, snapshot in enumerate(ordered):
        profile = snapshot["profile"]
        resident_id = str(profile.get("elderly_id", ""))
        left, right = st.columns([5, 1], vertical_alignment="center")
        with left:
            st.markdown(build_resident_directory_html([snapshot]), unsafe_allow_html=True)
        with right:
            st.button(
                "Open resident",
                key=f"open-resident-{resident_id}-{index}",
                use_container_width=True,
                on_click=_navigate,
                args=("resident", resident_id),
            )


def build_global_alerts_html(snapshots: list[dict[str, object]]) -> str:
    rows: list[tuple[dict[str, object], dict[str, object]]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        profile = snapshot["profile"]
        summary = snapshot["summary"]
        alerts = []
        current = summary.get("current_alert")
        if isinstance(current, dict):
            alerts.append(current)
        alerts.extend(alert for alert in summary.get("recent_alerts", []) if isinstance(alert, dict))
        for alert in alerts:
            alert_id = str(alert.get("alert_id", ""))
            if alert_id and alert_id in seen:
                continue
            if alert_id:
                seen.add(alert_id)
            if str(alert.get("status", "")) in {"unresolved", "acknowledged"}:
                rows.append((profile, alert))
    if not rows:
        return '<div class="empty-state state-success"><strong>No active alerts.</strong><span>Everything looks stable right now.</span></div>'
    severity_order = {"emergency": 0, "warning": 1}
    rows.sort(key=lambda item: severity_order.get(str(item[1].get("severity")), 2))
    cards = []
    for profile, alert in rows:
        severity = str(alert.get("severity", "warning"))
        resident_id = str(profile.get("elderly_id", ""))
        resident_name = str(profile.get("full_name", "Resident"))
        alert_type = str(alert.get("alert_type", "Alert")).replace("_", " ")
        cards.append(
            f"""
<div class="attention-card attention-{'critical' if severity == 'emergency' else 'warning'}">
  <span class="attention-marker" aria-hidden="true">!</span>
  <span class="attention-copy"><strong>{escape(resident_name)} · {escape(alert_type.title())}</strong><small>{escape(resident_id)} · {escape(str(alert.get('message', 'Review this alert.')))}</small></span>
  <span class="attention-state">{escape('Critical' if severity == 'emergency' else 'Warning')}<small>{escape(str(alert.get('status', '')).title())}</small></span>
</div>
"""
        )
    return f'<div class="attention-list">{"".join(cards)}</div>'


def render_global_alerts(snapshots: list[dict[str, object]]) -> None:
    rows: list[tuple[dict[str, object], dict[str, object]]] = []
    seen: set[str] = set()
    for snapshot in snapshots:
        current = snapshot["summary"].get("current_alert")
        alerts = [current] if isinstance(current, dict) else []
        alerts.extend(alert for alert in snapshot["summary"].get("recent_alerts", []) if isinstance(alert, dict))
        for alert in alerts:
            alert_id = str(alert.get("alert_id", ""))
            if alert_id and alert_id in seen:
                continue
            if alert_id:
                seen.add(alert_id)
            if str(alert.get("status", "")) in {"unresolved", "acknowledged"}:
                rows.append((snapshot["profile"], alert))
    if not rows:
        st.markdown(build_global_alerts_html(snapshots), unsafe_allow_html=True)
        return
    rows.sort(key=lambda item: {"emergency": 0, "warning": 1}.get(str(item[1].get("severity")), 2))
    for index, (profile, alert) in enumerate(rows):
        resident_id = str(profile.get("elderly_id", ""))
        severity = str(alert.get("severity", "warning"))
        left, right = st.columns([5, 1], vertical_alignment="center")
        with left:
            st.markdown(
                build_global_alerts_html([{"profile": profile, "summary": {"current_alert": alert, "recent_alerts": []}}]),
                unsafe_allow_html=True,
            )
        with right:
            st.button(
                "Review alert",
                key=f"review-global-alert-{resident_id}-{index}",
                use_container_width=True,
                type="primary" if severity == "emergency" else "secondary",
                on_click=_navigate,
                args=("alerts", resident_id),
            )

import os
import math
import re
from datetime import datetime
from html import escape
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

from dashboard.api import DashboardAPIError, DashboardNotFound, DashboardUnavailable, KindCareAPI
from dashboard.components.live import build_live_panel_html
from dashboard.components.summary import (
    render_alerts,
    render_activity_and_device,
    render_current_health,
    render_device_card,
    render_health_charts,
    render_activity_history,
    render_profile,
    render_reminders,
    section_label,
)
from dashboard.styles import DASHBOARD_CSS


st.set_page_config(
    page_title="KindCare Caregiver Console",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

api_url = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
public_ws_url = os.environ.get("PUBLIC_WS_BASE_URL", "ws://127.0.0.1:8000")
websocket_heartbeat_interval = float(
    os.environ.get("WEBSOCKET_HEARTBEAT_INTERVAL", "15.0")
)
if not math.isfinite(websocket_heartbeat_interval) or websocket_heartbeat_interval <= 0:
    raise ValueError("WEBSOCKET_HEARTBEAT_INTERVAL must be a positive finite number")
dashboard_refresh_seconds = float(os.environ.get("DASHBOARD_REFRESH_SECONDS", "5.0"))
if not math.isfinite(dashboard_refresh_seconds) or dashboard_refresh_seconds <= 0:
    raise ValueError("DASHBOARD_REFRESH_SECONDS must be a positive finite number")
api = KindCareAPI(api_url)

VIEW_CONFIG = {
    "overview": ("Overview", "Care overview", "Live monitoring"),
    "resident": ("Resident", "Resident profile", "Identity and health history"),
    "alerts": ("Alerts", "Alerts", "Active and recent care alerts"),
    "medication": ("Medication", "Medication", "Reminder schedule and actions"),
    "devices": ("Devices", "Devices", "Monitoring device and activity status"),
}
requested_view = str(st.query_params.get("view", "overview")).lower()
selected_view = requested_view if requested_view in VIEW_CONFIG else "overview"
_, view_title, view_subtitle = VIEW_CONFIG[selected_view]
initial_resident_query = str(
    st.query_params.get(
        "resident", os.environ.get("DEFAULT_ELDERLY_ID", "E001")
    )
)
if not initial_resident_query or len(initial_resident_query) > 50:
    initial_resident_query = os.environ.get("DEFAULT_ELDERLY_ID", "E001")

heading, control, action = st.columns([1.7, 1, 0.34], vertical_alignment="bottom")
with heading:
    st.title(view_title)
    st.markdown(
        f'<div class="page-subtitle">{datetime.now().strftime("%A, %B %d")} · {view_subtitle}</div>',
        unsafe_allow_html=True,
    )
with control:
    search_query = st.text_input(
        "Search resident or ID",
        value=initial_resident_query,
        max_chars=50,
        label_visibility="collapsed",
        placeholder="Search resident or ID",
    ).strip()
with action:
    st.button("Refresh", use_container_width=True)

if not search_query:
    st.error("Enter a resident name or ID.")
    st.stop()
if len(search_query) > 50:
    st.error("Search must be 50 characters or fewer.")
    st.stop()


def matching_profiles(
    profiles: list[dict[str, object]], query: str
) -> list[dict[str, object]]:
    exact_id = [profile for profile in profiles if profile.get("elderly_id") == query]
    if exact_id:
        return exact_id
    folded = query.casefold()
    exact_name = [
        profile
        for profile in profiles
        if str(profile.get("full_name", "")).casefold() == folded
    ]
    if exact_name:
        return exact_name
    return [
        profile
        for profile in profiles
        if folded in str(profile.get("full_name", "")).casefold()
    ]


def render_api_error(error: DashboardAPIError) -> None:
    if isinstance(error, DashboardNotFound):
        st.error(str(error))
    elif isinstance(error, DashboardUnavailable):
        st.error(f"Monitoring service unavailable. {error}")
    else:
        st.error(f"Backend response could not be read. {error}")


def run_action(action, success_message: str) -> None:
    try:
        action()
    except DashboardAPIError as error:
        st.session_state.action_error = f"Action failed. {error}"
    else:
        st.session_state.action_success = success_message
    st.rerun()


def render_sidebar() -> None:
    navigation_links = []
    encoded_resident = quote(elderly_id, safe="")
    for key, (label, _, _) in VIEW_CONFIG.items():
        class_name = "active" if key == selected_view else ""
        aria_current = ' aria-current="page"' if key == selected_view else ""
        navigation_links.append(
            f'<a class="{class_name}" href="?view={key}&amp;resident={encoded_resident}" '
            f'target="_self"{aria_current}>'
            f'<span class="nav-icon {key}" aria-hidden="true"></span>{label}</a>'
        )
    navigation = "".join(navigation_links)
    with st.sidebar:
        st.markdown(
            f"""
<div class="brand-lockup">
  <div class="brand-mark">+</div>
  <div class="brand-copy"><strong>KindCare</strong><span>Caregiver Console</span></div>
</div>
<nav class="side-nav" aria-label="Caregiver dashboard sections">
  {navigation}
</nav>
""",
            unsafe_allow_html=True,
        )
        components.html(
            build_live_panel_html(
                public_ws_url,
                elderly_id,
                websocket_heartbeat_interval,
            ),
            height=214,
            scrolling=False,
        )
        with st.container(key="sidebar-caregiver"):
            st.markdown(
                """
<div class="caregiver-card">
  <div class="caregiver-avatar">KC</div>
  <div><strong>Caregiver</strong><span>Primary caregiver · local demo</span></div>
</div>
""",
                unsafe_allow_html=True,
            )


try:
    profiles = api.get_profiles(limit=100)
except DashboardAPIError as error:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", search_query):
        elderly_id = search_query
    else:
        st.error(f"Resident search is unavailable. {error}")
        st.stop()
else:
    matches = matching_profiles(profiles, search_query)
    if not matches and re.fullmatch(r"[A-Za-z0-9_-]{1,50}", search_query):
        elderly_id = search_query
    elif not matches:
        st.error(f'No active resident matches "{search_query}".')
        st.stop()
    elif len(matches) == 1:
        elderly_id = str(matches[0]["elderly_id"])
    else:
        profile_by_id = {str(profile["elderly_id"]): profile for profile in matches}
        elderly_id = st.selectbox(
            "Choose a resident",
            options=list(profile_by_id),
            format_func=lambda profile_id: (
                f"{profile_by_id[profile_id].get('full_name', 'Unnamed resident')} "
                f"({profile_id})"
            ),
        )


if action_success := st.session_state.pop("action_success", None):
    st.success(action_success)
if action_error := st.session_state.pop("action_error", None):
    st.error(action_error)


render_sidebar()


def render_risk_banner(summary: dict[str, object]) -> None:
    raw_risk = summary.get("current_risk")
    risk = (
        raw_risk
        if isinstance(raw_risk, str)
        and raw_risk in ("normal", "warning", "emergency")
        else "normal"
    )
    if risk == "normal":
        return
    candidate = summary.get("current_alert")
    alert = candidate if isinstance(candidate, dict) else {}
    title = "Emergency state detected" if risk == "emergency" else "Care warning detected"
    detail = str(alert.get("message") or "Review the latest monitoring signals.")
    with st.container(key=f"risk-banner-{risk}"):
        copy, action_column = st.columns([5, 1.2], vertical_alignment="center")
        with copy:
            st.markdown(
                f"""
<div class="risk-banner-copy {risk}">
  <div class="risk-symbol">!</div>
  <div><div class="risk-title">{escape(title)}</div><div class="risk-detail">{escape(detail)}</div></div>
</div>
""",
                unsafe_allow_html=True,
            )
        if alert.get("status") == "unresolved" and alert.get("alert_id"):
            with action_column:
                alert_id = str(alert["alert_id"])
                alert_type = str(alert.get("alert_type", "alert")).replace("_", " ")
                if st.button(
                    "Acknowledge alert",
                    key=f"banner-ack-{alert_id}",
                    use_container_width=True,
                    type="primary",
                ):
                    run_action(
                        lambda: api.update_alert_status(alert_id, "acknowledged"),
                        f"{alert_type} alert acknowledged.",
                    )


def render_health_trends_panel(health_history: list[dict[str, object]]) -> None:
    with st.container(border=True, key="health-trends"):
        st.markdown(
            '<div class="card-header"><div><div class="card-title">Health trends</div><div class="card-caption">Latest 12 hours</div></div><span class="status-pill profile-active">Latest</span></div>',
            unsafe_allow_html=True,
        )
        render_health_charts(health_history)


def render_medication_panel(
    reminders: list[dict[str, object]], on_taken
) -> None:
    with st.container(border=True, key="medication-card"):
        st.markdown(
            '<div class="card-header"><div><div class="card-title">Medication</div><div class="card-caption">Upcoming and recent reminders</div></div><span class="meta">Today</span></div>',
            unsafe_allow_html=True,
        )
        render_reminders(reminders, on_taken)


@st.fragment(run_every=dashboard_refresh_seconds)
def render_dashboard_snapshot() -> None:
    try:
        summary = api.get_summary(elderly_id)
    except DashboardAPIError as error:
        render_api_error(error)
        return
    health_history = []
    alert_history = []
    activity_history = []
    try:
        if selected_view in {"overview", "resident"}:
            health_history = api.get_health(elderly_id, limit=50)
        if selected_view in {"overview", "alerts"}:
            alert_history = api.get_alerts(elderly_id, limit=20)
        if selected_view in {"overview", "resident", "devices"}:
            activity_history = api.get_activity(elderly_id, limit=50)
    except DashboardAPIError as error:
        render_api_error(error)
        return
    reminders = [
        *summary.get("upcoming_reminders", []),
        *summary.get("recent_reminders", []),
    ]
    on_reminder_taken = lambda reminder_id, medicine_name: run_action(
        lambda: api.mark_reminder_taken(elderly_id, reminder_id),
        f"{medicine_name} reminder marked taken.",
    )
    on_alert_status = lambda alert_id, target, alert_type: run_action(
        lambda: api.update_alert_status(alert_id, target),
        f"{alert_type} alert {target}.",
    )

    render_profile(summary)
    render_risk_banner(summary)

    if selected_view == "overview":
        render_current_health(summary)
        trends, context = st.columns([1.85, 1], gap="medium")
        with trends:
            render_health_trends_panel(health_history)
        with context:
            render_device_card(summary)
            render_medication_panel(reminders[:4], on_reminder_taken)

        history, alerts = st.columns([1, 1], gap="medium")
        with history:
            section_label("", "Activity history")
            render_activity_history(activity_history[:12])
        with alerts:
            section_label("", "Recent alerts")
            render_alerts(alert_history, on_alert_status)
    elif selected_view == "resident":
        render_current_health(summary)
        render_health_trends_panel(health_history)
        section_label("", "Activity history")
        render_activity_history(activity_history)
    elif selected_view == "alerts":
        section_label("", "Recent alerts")
        render_alerts(alert_history, on_alert_status)
    elif selected_view == "medication":
        render_medication_panel(reminders, on_reminder_taken)
    else:
        render_device_card(summary)
        section_label("", "Current monitoring signals")
        render_activity_and_device(summary)
        section_label("", "Activity history")
        render_activity_history(activity_history)


render_dashboard_snapshot()

st.markdown('<div class="section-label">Local academic demonstration · not for clinical use</div>', unsafe_allow_html=True)

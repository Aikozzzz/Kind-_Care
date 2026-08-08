import os
import math
import re
from datetime import date, datetime
from html import escape
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components

from dashboard.api import DashboardAPIError, DashboardNotFound, DashboardUnavailable, KindCareAPI
from dashboard.components.live import build_live_panel_html
from dashboard.components.overview import (
    render_attention,
    render_global_alerts,
    render_overview_stats,
    render_resident_directory,
)
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
unauthenticated_test_mode = os.environ.get("DASHBOARD_AUTH_DISABLED", "false").casefold() == "true"
if not unauthenticated_test_mode and not st.session_state.get("access_token"):
    st.title("KindCare sign in")
    st.markdown('<div class="page-subtitle">Sign in to access authorized resident records.</div>', unsafe_allow_html=True)
    login_name = st.text_input("Login name")
    password = st.text_input("Password", type="password")
    if st.button("Sign in", type="primary"):
        try:
            login_data = KindCareAPI(api_url).login(login_name, password)
        except DashboardAPIError as error:
            st.error(f"Sign in failed. {error}")
        else:
            st.session_state.access_token = login_data["access_token"]
            st.session_state.account = login_data["account"]
            st.rerun()
    st.stop()

api = KindCareAPI(api_url, access_token=st.session_state.get("access_token"))
account = st.session_state.get("account") or {}
is_admin = account.get("role") == "admin"

VIEW_CONFIG = {
    "overview": ("Overview", "Care overview", "Live monitoring"),
    "residents": ("Residents", "Residents", "Find and review residents"),
    "monitoring": ("Monitoring", "Monitoring", "Current resident monitoring"),
    "health": ("Health", "Health", "Vitals and health history"),
    "activity": ("Activity", "Activity", "Movement and device activity"),
    "resident": ("Health", "Resident profile", "Identity and health history"),
    "alerts": ("Alerts", "Alerts", "Active and recent care alerts"),
    "medication": ("Medication", "Medication", "Reminder schedule and actions"),
    "devices": ("Devices", "Devices", "Monitoring device and activity status"),
}
if is_admin:
    VIEW_CONFIG["admin"] = (
        "Administration",
        "Administration",
        "Profiles, trusted family, and Telegram access",
    )
    VIEW_CONFIG["family"] = (
        "Family & Caregivers",
        "Family & Caregivers",
        "Trusted access and Telegram recipients",
    )
NAV_GROUPS = [
    ("Main", ("overview", "residents", "alerts", "monitoring")),
    ("Care", ("resident", "activity", "medication", "devices")),
]
if is_admin:
    NAV_GROUPS.append(("Management", ("family", "admin")))
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
        f'<div class="page-subtitle">{datetime.now().strftime("%A, %B %d")} · {view_subtitle} · refreshes every {dashboard_refresh_seconds:g}s</div>',
        unsafe_allow_html=True,
    )
with control:
    if selected_view in {"admin", "family"}:
        search_query = initial_resident_query
        st.markdown('<div class="toolbar-context">Management workspace</div>', unsafe_allow_html=True)
    else:
        search_query = st.text_input(
            "Search resident or ID",
            value=initial_resident_query,
            max_chars=50,
            label_visibility="collapsed",
            placeholder="Search resident or ID",
        ).strip()
with action:
    st.button("Refresh", use_container_width=True)

if selected_view not in {"admin", "family"} and not search_query:
    st.error("Enter a resident name or ID.")
    st.stop()
if selected_view not in {"admin", "family"} and len(search_query) > 50:
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
    for group_label, group_items in NAV_GROUPS:
        links = []
        for key in group_items:
            label = VIEW_CONFIG[key][0]
            class_name = "active" if key == selected_view else ""
            aria_current = ' aria-current="page"' if key == selected_view else ""
            links.append(
                f'<a class="{class_name}" href="?view={key}&amp;resident={encoded_resident}" '
                f'target="_self"{aria_current}>'
                f'<span class="nav-icon {key}" aria-hidden="true"></span>{label}</a>'
            )
        navigation_links.append(
            f'<div class="nav-group"><div class="nav-group-label">{escape(group_label)}</div>{"".join(links)}</div>'
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
        if selected_view not in {"admin", "family"}:
            components.html(
                build_live_panel_html(
                    public_ws_url,
                    elderly_id,
                    websocket_heartbeat_interval,
                    websocket_ticket,
                ),
                height=214,
                scrolling=False,
            )
        with st.container(key="sidebar-caregiver"):
            st.markdown(
                f"""
<div class="caregiver-card">
  <div class="caregiver-avatar">KC</div>
  <div><strong>{escape(str(account.get("display_name", "Caregiver")))}</strong><span>{"Administrator" if is_admin else "Authorized caregiver"} · local demo</span></div>
</div>
""",
                unsafe_allow_html=True,
            )
            if not unauthenticated_test_mode and st.button(
                "Sign out", key="sign-out", use_container_width=True
            ):
                try:
                    api.logout()
                finally:
                    st.session_state.pop("access_token", None)
                    st.session_state.pop("account", None)
                    st.rerun()


ADMIN_FAMILY_PERMISSIONS = [
    "read_profile",
    "read_dashboard",
    "query_telegram_status",
    "receive_telegram_alerts",
]


def _profile_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date(1950, 1, 1)


def render_admin_page() -> None:
    st.markdown(
        '<div class="page-subtitle">Manage archived-safe resident records and trusted family notification access.</div>',
        unsafe_allow_html=True,
    )
    st.subheader("Resident profiles")
    with st.expander("Add elderly profile", expanded=not profiles):
        with st.form("admin-add-profile"):
            first, second = st.columns(2)
            with first:
                new_id = st.text_input("Resident ID", max_chars=50)
                new_name = st.text_input("Full name")
                new_birth = st.date_input("Date of birth", value=date(1950, 1, 1))
                new_phone = st.text_input("Phone number")
                new_address = st.text_input("Address")
            with second:
                new_emergency_name = st.text_input("Emergency contact name")
                new_emergency_phone = st.text_input("Emergency contact phone")
                new_notes = st.text_area("Medical notes", height=120)
            if st.form_submit_button("Add profile", type="primary"):
                payload = {
                    "elderly_id": new_id.strip(),
                    "full_name": new_name.strip(),
                    "date_of_birth": new_birth.isoformat(),
                    "phone_number": new_phone.strip() or None,
                    "address": new_address.strip() or None,
                    "emergency_contact_name": new_emergency_name.strip() or None,
                    "emergency_contact_phone": new_emergency_phone.strip() or None,
                    "medical_notes": new_notes.strip() or None,
                }
                run_action(
                    lambda: api.create_profile(payload),
                    f"Resident profile {new_id.strip()} created.",
                )

    for profile in profiles:
        profile_id = str(profile.get("elderly_id", ""))
        active = bool(profile.get("active", False))
        state_label = "Active" if active else "Archived"
        with st.expander(f"{profile.get('full_name', 'Unnamed resident')} · {profile_id} · {state_label}"):
            if active:
                with st.form(f"admin-edit-profile-{profile_id}"):
                    first, second = st.columns(2)
                    with first:
                        edit_name = st.text_input(
                            "Full name", value=str(profile.get("full_name", "")), key=f"name-{profile_id}"
                        )
                        edit_birth = st.date_input(
                            "Date of birth", value=_profile_date(profile.get("date_of_birth")), key=f"birth-{profile_id}"
                        )
                        edit_phone = st.text_input(
                            "Phone number", value=str(profile.get("phone_number") or ""), key=f"phone-{profile_id}"
                        )
                        edit_address = st.text_input(
                            "Address", value=str(profile.get("address") or ""), key=f"address-{profile_id}"
                        )
                    with second:
                        edit_emergency_name = st.text_input(
                            "Emergency contact name", value=str(profile.get("emergency_contact_name") or ""), key=f"emergency-name-{profile_id}"
                        )
                        edit_emergency_phone = st.text_input(
                            "Emergency contact phone", value=str(profile.get("emergency_contact_phone") or ""), key=f"emergency-phone-{profile_id}"
                        )
                        edit_notes = st.text_area(
                            "Medical notes", value=str(profile.get("medical_notes") or ""), height=120, key=f"notes-{profile_id}"
                        )
                    if st.form_submit_button("Save profile", type="primary"):
                        updates = {
                            "full_name": edit_name.strip(),
                            "date_of_birth": edit_birth.isoformat(),
                            "phone_number": edit_phone.strip() or None,
                            "address": edit_address.strip() or None,
                            "emergency_contact_name": edit_emergency_name.strip() or None,
                            "emergency_contact_phone": edit_emergency_phone.strip() or None,
                            "medical_notes": edit_notes.strip() or None,
                        }
                        run_action(
                            lambda: api.update_profile(profile_id, updates),
                            f"Resident profile {profile_id} updated.",
                        )
                if st.checkbox("Confirm archive", key=f"confirm-archive-{profile_id}") and st.button("Archive profile", key=f"archive-{profile_id}"):
                    run_action(
                        lambda: api.archive_profile(profile_id),
                        f"Resident profile {profile_id} archived.",
                    )
            else:
                st.caption("Archived profiles are hidden from normal caregiver views and do not receive Telegram alerts.")
                if st.button("Restore profile", key=f"restore-{profile_id}", type="primary"):
                    run_action(
                        lambda: api.restore_profile(profile_id),
                        f"Resident profile {profile_id} restored.",
                    )

def render_family_page(family_profiles: list[dict[str, object]]) -> None:
    st.markdown(
        '<div class="page-subtitle">Connect trusted family accounts to residents without exposing technical identifiers.</div>',
        unsafe_allow_html=True,
    )
    active_profiles = [profile for profile in family_profiles if profile.get("active")]
    if not active_profiles:
        st.info("No active residents are available for family access.")
        return
    profile_options = {
        str(profile["elderly_id"]): str(profile.get("full_name", "Unnamed resident"))
        for profile in active_profiles
    }
    selected_family_resident = st.selectbox(
        "Resident", options=list(profile_options), format_func=lambda value: f"{profile_options[value]} ({value})"
    )
    with st.container(border=True):
        st.markdown("**Add a trusted family member**")
        st.caption("Create their account first. They will receive a one-time Telegram code after access is created.")
        with st.form("family-add-account"):
            login_name = st.text_input("Login name")
            display_name = st.text_input("Family member name")
            password = st.text_input("Temporary password", type="password")
            if st.form_submit_button("Create family access", type="primary"):
                def create_access() -> None:
                    created = api.create_account(
                        {
                            "login_name": login_name.strip(),
                            "display_name": display_name.strip(),
                            "password": password,
                            "role": "family",
                        }
                    )
                    api.create_relationship(
                        {
                            "account_id": created["account_id"],
                            "elderly_id": selected_family_resident,
                            "relationship_type": "family",
                            "permissions": ADMIN_FAMILY_PERMISSIONS,
                        }
                    )

                run_action(create_access, f"Family access created for {selected_family_resident}.")
    try:
        relationships = [
            relationship
            for relationship in api.get_relationships(selected_family_resident)
            if relationship.get("relationship_type") == "family"
            or relationship.get("account_role") == "family"
        ]
        bindings = api.get_telegram_bindings(selected_family_resident)
    except DashboardAPIError as error:
        st.error(f"Family access could not be loaded. {error}")
        return
    bindings_by_account = {str(binding["account_id"]): binding for binding in bindings}
    section_label("", "Trusted family members")
    if not relationships:
        st.markdown('<div class="empty-state"><strong>No trusted family members yet.</strong><span>Create an account above to begin.</span></div>', unsafe_allow_html=True)
        return
    for relationship in relationships:
        relationship_id = str(relationship["relationship_id"])
        account_id = str(relationship["account_id"])
        binding = bindings_by_account.get(account_id)
        with st.container(border=True):
            st.markdown(
                f"**{escape(str(relationship.get('account_display_name', 'Family member')))}** · "
                f"`{escape(str(relationship.get('account_login_name', '')))}`"
            )
            permissions = st.multiselect(
                "Permissions",
                options=ADMIN_FAMILY_PERMISSIONS,
                default=[permission for permission in relationship.get("permissions", []) if permission in ADMIN_FAMILY_PERMISSIONS],
                key=f"family-permissions-{relationship_id}",
            )
            controls = st.columns(3)
            with controls[0]:
                if st.button("Save permissions", key=f"family-save-{relationship_id}"):
                    run_action(lambda: api.update_relationship(relationship_id, permissions), "Family permissions updated.")
            with controls[1]:
                if binding:
                    st.success("Telegram linked")
                elif st.button("Generate Telegram code", key=f"family-code-{account_id}"):
                    try:
                        link = api.create_family_telegram_link(account_id)
                    except DashboardAPIError as error:
                        st.error(f"Link code could not be created. {error}")
                    else:
                        st.session_state[f"family-code-{account_id}"] = link["code"]
            with controls[2]:
                confirm_key = f"family-confirm-{relationship_id}"
                if st.checkbox("Confirm revoke", key=confirm_key) and st.button("Revoke access", key=f"family-revoke-{relationship_id}"):
                    run_action(lambda: api.revoke_relationship(relationship_id), "Family access revoked.")
            if binding:
                alert_state = "alert delivery enabled" if binding.get("receive_telegram_alerts") else "alert delivery disabled"
                st.caption(f"Private Telegram chat linked; {alert_state}.")
                confirm_key = f"telegram-confirm-{binding['telegram_user_id']}"
                if st.checkbox("Confirm unlink", key=confirm_key) and st.button("Unlink Telegram", key=f"family-unlink-{binding['telegram_user_id']}"):
                    run_action(lambda: api.revoke_telegram_binding(str(binding["telegram_user_id"])), "Telegram recipient unlinked.")
            code = st.session_state.get(f"family-code-{account_id}")
            if code:
                st.code(f"/link {code}")
                st.caption("Send this one-time command in a private chat with the KindCare bot.")


if selected_view in {"admin", "family"}:
    try:
        profiles = api.get_profiles(limit=100, include_inactive=True)
    except DashboardAPIError as error:
        st.error(f"Resident management is unavailable. {error}")
        st.stop()
    elderly_id = (
        str(profiles[0]["elderly_id"])
        if profiles
        else initial_resident_query
    )
elif selected_view == "residents":
    try:
        profiles = api.get_profiles(limit=100)
    except DashboardAPIError as error:
        st.error(f"Resident directory is unavailable. {error}")
        st.stop()
    elderly_id = str(profiles[0]["elderly_id"]) if profiles else initial_resident_query
else:
    try:
        profiles = api.get_profiles(limit=100)
    except DashboardAPIError as error:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,50}", search_query):
            elderly_id = search_query
            profiles = [{"elderly_id": elderly_id, "full_name": "Resident"}]
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


websocket_ticket = None
if not unauthenticated_test_mode and selected_view not in {"admin", "family"}:
    try:
        websocket_ticket = api.get_websocket_ticket(elderly_id)
    except DashboardAPIError:
        websocket_ticket = None


if action_success := st.session_state.pop("action_success", None):
    st.success(action_success)
if action_error := st.session_state.pop("action_error", None):
    st.error(action_error)


render_sidebar()

if selected_view in {"admin", "family"}:
    if selected_view == "family":
        render_family_page(profiles)
    else:
        render_admin_page()
    st.stop()


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


def load_selected_summary() -> dict[str, object] | None:
    try:
        return api.get_summary(elderly_id)
    except DashboardAPIError as error:
        render_api_error(error)
        return None


def load_profile_snapshots(candidate_profiles: list[dict[str, object]]) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    with st.spinner("Refreshing resident status..."):
        for profile in candidate_profiles:
            profile_id = str(profile.get("elderly_id", ""))
            try:
                profile_summary = api.get_summary(profile_id)
            except DashboardAPIError:
                continue
            snapshots.append({"profile": profile_summary.get("profile", profile), "summary": profile_summary})
    return snapshots


@st.fragment(run_every=dashboard_refresh_seconds)
def render_dashboard_snapshot() -> None:
    overview_snapshots: list[dict[str, object]] = []
    if selected_view in {"overview", "residents", "alerts"}:
        overview_profiles = profiles[:24]
        if not any(str(profile.get("elderly_id")) == elderly_id for profile in overview_profiles):
            selected_profile = next(
                (profile for profile in profiles if str(profile.get("elderly_id")) == elderly_id),
                None,
            )
            if selected_profile is not None:
                overview_profiles = [*overview_profiles, selected_profile]
        overview_snapshots = load_profile_snapshots(overview_profiles)
        if selected_view == "overview":
            selected_snapshot = next(
                (snapshot for snapshot in overview_snapshots if str(snapshot["profile"].get("elderly_id")) == elderly_id),
                None,
            )
            if selected_snapshot is None:
                st.error("The selected resident could not be loaded.")
                return
            summary = selected_snapshot["summary"]
        else:
            summary = load_selected_summary()
            if summary is None:
                return
    else:
        summary = load_selected_summary()
        if summary is None:
            return
    health_history = []
    alert_history = []
    activity_history = []
    try:
        if selected_view in {"overview", "resident", "health"}:
            health_history = api.get_health(elderly_id, limit=50)
        if selected_view in {"overview", "alerts"}:
            alert_history = api.get_alerts(elderly_id, limit=20)
        if selected_view in {"overview", "resident", "health", "activity", "monitoring", "devices"}:
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

    if selected_view == "overview":
        render_overview_stats(overview_snapshots)
        section_label("", "Needs attention")
        render_attention(overview_snapshots)
        section_label("", "Resident status")
        render_resident_directory(overview_snapshots)
    elif selected_view == "alerts":
        section_label("", "All active resident alerts")
        render_global_alerts(overview_snapshots)
    if selected_view != "residents":
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
    elif selected_view in {"resident", "health"}:
        render_current_health(summary)
        render_health_trends_panel(health_history)
        section_label("", "Activity history")
        render_activity_history(activity_history)
    elif selected_view == "residents":
        render_resident_directory(overview_snapshots)
    elif selected_view == "monitoring":
        render_device_card(summary)
        section_label("", "Current monitoring signals")
        render_activity_and_device(summary)
        section_label("", "Activity history")
        render_activity_history(activity_history)
    elif selected_view == "activity":
        render_activity_and_device(summary)
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

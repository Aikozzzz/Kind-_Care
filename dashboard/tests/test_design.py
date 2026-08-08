import re
from pathlib import Path

from dashboard.components.live import build_live_panel_html
from dashboard.styles import DASHBOARD_CSS


def test_css_follows_kindcare_design_contract() -> None:
    css = DASHBOARD_CSS.lower()

    assert "#f4f7f8" in css
    assert "#10493f" in css
    assert "#ffffff" in css
    assert '"inter", "aptos", "segoe ui", sans-serif' in css
    assert "max-width: 1180px" in css
    assert "border: 1px solid var(--border)" in css
    assert "border-radius: 16px" in css
    assert '[data-testid="stsidebar"]' in css
    assert "width: 248px" in css
    assert ".resident-card" in css
    assert ".vitals-grid" in css
    assert ".overview-grid" in css
    assert ".overview-stats" in css
    assert ".attention-card" in css
    assert ".directory-card" in css
    assert ".nav-group-label" in css
    assert "@media (max-width: 900px)" in css
    assert '[data-testid="sthorizontalblock"]' in css
    assert '[data-testid="stcolumn"]' in css
    assert "flex-direction: column" in css
    assert "width: 100%" in css
    assert "@media (max-width: 640px)" in css
    assert "box-shadow" not in css
    assert "gradient" not in css
    assert "url(" not in css
    assert ":focus-visible" in css
    for monitoring_color in ("#2f7f6d", "#e39a2c", "#d94848", "#4c78df"):
        assert monitoring_color in css
    assert "#586a64" in css
    assert "#73827d" not in css
    assert "#8c9995" not in css


def test_css_prevents_mobile_content_and_action_overflow() -> None:
    css = DASHBOARD_CSS.lower()

    assert "min-width: 0" in css
    assert "overflow-wrap: anywhere" in css
    assert "word-break: break-word" in css
    assert ".stbutton > button p" in css
    assert "white-space: normal" in css
    assert "min-height: 44px" in css


def test_primary_button_keeps_white_nested_text() -> None:
    css = DASHBOARD_CSS.lower()

    assert re.search(
        r'\.stbutton\s*>\s*button\[kind="primary"\]\s*p[^{}]*\{[^}]*color:\s*#ffffff',
        css,
    )


def test_small_badges_use_dark_aa_text_tokens() -> None:
    css = DASHBOARD_CSS.lower()

    assert "--danger-text: #8f2020" in css
    assert ".status-pill.profile-active" in css
    assert "color: var(--brand)" in css
    assert ".status-pill.device-offline" in css
    assert "color: var(--danger-text)" in css


def test_offline_device_status_uses_aa_safe_danger_text() -> None:
    css = DASHBOARD_CSS.lower()

    assert re.search(
        r"\.device-status\.offline\s*\{[^}]*color:\s*var\(--danger-text\)",
        css,
    )
    assert not re.search(
        r"\.device-status\.offline\s*\{[^}]*color:\s*var\(--danger\)",
        css,
    )


def test_risk_banner_detail_and_nested_text_wrap() -> None:
    css = DASHBOARD_CSS.lower()

    assert ".risk-banner-copy > * > *" in css
    assert re.search(
        r"[^{}]*\.risk-detail[^{}]*\{[^}]*overflow-wrap:\s*anywhere",
        css,
    )
    assert re.search(
        r"[^{}]*\.risk-detail[^{}]*\{[^}]*word-break:\s*break-word",
        css,
    )


def test_sidebar_uses_accessibility_hidden_css_marks_and_bottom_identity() -> None:
    source = Path("dashboard/app.py").read_text(encoding="utf-8")
    css = DASHBOARD_CSS.lower()

    assert 'aria-hidden="true"' in source
    for letter in (">O<", ">R<", ">A<", ">M<", ">D<"):
        assert letter not in source
    for icon in ("overview", "resident", "alerts", "medication", "devices"):
        assert f'"{icon}"' in source
        assert f".nav-icon.{icon}" in css
    assert '[data-testid="stsidebarusercontent"]' in css
    assert "min-height: calc(100vh" in css
    assert ".st-key-sidebar-caregiver" in css
    assert "margin-top: auto" in css
    assert "overflow-y: auto" in css


def test_sidebar_links_select_real_dashboard_views() -> None:
    source = Path("dashboard/app.py").read_text(encoding="utf-8")

    for view in ("overview", "resident", "alerts", "medication", "devices"):
        assert f'"{view}"' in source
    assert "?view=" in source
    assert 'aria-current="page"' in source


def test_sidebar_groups_monitoring_and_management_navigation() -> None:
    source = Path("dashboard/app.py").read_text(encoding="utf-8")

    assert '("Main",' in source
    assert '("Care",' in source
    assert '("Management",' in source
    assert '"residents"' in source
    assert '"monitoring"' in source
    assert '"family"' in source


def test_live_component_uses_real_public_websocket_and_reconnects_accessibly() -> None:
    html = build_live_panel_html("ws://localhost:8000/", "E 001", 60.0)
    lowered = html.lower()

    assert "ws://localhost:8000/ws/dashboard/E%20001" in html
    assert "defaultHeartbeatSeconds: 60.0" in html
    assert "new WebSocket(wsUrl)" in html
    assert "setTimeout(connect" in html
    assert "stale" in lowered
    assert "reconnect" in lowered
    assert "aria-live=\"polite\"" in html
    assert "tabindex=\"0\"" in html
    assert "keydown" in html
    assert "online" in html and "offline" in html
    assert html.count('class="live-card"') == 1
    assert "background: #1b5a4f" in lowered
    assert "#state.live" in lowered
    assert "#state.error" in lowered
    assert "#state.connecting" in lowered
    assert "<img" not in lowered
    assert "gradient" not in lowered
    assert "shadow" not in lowered


def test_live_component_sends_ticket_before_receiving_summary() -> None:
    html = build_live_panel_html("ws://localhost:8000", "E001", 15.0, "ticket-123")

    assert 'const authTicket = "ticket-123";' in html
    assert 'current.send(JSON.stringify({ type: "authenticate", ticket: authTicket }));' in html


def test_live_component_wraps_long_output_without_horizontal_scroll() -> None:
    html = build_live_panel_html(
        "ws://localhost:8000",
        "E" * 50,
        15.0,
    ).lower()

    assert ".live-card, .head, #state, #output, .hint" in html
    assert ".head > *" in html
    assert "overflow-wrap: anywhere" in html
    assert "word-break: break-word" in html
    assert "overflow-x: hidden" in html
    assert "max-width: 100%" in html


def test_live_component_is_clearly_labeled_and_stacks_narrow_controls() -> None:
    html = build_live_panel_html("ws://localhost:8000", "E001", 15.0).lower()

    assert "live connection" in html
    assert re.search(r"\.head\s*\{[^}]*flex-direction:\s*column", html)
    assert re.search(r"button\s*\{[^}]*width:\s*100%", html)


def test_live_component_does_not_interpolate_untrusted_id_into_markup() -> None:
    html = build_live_panel_html("ws://localhost:8000", '<script id="bad">', 15.0)

    assert '<script id="bad">' not in html
    assert "%3Cscript%20id%3D%22bad%22%3E" in html
    assert len(re.findall(r'class="live-card"', html)) == 1


def test_live_component_json_serialization_cannot_close_script_element() -> None:
    payload = "ws://localhost:8000/</script><script>alert(1)</script>"

    html = build_live_panel_html(payload, "E001", 15.0)

    assert payload not in html
    assert "\\u003c/script\\u003e\\u003cscript\\u003ealert(1)" in html
    assert html.count("</script>") == 1


def test_live_component_wires_generation_terminal_and_stale_lifecycle() -> None:
    html = build_live_panel_html("ws://localhost:8000", "E001", 15.0)

    assert "createLifecycle" in html
    assert "lifecycle.isCurrent" in html
    assert "event.code" in html
    assert "interval_seconds" in html
    assert 'connect("stale")' in html


def test_live_component_keeps_recoverable_error_until_summary_arrives() -> None:
    html = build_live_panel_html("ws://localhost:8000", "E001", 15.0)

    assert "lifecycle.error(token" in html
    assert "lifecycle.hasRecoverableError()" in html
    timer_start = html.index("window.setInterval(() => {")
    timer = html[timer_start:html.index("}, 1000);", timer_start)]
    assert timer.index("lifecycle.hasRecoverableError()") < timer.index(
        "lifecycle.isSummaryStale"
    )


def test_rest_content_uses_one_configurable_periodic_snapshot_fragment() -> None:
    source = Path("dashboard/app.py").read_text(encoding="utf-8")

    assert "DASHBOARD_REFRESH_SECONDS" in source
    assert source.count("@st.fragment(run_every=dashboard_refresh_seconds)") == 1
    assert source.count("api.get_summary(elderly_id)") == 1
    for history_fetch in ("api.get_health", "api.get_activity", "api.get_alerts"):
        assert history_fetch in source


def test_live_heartbeat_configuration_rejects_non_positive_non_finite_values() -> None:
    source = Path("dashboard/app.py").read_text(encoding="utf-8")

    assert "math.isfinite(websocket_heartbeat_interval)" in source
    assert "WEBSOCKET_HEARTBEAT_INTERVAL must be a positive finite number" in source


def test_chart_empty_state_has_no_terminal_marker() -> None:
    source = Path("dashboard/components/summary.py").read_text(encoding="utf-8")

    assert "[-] No processed health history" not in source


def test_live_component_scrolls_detailed_updates_internally() -> None:
    html = build_live_panel_html("ws://localhost:8000", "E001", 15.0)

    assert "overflow-y: auto" in html
    assert "max-height:" in html

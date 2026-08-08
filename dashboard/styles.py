DASHBOARD_CSS = """
<style>
:root {
  --page: #f4f7f8;
  --surface: #ffffff;
  --ink: #172822;
  --body: #435650;
  --muted: #586a64;
  --border: #dce5e2;
  --brand: #10493f;
  --brand-raised: #1b5a4f;
  --brand-soft: #def1eb;
  --success: #2f7f6d;
  --success-soft: #e2f3ed;
  --info: #4c78df;
  --info-soft: #eaf0fd;
  --warning: #e39a2c;
  --warning-soft: #fff5e3;
  --danger: #d94848;
  --danger-text: #8f2020;
  --danger-soft: #fff1ef;
  --surface-soft: #f8fbfa;
  --focus: #7eb7aa;
}

html, body, [class*="st-"], [data-testid="stAppViewContainer"] {
  color: var(--ink);
  font-family: "Inter", "Aptos", "Segoe UI", sans-serif;
}

[data-testid="stAppViewContainer"], [data-testid="stHeader"] {
  background: var(--page);
}

[data-testid="stHeader"] { height: 0; }
[data-testid="stDecoration"] { display: none; }

[data-testid="stMainBlockContainer"] {
  max-width: 1180px;
  padding: 28px 36px 80px;
}

[data-testid="stSidebar"] {
  background: var(--brand);
  min-width: 248px;
  width: 248px !important;
}

[data-testid="stSidebarContent"] {
  background: var(--brand);
  overflow-y: auto;
  padding: 28px 20px 24px;
}

[data-testid="stSidebarUserContent"] {
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 52px);
}

[data-testid="stSidebarUserContent"] > [data-testid="stVerticalBlock"] {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: calc(100vh - 52px);
}

[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
  color: #d8ebe6;
}

h1, h2, h3, p { color: var(--ink); }
h1 { font-size: 30px; line-height: 1.2; letter-spacing: -0.02em; margin-bottom: 2px; }
h2, h3 { font-size: 18px; line-height: 1.35; }

.page-subtitle, .meta {
  color: var(--muted);
  font-size: 13px;
  line-height: 1.5;
}

.page-subtitle { margin: -8px 0 0; }
.toolbar-context {
  align-items: center;
  color: var(--muted);
  display: flex;
  font-size: 12px;
  min-height: 44px;
  padding: 0 12px;
}
.section-heading { margin: 0 0 14px; }
.section-heading strong { display: block; font-size: 18px; }
.section-label {
  color: var(--ink);
  font-size: 18px;
  font-weight: 700;
  margin: 32px 0 14px;
}

.brand-lockup { align-items: center; display: flex; gap: 12px; margin-bottom: 34px; }
.brand-mark {
  align-items: center;
  background: var(--brand-soft);
  border-radius: 13px;
  color: var(--success);
  display: flex;
  font-size: 27px;
  font-weight: 800;
  height: 44px;
  justify-content: center;
  width: 44px;
}
.brand-copy strong { color: #ffffff; display: block; font-size: 15px; }
.brand-copy span { color: #afd2c9; font-size: 11px; }
.side-nav { display: grid; gap: 22px; }
.nav-group { display: grid; gap: 5px; }
.nav-group-label {
  color: #85b4a8;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: .12em;
  padding: 0 14px 4px;
  text-transform: uppercase;
}
.side-nav a {
  align-items: center;
  border-radius: 12px;
  color: #c5ddd7;
  display: flex;
  font-size: 14px;
  gap: 12px;
  min-height: 46px;
  padding: 0 14px;
  text-decoration: none;
}
.side-nav a:hover, .side-nav a:focus-visible { background: #1b5a4f; color: #ffffff; }
.side-nav a.active { background: var(--brand-soft); color: var(--brand); font-weight: 700; }
.nav-icon {
  flex: 0 0 20px;
  height: 20px;
  position: relative;
  width: 20px;
}
.nav-icon::before, .nav-icon::after { box-sizing: border-box; content: ""; position: absolute; }
.nav-icon.overview::before { border: 2px solid currentColor; border-radius: 50%; inset: 3px; }
.nav-icon.residents::before { border: 2px solid currentColor; border-radius: 50%; height: 7px; left: 7px; top: 1px; width: 7px; }
.nav-icon.residents::after { border: 2px solid currentColor; border-bottom: 0; border-radius: 8px 8px 0 0; bottom: 1px; height: 8px; left: 3px; width: 14px; }
.nav-icon.resident::before { border: 2px solid currentColor; border-radius: 50%; height: 7px; left: 7px; top: 1px; width: 7px; }
.nav-icon.resident::after { border: 2px solid currentColor; border-bottom: 0; border-radius: 8px 8px 0 0; bottom: 1px; height: 8px; left: 3px; width: 14px; }
.nav-icon.monitoring::before { border: 2px solid currentColor; border-radius: 3px; inset: 2px 1px 5px; }
.nav-icon.monitoring::after { background: currentColor; bottom: 1px; height: 2px; left: 5px; width: 10px; }
.nav-icon.health::before { border: 2px solid currentColor; border-radius: 50%; inset: 2px; }
.nav-icon.health::after { background: currentColor; height: 2px; left: 5px; top: 9px; transform: rotate(-45deg); width: 9px; }
.nav-icon.activity::before { border-bottom: 2px solid currentColor; border-left: 2px solid transparent; border-right: 2px solid transparent; height: 11px; left: 1px; top: 4px; transform: skewY(-25deg); width: 17px; }
.nav-icon.family::before { border: 2px solid currentColor; border-radius: 50%; height: 6px; left: 3px; top: 2px; width: 6px; }
.nav-icon.family::after { border: 2px solid currentColor; border-bottom: 0; border-radius: 8px 8px 0 0; bottom: 1px; height: 8px; left: 1px; width: 17px; }
.nav-icon.admin::before { border: 2px solid currentColor; border-radius: 3px; inset: 2px; }
.nav-icon.admin::after { background: currentColor; border-radius: 50%; height: 5px; left: 7px; top: 7px; width: 5px; }
.nav-icon.alerts::before { border-bottom: 15px solid currentColor; border-left: 8px solid transparent; border-right: 8px solid transparent; left: 2px; top: 2px; }
.nav-icon.medication::before { border: 2px solid currentColor; border-radius: 8px; height: 10px; left: 2px; top: 5px; transform: rotate(-35deg); width: 17px; }
.nav-icon.devices::before { border: 2px solid currentColor; border-radius: 3px; inset: 1px 4px 3px; }
.nav-icon.devices::after { background: currentColor; bottom: 0; height: 2px; left: 8px; width: 4px; }
.st-key-sidebar-caregiver { margin-top: auto; padding-top: 24px; }
.caregiver-card {
  align-items: center;
  background: #1b5a4f;
  border-radius: 14px;
  display: flex;
  gap: 12px;
  margin-top: 0;
  padding: 12px;
}
.caregiver-avatar {
  align-items: center;
  background: #f5c979;
  border-radius: 50%;
  color: var(--brand);
  display: flex;
  font-size: 12px;
  font-weight: 800;
  height: 38px;
  justify-content: center;
  width: 38px;
}
.caregiver-card strong { color: #ffffff; display: block; font-size: 13px; }
.caregiver-card span { color: #afd2c9; font-size: 11px; }

.resident-card, .metric-card, .content-card, .alert-row, .reminder-row,
.empty-state, .device-card, .activity-row {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
}

[data-testid="stColumn"], .resident-card, .resident-card > *, .vitals-grid,
.vitals-grid > *, .alert-row > *, .reminder-row > *, .telemetry-grid,
.telemetry-grid > *, .card-header > *, .risk-banner-copy > *,
.risk-banner-copy > * > * {
  min-width: 0;
}

.resident-name, .resident-meta, .contact-name, .contact-phone, .alert-message,
.alert-row, .reminder-row, .telemetry-row, .meta, .risk-title, .risk-detail,
.stButton > button, .stButton > button p {
  overflow-wrap: anywhere;
  word-break: break-word;
}

.resident-card {
  align-items: center;
  display: grid;
  gap: 18px;
  grid-template-columns: auto 1fr auto;
  margin: 24px 0;
  padding: 24px;
}
.resident-avatar {
  align-items: center;
  background: var(--brand-soft);
  border-radius: 50%;
  color: var(--success);
  display: flex;
  font-size: 22px;
  font-weight: 800;
  height: 68px;
  justify-content: center;
  width: 68px;
}
.resident-name { font-size: 22px; font-weight: 750; margin-bottom: 5px; }
.resident-meta { color: var(--muted); font-size: 13px; }
.resident-badges { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.status-pill {
  border-radius: 999px;
  display: inline-flex;
  font-size: 11px;
  font-weight: 700;
  padding: 5px 10px;
}
.status-pill.profile-active, .status-pill.device-online { background: var(--success-soft); color: var(--brand); }
.status-pill.risk-stable { background: var(--success-soft); color: var(--brand); }
.status-pill.risk-warning { background: var(--warning-soft); color: #8b5a14; }
.status-pill.risk-critical { background: var(--danger-soft); color: var(--danger-text); }
.status-pill.device-offline { background: var(--danger-soft); color: var(--danger-text); }
.status-pill.device-unavailable { background: #edf1f0; color: var(--muted); }
.contact-block { min-width: 180px; text-align: right; }
.contact-label { color: var(--muted); font-size: 10px; letter-spacing: .04em; text-transform: uppercase; }
.contact-name { font-size: 14px; font-weight: 700; margin-top: 5px; }
.contact-phone { color: var(--muted); font-size: 13px; }

.st-key-risk-banner-emergency, .st-key-risk-banner-warning {
  border: 1px solid #f1c1bb;
  border-radius: 16px;
  margin: 0 0 24px;
  padding: 18px !important;
}
.st-key-risk-banner-emergency { background: var(--danger-soft); }
.st-key-risk-banner-warning { background: var(--warning-soft); border-color: #f0d49f; }
.risk-banner-copy { align-items: center; display: flex; gap: 14px; }
.risk-symbol {
  align-items: center;
  background: var(--danger);
  border-radius: 12px;
  color: #ffffff;
  display: flex;
  font-size: 22px;
  font-weight: 800;
  height: 44px;
  justify-content: center;
  width: 44px;
}
.risk-banner-copy.warning .risk-symbol { background: var(--warning); }
.risk-title { color: #a72c2c; font-size: 15px; font-weight: 750; }
.risk-banner-copy.warning .risk-title { color: #8b5a14; }
.risk-detail { color: #7f4e48; font-size: 12px; margin-top: 2px; }

.vitals-grid { display: grid; gap: 14px; grid-template-columns: repeat(4, 1fr); }
.metric-card { min-height: 142px; padding: 18px; }
.metric-top { align-items: center; display: flex; justify-content: space-between; }
.metric-label { color: var(--muted); font-size: 10px; letter-spacing: .03em; text-transform: uppercase; }
.metric-dot { border-radius: 50%; height: 9px; width: 9px; }
.metric-dot.danger { background: var(--danger); }
.metric-dot.warning { background: var(--warning); }
.metric-dot.success { background: var(--success); }
.metric-dot.info { background: var(--info); }
.metric-value { font-size: 27px; font-weight: 760; margin-top: 22px; }
.metric-unit { color: var(--muted); font-size: 12px; font-weight: 600; margin-left: 3px; }
.metric-note { color: var(--muted); font-size: 11px; margin-top: 8px; }

.overview-stats {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 24px 0 30px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  min-height: 124px;
  padding: 16px;
}
.stat-card-top { align-items: center; color: var(--muted); display: flex; font-size: 11px; font-weight: 700; justify-content: space-between; }
.stat-card strong { display: block; font-size: 28px; letter-spacing: -.03em; margin-top: 16px; }
.stat-card small, .directory-card small, .attention-card small { color: var(--muted); display: block; font-size: 11px; line-height: 1.4; }
.stat-marker, .attention-marker { background: var(--success); border-radius: 50%; display: inline-block; height: 9px; width: 9px; }
.stat-critical .stat-marker, .attention-critical .attention-marker { background: var(--danger); }
.stat-warning .stat-marker, .attention-warning .attention-marker { background: var(--warning); }
.stat-offline .stat-marker, .attention-offline .attention-marker { background: var(--danger-text); }
.stat-info .stat-marker { background: var(--info); }

.attention-list, .directory-list { display: grid; gap: 10px; }
.attention-card, .directory-card {
  align-items: center;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
  color: var(--ink);
  display: grid;
  gap: 14px;
  min-width: 0;
  padding: 14px 16px;
  text-decoration: none;
}
.attention-card { grid-template-columns: auto 1fr auto; }
.attention-card:hover, .attention-card:focus-visible, .directory-card:hover, .directory-card:focus-visible { border-color: var(--success); background: var(--surface-soft); }
.attention-critical { border-left: 4px solid var(--danger); }
.attention-warning { border-left: 4px solid var(--warning); }
.attention-offline { border-left: 4px solid var(--danger-text); }
.attention-marker { align-items: center; color: #ffffff; display: flex; font-size: 12px; font-weight: 800; height: 30px; justify-content: center; width: 30px; }
.attention-copy strong, .directory-main strong { display: block; font-size: 14px; }
.attention-state { color: var(--danger-text); font-size: 12px; font-weight: 750; text-align: right; }
.attention-warning .attention-state { color: #8b5a14; }
.attention-state small { font-weight: 500; margin-top: 2px; }
.directory-card { grid-template-columns: auto minmax(140px, 1fr) minmax(140px, 1fr) 90px 90px; }
.directory-critical { border-left: 4px solid var(--danger); }
.directory-warning { border-left: 4px solid var(--warning); }
.directory-offline { border-left: 4px solid var(--danger-text); }
.directory-avatar { align-items: center; background: var(--brand-soft); border-radius: 50%; color: var(--brand); display: flex; font-size: 12px; font-weight: 800; height: 38px; justify-content: center; width: 38px; }
.directory-main small, .directory-reading small, .directory-device small { margin-top: 3px; }
.directory-status b, .directory-reading b, .directory-device b { display: block; font-size: 12px; }
.directory-critical .directory-status b { color: var(--danger-text); }
.directory-warning .directory-status b { color: #8b5a14; }
.directory-offline .directory-status b { color: var(--danger-text); }
.directory-device { border-left: 1px solid var(--border); padding-left: 12px; }

.stExpander {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 14px;
}
.stExpander summary { min-height: 44px; }

.overview-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1.85fr) minmax(280px, 1fr); margin-top: 24px; }
.content-card, .device-card { padding: 20px; }
.content-card { min-height: 430px; }
.context-stack { display: grid; gap: 16px; }
.device-card { min-height: 136px; }
.st-key-health-trends, .st-key-medication-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px;
}
.st-key-health-trends [data-testid="stVerticalBlockBorderWrapper"],
.st-key-medication-card [data-testid="stVerticalBlockBorderWrapper"] {
  border: 0;
}
.st-key-medication-card { margin-top: 16px; }
.card-header { align-items: start; display: flex; justify-content: space-between; margin-bottom: 14px; }
.card-title { font-size: 17px; font-weight: 750; }
.card-caption { color: var(--muted); font-size: 11px; margin-top: 2px; }
.device-status { color: var(--success); font-size: 12px; font-weight: 700; }
.device-status.offline { color: var(--danger-text); }
.device-status.unavailable { color: var(--muted); }

.alert-row, .reminder-row, .activity-row, .empty-state { margin-bottom: 10px; padding: 16px; }
.alert-row { align-items: start; display: grid; gap: 14px; grid-template-columns: 100px 1fr 145px; }
.alert-warning { border-left: 4px solid var(--warning); }
.alert-emergency { border-left: 4px solid var(--danger); }
.alert-severity { font-size: 12px; font-weight: 800; }
.alert-severity.emergency { color: var(--danger-text); }
.alert-severity.warning { color: #8b5a14; }
.alert-status { color: var(--ink); font-weight: 700; }
.alert-message { color: var(--body); }
.alert-time { text-align: right; }
.reminder-row { display: grid; gap: 6px; }
.reminder-pending { border-left: 4px solid var(--info); }
.reminder-missed { border-left: 4px solid var(--warning); }
.reminder-taken { border-left: 4px solid var(--success); }
.telemetry-grid { display: grid; gap: 14px; grid-template-columns: 1fr 1fr; }
.telemetry-row { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 16px; }
.status-active, .status-online { border-left: 4px solid var(--success); }
.status-inactive { border-left: 4px solid var(--warning); }
.status-offline { border-left: 4px solid var(--danger); }
.status-no-data { border-left: 4px solid var(--muted); }
.state-success { border-left: 4px solid var(--success); }

.stButton > button, .stTextInput input, .stTextArea textarea, .stDateInput input {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  color: var(--ink);
  min-height: 44px;
}
.stTextArea textarea { min-height: 100px; }
[data-baseweb="select"] > div { border-color: var(--border); border-radius: 12px; min-height: 44px; }
[data-testid="stCheckbox"] label { min-height: 44px; }
.stButton > button { height: auto; white-space: normal; }
.stButton > button p { white-space: normal; }
.stButton > button:hover { border-color: var(--success); color: var(--brand); }
.stButton > button[kind="primary"] { background: var(--brand); }
.stButton > button[kind="primary"],
.stButton > button[kind="primary"] p { color: #ffffff !important; }
.stButton > button[kind="primary"]:hover,
.stButton > button[kind="primary"]:hover p { color: #ffffff !important; }
[data-testid="stSidebar"] .stButton > button {
  background: var(--brand-raised);
  border-color: #6fa99a;
  color: #ffffff;
  justify-content: flex-start;
}
[data-testid="stSidebar"] .stButton > button p { color: #ffffff; }
[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: var(--brand-soft);
  border-color: var(--brand-soft);
  color: var(--brand);
  font-weight: 750;
}
[data-testid="stSidebar"] .stButton > button[kind="primary"] p { color: var(--brand) !important; }
button:focus-visible, input:focus-visible, a:focus-visible {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
}

[data-testid="stAltairChart"] { background: #f8faf9; border-radius: 12px; padding: 8px; }
[data-testid="stCaptionContainer"] { color: var(--muted); }

@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding-inline: 24px; }
  [data-testid="stHorizontalBlock"] { flex-direction: column; }
  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    flex: 1 1 100%;
    width: 100%;
  }
  .vitals-grid { grid-template-columns: 1fr 1fr; }
  .overview-stats { grid-template-columns: 1fr 1fr; }
  .overview-grid { grid-template-columns: 1fr; }
  .directory-card { grid-template-columns: auto 1fr 1fr; }
  .directory-reading, .directory-device { border-left: 0; grid-column: span 1; padding-left: 0; }
  .content-card { min-height: 360px; }
}

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding: 22px 16px 60px; }
  h1 { font-size: 26px; }
  .resident-card { align-items: start; grid-template-columns: auto 1fr; padding: 18px; }
  .contact-block { grid-column: 1 / -1; text-align: left; }
  .vitals-grid, .telemetry-grid { grid-template-columns: 1fr; }
  .overview-stats { grid-template-columns: 1fr; }
  .directory-card { grid-template-columns: auto 1fr; }
  .directory-status, .directory-reading, .directory-device { grid-column: 2; }
  .attention-card { grid-template-columns: auto 1fr; }
  .attention-state { grid-column: 2; text-align: left; }
  .metric-card { min-height: 124px; }
  .alert-row { grid-template-columns: 1fr; }
  .alert-time { text-align: left; }
}
</style>
"""

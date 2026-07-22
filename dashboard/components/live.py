import json
from html import escape
from pathlib import Path
from urllib.parse import quote, urlsplit


LIFECYCLE_JS = Path(__file__).with_name("live_state.js").read_text(encoding="utf-8")


def _html_safe_json(value: str) -> str:
    return (
        json.dumps(value)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_live_panel_html(
    public_ws_base_url: str,
    elderly_id: str,
    heartbeat_interval_seconds: float,
) -> str:
    parsed = urlsplit(public_ws_base_url)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError("PUBLIC_WS_BASE_URL must start with ws:// or wss://")
    ws_url = (
        f"{public_ws_base_url.rstrip('/')}/ws/dashboard/"
        f"{quote(elderly_id, safe='')}"
    )
    return (
        _HTML_TEMPLATE.replace("__LIFECYCLE_JS__", LIFECYCLE_JS)
        .replace("__WS_URL__", _html_safe_json(ws_url))
        .replace("__ELDERLY_ID__", escape(elderly_id, quote=True))
        .replace("__HEARTBEAT_INTERVAL__", json.dumps(heartbeat_interval_seconds))
    )


_HTML_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; }
body { margin: 0; background: #10493f; color: #ffffff; }
.live-card {
  background: #1b5a4f;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px;
  color: #ffffff;
  font: 12px/1.55 "Inter", "Aptos", "Segoe UI", sans-serif;
  max-height: 198px;
  min-height: 198px;
  overflow-x: hidden;
  overflow-y: auto;
  padding: 16px;
}
.live-card, .head, #state, #output, .hint {
  max-width: 100%;
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.head > * { min-width: 0; overflow-wrap: anywhere; word-break: break-word; }
.live-title { color: #ffffff; font-size: 13px; font-weight: 750; margin-bottom: 10px; }
.head { align-items: stretch; display: flex; flex-direction: column; gap: 9px; }
#state { color: #ffffff; border-left: 4px solid #e39a2c; font-weight: 700; padding-left: 8px; }
#state.live { border-left-color: #7ed7bf; }
#state.error { border-left-color: #ff8c87; }
#state.connecting { border-left-color: #9ab8ff; }
button {
  background: transparent;
  border: 1px solid #9dc9be;
  border-radius: 10px;
  color: #ffffff;
  cursor: pointer;
  font: inherit;
  min-height: 44px;
  padding: 6px 12px;
  width: 100%;
}
button:focus-visible, .live-card:focus-visible { outline: 3px solid #dff3ed; outline-offset: 2px; }
pre { color: #ffffff; font: inherit; margin: 12px 0 0; white-space: pre-wrap; }
.hint { color: #afd2c9; margin-top: 10px; }
@media (max-width: 640px) { .live-card { padding: 14px; } .head { align-items: start; } }
</style>
</head>
<body>
<section class="live-card" role="status" aria-live="polite" tabindex="0">
  <div class="live-title">Live connection</div>
  <div class="head">
    <span id="state" class="connecting">Connecting</span>
    <button id="reconnect" type="button" aria-label="Reconnect live monitoring">Reconnect</button>
  </div>
  <pre id="output">elderly  __ELDERLY_ID__
status   waiting for first summary</pre>
  <div class="hint">Updates arrive automatically. Press R to reconnect.</div>
</section>
<script>
__LIFECYCLE_JS__
const wsUrl = __WS_URL__;
const panel = document.querySelector(".live-card");
const state = document.getElementById("state");
const output = document.getElementById("output");
const reconnect = document.getElementById("reconnect");
const lifecycle = KindCareLiveState.createLifecycle({ defaultHeartbeatSeconds: __HEARTBEAT_INTERVAL__ });
let socket = null;
let reconnectTimer = null;
let shuttingDown = false;

function setState(label, className) {
  state.textContent = label;
  state.className = className;
}

function renderSummary(data) {
  output.textContent = KindCareLiveState.summaryLines(data).join("\n");
}

function scheduleReconnect(delay) {
  if (!navigator.onLine || shuttingDown || lifecycle.isTerminal()) return;
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, delay);
}

function connect(reason) {
  const token = lifecycle.beginConnect(Date.now());
  if (token === null) return;
  clearTimeout(reconnectTimer);
  const previous = socket;
  socket = null;
  if (previous && previous.readyState < 2) previous.close(1000, "replaced");
  setState("Connecting", "connecting");
  const current = new WebSocket(wsUrl);
  socket = current;

  current.onopen = () => {
    if (!lifecycle.opened(token, Date.now())) return;
    setState("Live", "live");
  };
  current.onmessage = (event) => {
    if (!lifecycle.isCurrent(token)) return;
    const message = JSON.parse(event.data);
    if (message.type === "heartbeat") {
      lifecycle.heartbeat(
        token,
        Date.now(),
        Number(message.data.interval_seconds),
        message.data.sent_at,
        message.data.last_summary_check_at,
        Number(message.data.poll_interval_seconds)
      );
    } else if (message.type === "summary") {
      lifecycle.summary(token, Date.now());
    } else if (message.type === "error") {
      lifecycle.error(token, Date.now());
    } else {
      lifecycle.message(token, Date.now());
    }
    if (message.type === "summary") renderSummary(message.data);
    if (message.type === "error") {
      output.textContent = `[x] ${message.data.message}`;
      setState("Connection error", "error");
    } else if (lifecycle.hasRecoverableError()) {
      setState("Connection error", "error");
    } else if (lifecycle.isSummaryStale(Date.now())) {
      setState("Live connection / data stale", "");
    } else {
      setState("Live", "live");
    }
  };
  current.onerror = () => {
    if (lifecycle.isCurrent(token)) setState("Connection error", "error");
  };
  current.onclose = (event) => {
    const decision = lifecycle.closed(token, event.code);
    if (decision.ignored) return;
    if (decision.terminal) {
      setState("Profile unavailable", "error");
      output.textContent = "This elderly profile is not active.";
      return;
    }
    setState("Reconnecting", "");
    if (decision.retry) scheduleReconnect(decision.delay);
  };
}

function reconnectNow() {
  connect("manual");
}

reconnect.addEventListener("click", reconnectNow);
panel.addEventListener("keydown", (event) => {
  if (event.key.toLowerCase() === "r") { event.preventDefault(); reconnectNow(); }
});
window.addEventListener("online", () => connect("online"));
window.addEventListener("offline", () => setState("Offline / data stale", ""));
window.setInterval(() => {
  if (lifecycle.isConnectionStale(Date.now())) {
    setState("Reconnecting", "");
    connect("stale");
  } else if (lifecycle.hasRecoverableError()) {
    setState("Connection error", "error");
  } else if (lifecycle.isSummaryStale(Date.now())) {
    setState("Live connection / data stale", "");
  }
}, 1000);
window.addEventListener("beforeunload", () => {
  shuttingDown = true;
  clearTimeout(reconnectTimer);
  if (socket) socket.close(1000, "page unload");
});
connect("initial");
</script>
</body>
</html>
"""

const test = require("node:test");
const assert = require("node:assert/strict");

const { createLifecycle, summaryLines } = require("../components/live_state.js");

test("old socket close cannot schedule a retry for a newer generation", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const oldToken = lifecycle.beginConnect(0);
  const currentToken = lifecycle.beginConnect(1);

  const decision = lifecycle.closed(oldToken, 1006);

  assert.equal(decision.ignored, true);
  assert.equal(decision.retry, false);
  assert.equal(lifecycle.isCurrent(currentToken), true);
});

test("4404 is terminal and disables automatic or manual reconnect", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const token = lifecycle.beginConnect(0);

  const decision = lifecycle.closed(token, 4404);

  assert.deepEqual(decision, { ignored: false, retry: false, terminal: true });
  assert.equal(lifecycle.beginConnect(1), null);
});

test("heartbeat interval determines connection stale threshold", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 15 });
  const token = lifecycle.beginConnect(0);
  lifecycle.opened(token, 1000);
  lifecycle.heartbeat(token, 2000, 4);

  assert.equal(lifecycle.isConnectionStale(11999), false);
  assert.equal(lifecycle.isConnectionStale(12001), true);
});

test("60 second configured interval detects a missing heartbeat after 150 seconds", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 60 });
  const token = lifecycle.beginConnect(0);
  lifecycle.opened(token, 0);

  assert.equal(lifecycle.isConnectionStale(150000), false);
  assert.equal(lifecycle.isConnectionStale(150001), true);
});

test("transport heartbeats do not make stalled summary polling fresh", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const token = lifecycle.beginConnect(0);
  lifecycle.opened(token, 0);
  lifecycle.summary(token, 0);
  lifecycle.heartbeat(
    token,
    10000,
    10,
    "2026-07-20T10:00:10Z",
    "2026-07-20T10:00:00Z",
    1
  );

  assert.equal(lifecycle.isConnectionStale(10000), false);
  assert.equal(lifecycle.isSummaryStale(10000), true);
});

test("successful unchanged poll metadata clears summary stale state", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const token = lifecycle.beginConnect(0);
  lifecycle.opened(token, 0);
  lifecycle.heartbeat(
    token,
    10000,
    10,
    "2026-07-20T10:00:10Z",
    "2026-07-20T10:00:10Z",
    1
  );

  assert.equal(lifecycle.isSummaryStale(10000), false);
});

test("recoverable error remains through heartbeats until a summary restores output", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const token = lifecycle.beginConnect(0);
  lifecycle.opened(token, 0);

  lifecycle.error(token, 1);
  lifecycle.heartbeat(token, 2, 10);

  assert.equal(lifecycle.hasRecoverableError(), true);
  lifecycle.summary(token, 3);
  assert.equal(lifecycle.hasRecoverableError(), false);
});

test("current non-terminal close returns bounded retry decision", () => {
  const lifecycle = createLifecycle({ defaultHeartbeatSeconds: 10 });
  const token = lifecycle.beginConnect(0);

  const decision = lifecycle.closed(token, 1006);

  assert.equal(decision.ignored, false);
  assert.equal(decision.retry, true);
  assert.equal(decision.delay, 1000);
});

test("summary renderer includes activity and device changes", () => {
  const lines = summaryLines({
    profile: { full_name: "Margaret Lee", elderly_id: "E001" },
    current_risk: "warning",
    latest_health: null,
    latest_activity: { value: "inactive", received_at: "2026-07-17T08:00:00Z" },
    device_status: { status: "offline", last_seen: "2026-07-17T08:01:00Z" },
    recent_alerts: []
  });

  assert.equal(lines.includes("activity inactive / 2026-07-17T08:00:00Z"), true);
  assert.equal(lines.includes("device   offline / 2026-07-17T08:01:00Z"), true);
});

test("summary renderer includes current reminder statuses", () => {
  const lines = summaryLines({
    profile: { full_name: "Margaret Lee", elderly_id: "E001" },
    current_risk: "warning",
    latest_health: null,
    latest_activity: null,
    device_status: null,
    recent_alerts: [],
    upcoming_reminders: [
      { medicine_name: "Aspirin", status: "pending", scheduled_for: "2026-07-18T08:00:00Z" }
    ],
    recent_reminders: [
      { medicine_name: "Vitamin D", status: "taken", scheduled_for: "2026-07-18T07:00:00Z" }
    ]
  });

  assert.equal(lines.includes("reminder pending / Aspirin / 2026-07-18T08:00:00Z"), true);
  assert.equal(lines.includes("reminder taken / Vitamin D / 2026-07-18T07:00:00Z"), true);
});

test("summary renderer includes bounded alert identity and status details", () => {
  const lines = summaryLines({
    profile: { full_name: "Margaret Lee", elderly_id: "E001" },
    current_risk: "emergency",
    latest_health: null,
    latest_activity: null,
    device_status: null,
    recent_alerts: [
      { alert_type: "device_offline", severity: "emergency", status: "acknowledged" },
      { alert_type: "long_inactivity", severity: "warning", status: "unresolved" }
    ]
  });

  assert.equal(lines.includes("alert    emergency / device offline / acknowledged"), true);
  assert.equal(lines.includes("alert    warning / long inactivity / unresolved"), true);
});

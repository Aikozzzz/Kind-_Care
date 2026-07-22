(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KindCareLiveState = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function createLifecycle(options) {
    let generation = 0;
    let terminal = false;
    let attempts = 0;
    let phase = "idle";
    let lastSeen = null;
    let lastSummaryCheck = null;
    let recoverableError = false;
    let heartbeatMs = options.defaultHeartbeatSeconds * 1000;
    let summaryFreshnessMs = heartbeatMs * 2.5;

    return {
      beginConnect(now) {
        if (terminal) return null;
        generation += 1;
        phase = "connecting";
        lastSeen = null;
        lastSummaryCheck = null;
        recoverableError = false;
        return generation;
      },
      isCurrent(token) {
        return token === generation;
      },
      opened(token, now) {
        if (token !== generation) return false;
        attempts = 0;
        phase = "open";
        lastSeen = now;
        return true;
      },
      message(token, now) {
        if (token !== generation) return false;
        lastSeen = now;
        return true;
      },
      summary(token, now) {
        if (token !== generation) return false;
        lastSeen = now;
        lastSummaryCheck = now;
        recoverableError = false;
        return true;
      },
      error(token, now) {
        if (token !== generation) return false;
        lastSeen = now;
        recoverableError = true;
        return true;
      },
      heartbeat(token, now, intervalSeconds, sentAt, lastSummaryAt, pollIntervalSeconds) {
        if (token !== generation) return false;
        if (Number.isFinite(intervalSeconds) && intervalSeconds > 0) {
          heartbeatMs = intervalSeconds * 1000;
        }
        const sentMs = Date.parse(sentAt);
        const summaryMs = Date.parse(lastSummaryAt);
        if (Number.isFinite(sentMs) && Number.isFinite(summaryMs)) {
          lastSummaryCheck = now - Math.max(0, sentMs - summaryMs);
        }
        if (Number.isFinite(pollIntervalSeconds) && pollIntervalSeconds > 0) {
          summaryFreshnessMs = pollIntervalSeconds * 1000 * 2.5;
        }
        lastSeen = now;
        return true;
      },
      closed(token, code) {
        if (token !== generation) {
          return { ignored: true, retry: false, terminal: false };
        }
        phase = "closed";
        if (code === 4404) {
          terminal = true;
          generation += 1;
          return { ignored: false, retry: false, terminal: true };
        }
        const delay = Math.min(30000, 1000 * (2 ** attempts));
        attempts += 1;
        return { ignored: false, retry: true, terminal: false, delay };
      },
      isConnectionStale(now) {
        return phase === "open" && lastSeen !== null && now - lastSeen > heartbeatMs * 2.5;
      },
      isSummaryStale(now) {
        return phase === "open" && lastSummaryCheck !== null && now - lastSummaryCheck > summaryFreshnessMs;
      },
      hasRecoverableError() {
        return recoverableError;
      },
      isTerminal() {
        return terminal;
      }
    };
  }

  function summaryLines(data) {
    const health = data.latest_health || {};
    const activity = data.latest_activity || {};
    const device = data.device_status || {};
    const reminderLines = [
      ...(data.upcoming_reminders || []),
      ...(data.recent_reminders || [])
    ].slice(0, 5).map((reminder) =>
      `reminder ${reminder.status} / ${reminder.medicine_name} / ${reminder.scheduled_for}`
    );
    const alertLines = (data.recent_alerts || []).slice(0, 5).map((alert) =>
      `alert    ${alert.severity} / ${alert.alert_type.replaceAll("_", " ")} / ${alert.status}`
    );
    return [
      `elderly  ${data.profile.full_name} (${data.profile.elderly_id})`,
      `risk     ${data.current_risk}`,
      `pulse    ${health.heart_rate ?? "no reading"} bpm`,
      `oxygen   ${health.oxygen_level ?? "no reading"}%`,
      `temp     ${health.temperature ?? "no reading"} C`,
      `activity ${activity.value ?? "no data"} / ${activity.received_at ?? "not available"}`,
      `device   ${device.status ?? "no data"} / ${device.last_seen ?? "not available"}`,
      `alerts   ${(data.recent_alerts || []).length} recent`,
      ...alertLines,
      ...reminderLines
    ];
  }

  return { createLifecycle, summaryLines };
});

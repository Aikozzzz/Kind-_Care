# KindCare Dashboard Design

## Canonical Sources

The caregiver dashboard follows the exported Figma references in:

- `figma design/Sidebar.png`
- `figma design/Main Content.png`

These exports define the dashboard's information hierarchy, color direction, card
geometry, sidebar, and responsive intent. The user-supplied root `DESIGN.md` remains an
upstream historical reference, but this document and the Figma exports govern the
KindCare application when they differ.

The implementation adapts the static composition to Streamlit without presenting
controls that do not work. Existing REST actions, automatic refresh, WebSocket
freshness, missing-data states, and local-demo warnings remain functional.

## Product Intent

The interface is a calm caregiver workspace optimized for rapid scanning:

1. Identify the resident and emergency contact.
2. See urgent risk before routine measurements.
3. Read four current signals without opening another view.
4. Compare recent health trends.
5. Check device and medication state.
6. Act on reminders and alerts.
7. Inspect activity and alert history below the primary overview.

Color never carries state alone. Text, status labels, timestamps, and borders remain
present for caregivers using assistive technology or low-quality displays.

## Layout

### Desktop

The desktop composition uses a 248px fixed deep-green sidebar and a flexible light
workspace. Main content is limited to 1180px with 36px horizontal padding.

The main reading order is:

1. `Care overview` header, current date, resident name/ID search, and refresh action.
2. Resident card with initials, ID, age, profile/device badges, and emergency contact.
3. Warning or emergency action banner when current risk is not normal.
4. Four vital cards: heart rate, oxygen, temperature, and activity.
5. Health-trends area with device and medication context beside it.
6. Activity history and recent alerts.
7. Local academic-demo warning.

The sidebar contains the KindCare caregiver-console lockup, dashboard-view links, the
browser-owned live WebSocket monitor, and caregiver identity. Each link sets a stable
`?view=` URL with the resolved resident ID, updates the page heading and main content,
and receives both the active visual treatment and `aria-current=page`. This preserves
resident context when link navigation starts a new Streamlit WebSocket session.
Navigation uses simple CSS-drawn marks hidden
from assistive technology while link text supplies each accessible name. A flex/minimum
height layout pushes the sidebar identity toward the bottom on taller screens; the
sidebar continues to scroll when viewport height is constrained.

### Navigation Views

- `Overview` retains the combined resident, risk, vitals, trends, device, medication,
  activity, and alert composition.
- `Resident` focuses on identity, current vitals, health trends, and activity history.
- `Alerts` focuses on the current risk banner and recent alert actions.
- `Medication` shows the complete bounded reminder set and mark-taken actions.
- `Devices` shows current device state, current monitoring signals, and activity history.

Resident identity and non-normal risk remain visible in every view so navigation cannot
hide who is being monitored or an urgent care condition. Unknown `view` values safely
fall back to Overview.

### Tablet And Mobile

- At 900px, real Streamlit horizontal blocks stack and every child column becomes full
  width; vital cards become two columns.
- At 640px, resident contact details move below identity and all data grids become one
  column.
- Streamlit controls the sidebar drawer on narrow screens, preserving main-content
  width and keyboard access.
- Long names, IDs, messages, medicine instructions, and timestamps wrap without
  horizontal scrolling. Shrinkable grid/card children use `min-width: 0`; text and
  button labels use `overflow-wrap: anywhere` with word breaking where needed. The
  live iframe applies the same safeguards to its card, state, hint, and preformatted
  output while suppressing horizontal scrolling.

## Visual Tokens

### Color

| Role | Value | Usage |
| --- | --- | --- |
| Workspace | `#f4f7f8` | Main application background |
| Surface | `#ffffff` | Resident, vital, trend, device, medication, history cards |
| Primary ink | `#172822` | Headings and high-emphasis content |
| Body | `#435650` | Supporting text |
| Muted | `#586a64` | Labels, timestamps, metadata; at least 4.5:1 on white |
| Border | `#dce5e2` | Card and input boundaries |
| Brand | `#10493f` | Sidebar and primary action language |
| Brand raised | `#1b5a4f` | Sidebar live/caregiver surfaces |
| Brand soft | `#def1eb` | Active navigation, avatar, positive badges |
| Success | `#2f7f6d` | Online, active, live, normal status |
| Information | `#4c78df` | Device/activity context and oxygen trend |
| Warning | `#e39a2c` | Warning state and caution measurements |
| Danger | `#d94848` | Emergency and destructive/error states |
| Danger text | `#8f2020` | Small danger labels on soft danger backgrounds |

Soft semantic backgrounds are used for banners and badges. At 11px, positive badge
text uses brand `#10493f`, danger badge text uses `#8f2020`, and unavailable badge text
uses muted `#586a64`, keeping semantic labels at WCAG AA contrast. The 12px offline
device-status text also uses `#8f2020`. No pale semantic color is used as body text.

### Typography

The Figma direction is a contemporary product interface rather than the prior terminal
style. KindCare uses the system-safe stack:

```css
"Inter", "Aptos", "Segoe UI", sans-serif
```

Inter is not bundled or downloaded. Aptos or Segoe UI is used when Inter is not
installed. Headings use 700-760 weight; metadata is 10-13px; current measurements use
27px values with separate units.

### Shape And Spacing

- Primary cards use 16px corners and 1px borders.
- Controls and sidebar rows use 10-12px corners.
- Badges are fully rounded.
- Interactive targets remain at least 44px high.
- Component spacing follows 8/10/14/16/20/24/32px steps.
- There are no gradients, decorative images, or drop shadows.

## Components

### Resident Card

Initials scan tokens until the first two containing alphanumeric characters, skipping
punctuation-only prefixes, and all profile values are HTML escaped. Age is calculated
from `date_of_birth`; invalid legacy dates render `Age unavailable`. The profile badge
says `Profile active`. Device state is separately and semantically labeled
`Device online`, `Device offline`, or unavailable; unknown values cannot enter CSS
classes.

### Resident Search

The dashboard requests at most 100 active profiles. Exact ID wins, followed by exact
name and case-insensitive name substring matches. One match opens directly and multiple
matches expose a resident selectbox. A syntactically valid direct ID remains usable if
it is outside the bounded profile list or profile listing is unavailable; name-search
not-found and unavailable states are explicit.

### Risk Banner

Normal risk does not consume vertical space. Warning and emergency states render a
full-width semantic banner using the highest relevant unresolved/acknowledged alert.
The typed summary carries that complete `current_alert` even when it is older than the
bounded recent-alert list. Selection checks emergency before warning, unresolved before acknowledged,
then newest `(created_at,event_id,alert_type)` order. `current_alert` is
exposed only when its severity equals final `current_risk`; a higher latest-health risk
therefore gets generic banner text and no unrelated alert action. Otherwise its escaped
message and stable ID drive the owner-safe `Acknowledge alert` action. Risk tone classes
are selected from a fixed normal/warning/emergency allowlist. The normal alert history
retains acknowledge and resolve controls and backend source-state guards.

### Vital Cards

Heart rate, oxygen, temperature, and activity form a four-card grid. Each card includes
a label, value, separate unit, status dot, and plain-language range note. Missing data
is `No data`, never zero.

### Health Trends

The primary chart overlays caregiver-named `Heart rate` and `Oxygen level` series using
restrained green and blue. It plots only the latest 12 hours relative to the newest
record. Tooltips include explicit caregiver labels and actual timestamps. Temperature
remains available as a current metric and in the chart-building API for future views.

### Device And Medication

Device status shows the latest server liveness state and timestamp. Medication shows a
bounded set of upcoming/recent reminders and keeps real `Mark taken` actions. Empty
states are explicit.

Activity, reminder, and alert rows use sentence-case caregiver language and formatted
UTC times rather than bracket markers, uppercase technical labels, or raw ISO strings.
Visible action labels omit UUIDs and identify their target by medicine/alert type plus
compact action times such as `Jul 21, 8:00 AM`; stable IDs remain only in Streamlit
widget keys. Detailed rows retain year and UTC context. The chart empty state uses plain
caregiver copy without a terminal marker.

### REST Refresh Snapshot

One periodic Streamlit fragment performs one summary fetch. Overview also fetches
health, activity, and alert histories in that same refresh; focused views request only
the histories they render. The REST surface therefore renders from one refresh cycle
without making medication or other focused views depend on unrelated history endpoints.
The browser-owned WebSocket lifecycle and security policy are unchanged.

### Live WebSocket Monitor

The real browser WebSocket monitor is embedded in the sidebar using the raised brand
surface. The card is explicitly titled `Live connection`; its status and full-width
reconnect control stack vertically so they remain readable within the narrow sidebar.
Detailed updates scroll inside the bounded card. It preserves:

- immediate summary rendering;
- independent transport and MongoDB-summary freshness;
- reconnect button and `R` keyboard shortcut;
- generation-safe reconnect behavior;
- terminal `4404` handling;
- accessible `role=status` and `aria-live=polite` output;
- escaped profile IDs and HTML-safe WebSocket URLs.

The monitor uses the same green/sidebar visual language rather than appearing as an
unrelated black terminal.

## Accessibility

- All controls are keyboard reachable and at least 44px high.
- Focus uses a visible 3px outline.
- Status always includes words, not only dots or color.
- Live updates use polite announcements and expose stale/error labels.
- Emergency actions are explicit and not triggered by color or icon alone.
- Grid collapse preserves document reading order.
- Auto-refresh does not move focus.
- HTML generated from backend values is escaped.

## Implementation References

- `dashboard/app.py`: page composition, sidebar, fragments, actions, risk banner.
- `dashboard/styles.py`: Figma-derived tokens, desktop/sidebar layout, breakpoints.
- `dashboard/components/summary.py`: resident, vital, device, chart, reminder, history,
  and alert components.
- `dashboard/components/live.py`: sidebar WebSocket surface and safe browser embedding.
- `dashboard/components/live_state.js`: connection generations, freshness, retry policy.
- `dashboard/tests/test_design.py`: visual contract.
- `dashboard/tests/test_summary.py`: escaped component markup and empty states.
- `dashboard/tests/test_app.py`: Streamlit page and caregiver action behavior.

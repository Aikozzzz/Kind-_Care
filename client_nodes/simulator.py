import argparse
import os
import time
from datetime import UTC, datetime, timedelta
from collections.abc import Iterator, Sequence

from client_nodes.elderly_node import ElderlyNodeClient


def _normal(elderly_id: str, index: int) -> dict[str, object]:
    return {
        "elderly_id": elderly_id,
        "heart_rate": 78 + index % 7,
        "temperature": round(36.5 + (index % 3) * 0.1, 1),
        "oxygen_level": 97 - index % 2,
        "blood_pressure": f"{118 + index % 5}/{76 + index % 4}",
        "movement_status": "active",
        "medicine_status": "taken",
        "emergency_pressed": False,
    }


def _warning(elderly_id: str, index: int) -> dict[str, object]:
    return {
        **_normal(elderly_id, index),
        "temperature": round(38.2 + (index % 3) * 0.2, 1),
        "medicine_status": "missed" if index % 2 else "taken",
    }


def _emergency(elderly_id: str, index: int) -> dict[str, object]:
    return {
        **_normal(elderly_id, index),
        "heart_rate": 126 + index % 8,
        "oxygen_level": 89 + index % 3,
        "emergency_pressed": True,
    }


def _inactivity(elderly_id: str, index: int) -> dict[str, object]:
    return {**_normal(elderly_id, index), "movement_status": "inactive"}


SCENARIOS = {
    "normal": _normal,
    "warning": _warning,
    "emergency": _emergency,
    "inactivity": _inactivity,
    "offline": _normal,
}


def build_scenario(name: str, elderly_id: str, count: int) -> Iterator[dict[str, object]]:
    sequence = ("normal", "warning", "emergency") if name == "mixed" else (name,)
    for index in range(count):
        yield SCENARIOS[sequence[index % len(sequence)]](elderly_id, index)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send simulated KindCare health events")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--elderly-id", default="E001")
    parser.add_argument("--scenario", choices=[*SCENARIOS, "mixed"], default="normal")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--backoff", type=float, default=0.5)
    parser.add_argument(
        "--auth-token", default=os.environ.get("TELEMETRY_SERVICE_TOKEN", "kindcare_telemetry_dev_only")
    )
    parser.add_argument("--reminder-demo", choices=["taken", "missed"])
    parser.add_argument("--reminder-medicine", default="KindCare demo medicine")
    parser.add_argument("--reminder-delay", type=float, default=2.0)
    parser.add_argument("--reminder-grace-seconds", type=float, default=300.0)
    parser.add_argument("--reminder-poll-interval", type=float, default=1.0)
    parser.add_argument("--reminder-timeout", type=float, default=60.0)
    return parser


def run_reminder_demo(args: argparse.Namespace, client: ElderlyNodeClient) -> int:
    now = datetime.now(UTC)
    if args.reminder_demo == "missed":
        scheduled_for = now - timedelta(
            seconds=max(args.reminder_grace_seconds, 0) + 1
        )
    else:
        scheduled_for = now + timedelta(seconds=args.reminder_delay)
    try:
        created = client.create_reminder(
            {
                "elderly_id": args.elderly_id,
                "medicine_name": args.reminder_medicine,
                "scheduled_for": scheduled_for.isoformat().replace("+00:00", "Z"),
                "instructions": "One-time simulator demonstration",
            }
        )
        if args.reminder_demo == "taken":
            result = client.mark_reminder_taken(created.reminder_id, args.elderly_id)
            print(
                f"[+] reminder taken elderly={args.elderly_id} "
                f"reminder={result.reminder_id}",
                flush=True,
            )
            return 0

        deadline = time.monotonic() + max(args.reminder_timeout, 0)
        while True:
            reminders = client.list_reminders(args.elderly_id, limit=50)
            observed = next(
                (
                    reminder
                    for reminder in reminders
                    if reminder.reminder_id == created.reminder_id
                ),
                None,
            )
            if observed is not None and observed.status == "missed":
                print(
                    f"[+] reminder missed elderly={args.elderly_id} "
                    f"reminder={observed.reminder_id}",
                    flush=True,
                )
                return 0
            if time.monotonic() >= deadline:
                print(
                    f"[x] reminder missed timeout elderly={args.elderly_id} "
                    f"reminder={created.reminder_id}",
                    flush=True,
                )
                return 1
            time.sleep(max(args.reminder_poll_interval, 0))
    except Exception as error:
        print(
            f"[x] reminder {args.reminder_demo} failed elderly={args.elderly_id}: {error}",
            flush=True,
        )
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = ElderlyNodeClient(
        args.url,
        timeout=args.timeout,
        max_retries=args.retries,
        backoff=args.backoff,
        auth_token=args.auth_token,
    )
    if args.reminder_demo is not None:
        return run_reminder_demo(args, client)
    failed = False
    for number, payload in enumerate(
        build_scenario(args.scenario, args.elderly_id, args.count), start=1
    ):
        recorded_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        health_payload = {**payload, "recorded_at": recorded_at}
        submissions = [
            ("health", client.send_health, health_payload),
            (
                "activity",
                client.send_activity,
                {
                    "elderly_id": args.elderly_id,
                    "value": payload["movement_status"],
                    "recorded_at": recorded_at,
                },
            ),
        ]
        if args.scenario != "offline" or number == 1:
            submissions.append((
                "heartbeat",
                client.send_heartbeat,
                {"elderly_id": args.elderly_id, "recorded_at": recorded_at},
            ))
        for label, sender, event_payload in submissions:
            try:
                result = sender(event_payload)
                print(
                    f"[+] {label} {number}/{args.count} elderly={args.elderly_id} "
                    f"event={result.event_id} key={result.idempotency_key}",
                    flush=True,
                )
            except Exception as error:
                failed = True
                print(
                    f"[x] {label} failed {number}/{args.count} elderly={args.elderly_id}: {error}",
                    flush=True,
                )
        if number < args.count and args.interval > 0:
            time.sleep(args.interval)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

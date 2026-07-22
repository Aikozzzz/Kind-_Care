import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REMINDER_PATCH_ENDPOINT = "PATCH /api/reminders/{reminder_id}"
ELDERLY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,50}")
COMPOSE_SUBSTITUTION = re.compile(r"\$\{([A-Z][A-Z0-9_]*):-([^}]*)\}")
ENV_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$", re.MULTILINE)
RABBITMQ_CREDENTIAL_PATTERN_TEXT = r"[A-Za-z0-9._~-]+"
RABBITMQ_CREDENTIAL_PATTERN = re.compile(
    rf"^{RABBITMQ_CREDENTIAL_PATTERN_TEXT}$"
)
ROUTE_REFERENCE = re.compile(
    r"`?(GET|POST|PATCH|DELETE|WS)\s+(/[A-Za-z0-9_{}./-]+)`?"
)
FENCED_CODE = re.compile(
    r"^[ \t]*(?P<marker>`{3,}|~{3,})[^\n]*\n(?P<body>.*?)"
    r"^[ \t]*(?P=marker)[ \t]*(?:\n|$)",
    re.MULTILINE | re.DOTALL,
)
INLINE_CODE = re.compile(r"(?P<marker>`+)(?P<body>[^\n]*?)(?P=marker)")
REQUEST_BODY_LINK = re.compile(
    r"\b(?:with|body|request|requires?|accepts(?:\s+only)?|example)\b",
    re.IGNORECASE,
)


def _markdown_code_samples(markdown: str) -> list[tuple[int, int, str]]:
    fenced = [
        (match.start(), match.end(), match.group("body"))
        for match in FENCED_CODE.finditer(markdown)
    ]
    samples = list(fenced)

    for match in INLINE_CODE.finditer(markdown):
        if any(start <= match.start() < end for start, end, _ in fenced):
            continue
        samples.append((match.start(), match.end(), match.group("body")))

    return sorted(samples)


def _first_json_object(code: str) -> dict[str, object] | None:
    decoder = json.JSONDecoder()
    start = code.find("{")

    while start >= 0:
        try:
            value, _ = decoder.raw_decode(code[start:])
        except json.JSONDecodeError:
            start = code.find("{", start + 1)
            continue
        if isinstance(value, dict):
            return value
        start = code.find("{", start + 1)

    return None


def _reminder_patch_request_examples(markdown: str) -> list[dict[str, object]]:
    samples = _markdown_code_samples(markdown)
    examples: list[dict[str, object]] = []

    for index, (_, end, code) in enumerate(samples):
        endpoint_at = code.find(REMINDER_PATCH_ENDPOINT)
        if endpoint_at < 0:
            continue

        body = _first_json_object(code[endpoint_at + len(REMINDER_PATCH_ENDPOINT) :])
        if body is not None:
            examples.append(body)
            continue

        if index + 1 >= len(samples):
            continue
        next_start, _, next_code = samples[index + 1]
        gap = markdown[end:next_start]
        if (
            len(gap) <= 300
            and not re.search(r"^#{1,6}\s", gap, re.MULTILINE)
            and REQUEST_BODY_LINK.search(gap)
        ):
            body = _first_json_object(next_code)
            if body is not None:
                examples.append(body)

    return examples


def _assert_owner_bound_reminder_patch(
    body: dict[str, object], location: object
) -> None:
    elderly_id = body.get("elderly_id")
    assert (
        isinstance(elderly_id, str)
        and elderly_id == elderly_id.strip()
        and ELDERLY_ID_PATTERN.fullmatch(elderly_id) is not None
    ), f"{location}: invalid elderly_id in {body!r}"
    assert body.get("status") == "taken", f"{location}: {body!r}"


def _compose_substitutions() -> dict[str, str]:
    compose = (ROOT / "docker-compose.yml").read_text()
    return dict(COMPOSE_SUBSTITUTION.findall(compose))


def _env_assignments(path: Path) -> dict[str, str]:
    return dict(ENV_ASSIGNMENT.findall(path.read_text()))


def _documented_routes(markdown: str) -> set[tuple[str, str]]:
    return set(ROUTE_REFERENCE.findall(markdown))


def _json_objects(markdown: str) -> list[dict[str, object]]:
    objects = []
    for _, _, sample in _markdown_code_samples(markdown):
        value = _first_json_object(sample)
        if value is not None:
            objects.append(value)
    return objects


def _markdown_section(markdown: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    assert match is not None, heading
    return match.group("body")


def test_canonical_release_documents_exist() -> None:
    for relative_path in (
        "AGENTS.md",
        ".env.example",
        ".dockerignore",
        "docs/database-design.md",
        "docs/dashboard-design.md",
        "docs/api-documentation.md",
        "docs/architecture.md",
    ):
        assert (ROOT / relative_path).is_file(), relative_path


def test_root_dockerignore_excludes_local_state_but_keeps_release_sources() -> None:
    patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    for required in (
        ".env",
        ".git",
        ".superpowers",
        "opencode.json",
        "__pycache__",
        ".pytest_cache",
        ".cache",
        ".coverage",
        "*.log",
        ".venv",
        "*.dump",
    ):
        assert required in patterns
    for release_path in ("README.md", "AGENTS.md", "docs", "backend", "workers"):
        assert release_path not in patterns


def test_superpowers_is_ignored_as_local_agent_state() -> None:
    git_patterns = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    docker_patterns = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".superpowers/" in git_patterns
    assert ".superpowers" in docker_patterns


def test_root_env_example_covers_every_compose_override_with_defaults() -> None:
    substitutions = _compose_substitutions()
    assignments = _env_assignments(ROOT / ".env.example")

    assert assignments == substitutions
    assert assignments["RABBITMQ_DEFAULT_USER"] != "guest"
    assert assignments["RABBITMQ_DEFAULT_PASS"]
    assert assignments["MQTT_USERNAME"]
    assert assignments["MQTT_PASSWORD"]


def test_rabbitmq_compose_credentials_are_documented_as_uri_unreserved() -> None:
    assignments = _env_assignments(ROOT / ".env.example")
    env_example = (ROOT / ".env.example").read_text()
    readme = (ROOT / "README.md").read_text()
    agents = (ROOT / "AGENTS.md").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()

    assert (
        "amqp://${RABBITMQ_DEFAULT_USER:-kindcare}:"
        "${RABBITMQ_DEFAULT_PASS:-kindcare_dev_only}@rabbitmq:5672//"
    ) in compose

    for name in ("RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"):
        assert RABBITMQ_CREDENTIAL_PATTERN.fullmatch(assignments[name]) is not None
    for document in (env_example,):
        assert "URI-unreserved" in document
        assert RABBITMQ_CREDENTIAL_PATTERN_TEXT in document
        assert "RABBITMQ_DEFAULT_USER" in document
        assert "RABBITMQ_DEFAULT_PASS" in document
        assert "raw" in document.lower() and "reserved" in document.lower()
        assert "unsupported" in document.lower()
    for heading in ("Clean-Clone Quick Start", "Configuration", "Troubleshooting"):
        section = _markdown_section(readme, heading)
        assert "URI-unreserved" in section, heading
        assert RABBITMQ_CREDENTIAL_PATTERN_TEXT in section, heading
        assert "RABBITMQ_DEFAULT_USER" in section, heading
        assert "RABBITMQ_DEFAULT_PASS" in section, heading
        assert "raw" in section.lower() and "reserved" in section.lower(), heading
    for heading in ("Safety And Trust Boundary", "Configuration Rules"):
        section = _markdown_section(agents, heading)
        assert "URI-unreserved" in section, heading
        assert RABBITMQ_CREDENTIAL_PATTERN_TEXT in section, heading
        assert "RABBITMQ_DEFAULT_USER" in section, heading
        assert "RABBITMQ_DEFAULT_PASS" in section, heading
        assert "raw" in section.lower() and "reserved" in section.lower(), heading


def test_readme_clean_clone_bootstrap_is_ordered_and_runnable() -> None:
    readme = (ROOT / "README.md").read_text()
    up = "docker compose up --build -d --wait"
    health = "$health = Invoke-RestMethod -Uri \"http://127.0.0.1:8000/health\""
    create = "Invoke-RestMethod -Method Post -Uri \"http://127.0.0.1:8000/api/elderly\""
    dashboard = "http://127.0.0.1:8501"

    assert "Copy-Item .env.example .env" in readme
    assert readme.index(up) < readme.index(health) < readme.index(create)
    assert readme.index(create) < readme.index(dashboard)
    assert "$deadline = (Get-Date).AddMinutes(2)" in readme
    assert "Start-Sleep -Seconds 2" in readme
    assert '"elderly_id": "E001"' in readme
    assert "201 Created" in readme
    assert '"active": true' in readme
    assert "docker compose down" in readme
    assert "docker compose down --volumes --remove-orphans" in readme


def test_readme_documents_every_unit_image_and_test_profile_service() -> None:
    readme = (ROOT / "README.md").read_text()

    for image in (
        "kindcare-backend-test",
        "kindcare-worker-test",
        "kindcare-client-test",
        "kindcare-dashboard-test",
        "kindcare-mqtt-ingestor-test",
    ):
        assert image in readme
    for service in (
        "backend-tests",
        "worker-integration",
        "worker-tests",
        "mqtt-integration-tests",
    ):
        assert service in readme
    assert "four test-profile services" in readme
    assert "requirements-dev.lock" in readme
    assert "kindcare_integration_test" in readme
    assert "kindcare_db" in readme


def test_api_documentation_has_complete_route_matrix() -> None:
    api = (ROOT / "docs" / "api-documentation.md").read_text()
    expected = {
        ("GET", "/health"),
        ("POST", "/api/elderly"),
        ("GET", "/api/elderly"),
        ("GET", "/api/elderly/{elderly_id}"),
        ("PATCH", "/api/elderly/{elderly_id}"),
        ("DELETE", "/api/elderly/{elderly_id}"),
        ("POST", "/api/health"),
        ("GET", "/api/health/{elderly_id}"),
        ("POST", "/api/activity"),
        ("GET", "/api/activity/{elderly_id}"),
        ("POST", "/api/device-status"),
        ("GET", "/api/device-status/{elderly_id}"),
        ("POST", "/api/reminders"),
        ("GET", "/api/reminders/{elderly_id}"),
        ("PATCH", "/api/reminders/{reminder_id}"),
        ("GET", "/api/alerts/{elderly_id}"),
        ("PATCH", "/api/alerts/{alert_id}"),
        ("GET", "/api/dashboard/{elderly_id}"),
        ("WS", "/ws/dashboard/{elderly_id}"),
    }

    assert _documented_routes(api) == expected
    assert "POST /api/alerts" not in api


def test_api_documentation_examples_cover_current_payload_contracts() -> None:
    api = (ROOT / "docs" / "api-documentation.md").read_text()
    objects = _json_objects(api)

    assert any(
        value.get("elderly_id") == "E001" and value.get("full_name")
        for value in objects
    )
    assert any(
        value.get("elderly_id") == "E001"
        and value.get("heart_rate") == 86
        and "event_id" not in value
        for value in objects
    )
    assert {"elderly_id": "E001", "status": "taken"} in objects
    assert {"status": "acknowledged"} in objects
    assert "Idempotency-Key" in api
    assert "1-128 visible ASCII" in api
    assert "200" in api and "201" in api and "202" in api
    assert "404" in api and "409" in api and "422" in api and "503" in api


def test_api_documentation_states_exact_reminder_taken_at_contract() -> None:
    api = " ".join(
        (ROOT / "docs" / "api-documentation.md").read_text().split()
    )

    assert (
        'Reminder creation always returns `"taken_at": null`; after it is marked '
        "taken, `taken_at` is a server-generated UTC timestamp."
    ) in api


def test_dashboard_design_maps_the_exported_figma_caregiver_console() -> None:
    design = (ROOT / "docs" / "dashboard-design.md").read_text()

    for required in (
        "figma design/Sidebar.png",
        "figma design/Main Content.png",
        "DESIGN.md",
        "#f4f7f8",
        "#10493f",
        '"Inter", "Aptos", "Segoe UI", sans-serif',
        "248px",
        "16px",
        "WebSocket",
        "Auto-refresh",
        "Accessibility",
        "900px",
        "640px",
    ):
        assert required in design
    assert "Inter is not bundled" in design
    assert "#586a64" in design
    assert "#73827d" not in design
    assert "focused views request only" in design
    assert "one refresh cycle" in design
    assert "latest 12 hours" in design
    assert "unresolved before acknowledged" in design
    assert "compact action times" in design
    assert "overflow-wrap" in design
    assert "sidebar identity" in design


def test_readme_documents_mqtt_demo_contract_and_security_boundary() -> None:
    readme = (ROOT / "README.md").read_text()

    for required in (
        "kindcare/{elderly_id}/health",
        "kindcare/{elderly_id}/activity",
        "kindcare/{elderly_id}/device",
        "kindcare/{elderly_id}/reminder",
        "idempotency_key",
        "QoS 1",
        "retained",
        "127.0.0.1:1883",
        "kindcare_mqtt_dev_only",
        "client_nodes.mqtt_node",
        "mqtt-integration-tests",
    ):
        assert required in readme


def test_reminder_patch_parser_extracts_malformed_inline_and_fenced_examples() -> None:
    markdown = """
Inline request: `PATCH /api/reminders/{reminder_id}` with `{"status":"taken"}`.

Fenced request: `PATCH /api/reminders/{reminder_id}` request body:

```json
{
  "elderly_id": "E001"
}
```
"""

    assert _reminder_patch_request_examples(markdown) == [
        {"status": "taken"},
        {"elderly_id": "E001"},
    ]


@pytest.mark.parametrize(
    ("elderly_id"),
    ["", "   ", None],
    ids=["empty", "whitespace", "null"],
)
def test_reminder_patch_contract_rejects_invalid_elderly_id(
    elderly_id: object,
) -> None:
    request = json.dumps({"elderly_id": elderly_id, "status": "taken"})
    markdown = f"`{REMINDER_PATCH_ENDPOINT}` with `{request}`."
    [body] = _reminder_patch_request_examples(markdown)

    with pytest.raises(AssertionError, match="elderly_id"):
        _assert_owner_bound_reminder_patch(body, "synthetic.md")


def test_project_reminder_patch_examples_are_owner_bound() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "api-documentation.md",
        *sorted((ROOT / "docs" / "superpowers" / "specs").glob("*task-6*.md")),
        *sorted((ROOT / "docs" / "superpowers" / "plans").glob("*task-6*.md")),
    ]
    examples_found = 0

    for path in paths:
        for body in _reminder_patch_request_examples(path.read_text()):
            examples_found += 1
            location = path.relative_to(ROOT)
            _assert_owner_bound_reminder_patch(body, location)

    assert examples_found


def test_architecture_documents_bridge_ownership_and_failure_policy() -> None:
    architecture = (ROOT / "docs" / "architecture.md").read_text()

    assert "MQTT ingestor" in architecture
    assert "FastAPI" in architecture
    assert "does not" in architecture
    assert "manual acknowledgement" in architecture
    assert "408" in architecture
    assert "425" in architecture
    assert "429" in architecture
    assert "TLS" in architecture
    assert "unclean" in architecture
    assert "durably flushed" in architecture


def test_api_documentation_contains_exact_flat_payloads() -> None:
    api = (ROOT / "docs" / "api-documentation.md").read_text()

    assert '"idempotency_key"' in api
    assert '"elderly_id"' in api
    assert '"status": "taken"' in api
    assert "PATCH /api/reminders/{reminder_id}" in api
    assert "16,384" in api

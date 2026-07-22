import pytest
import requests

from dashboard.api import (
    DashboardAPIError,
    DashboardNotFound,
    DashboardUnavailable,
    KindCareAPI,
)


class Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> dict[str, object]:
        return self.payload


class Session:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = responses
        self.calls = []

    def get(self, url: str, *, params: dict[str, object] | None, timeout: float):
        self.calls.append((url, params, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def patch(self, url: str, *, json: dict[str, object], timeout: float):
        self.calls.append((url, json, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_api_returns_data_from_success_envelope() -> None:
    session = Session([Response(200, {"success": True, "data": {"current_risk": "normal"}})])
    api = KindCareAPI("http://backend:8000/", timeout=3, session=session)

    result = api.get_summary("E001")

    assert result == {"current_risk": "normal"}
    assert session.calls == [
        ("http://backend:8000/api/dashboard/E001", None, 3)
    ]


def test_api_lists_up_to_one_hundred_active_profiles() -> None:
    profiles = [{"elderly_id": "E001", "full_name": "Margaret Lee"}]
    session = Session([Response(200, {"success": True, "data": profiles})])
    api = KindCareAPI("http://backend:8000", timeout=3, session=session)

    assert api.get_profiles() == profiles
    assert session.calls == [
        (
            "http://backend:8000/api/elderly",
            {"include_inactive": False, "limit": 100, "offset": 0},
            3,
        )
    ]


def test_api_sends_bounded_history_parameters() -> None:
    session = Session(
        [
            Response(200, {"success": True, "data": []}),
            Response(200, {"success": True, "data": []}),
            Response(200, {"success": True, "data": []}),
            Response(200, {"success": True, "data": []}),
        ]
    )
    api = KindCareAPI("http://backend:8000", session=session)

    api.get_health("E001", limit=40)
    api.get_alerts("E001", limit=12)
    api.get_activity("E001", limit=15)
    api.get_device_status("E001", limit=6)

    assert session.calls[0][1] == {"limit": 40, "offset": 0}
    assert session.calls[1][1] == {"limit": 12, "offset": 0}
    assert session.calls[2] == (
        "http://backend:8000/api/activity/E001",
        {"limit": 15, "offset": 0},
        5.0,
    )
    assert session.calls[3] == (
        "http://backend:8000/api/device-status/E001",
        {"limit": 6, "offset": 0},
        5.0,
    )


def test_api_translates_not_found_and_unavailable_statuses() -> None:
    session = Session(
        [
            Response(404, {"detail": "Elderly profile E404 not found"}),
            Response(503, {"message": "Dashboard data storage is unavailable"}),
        ]
    )
    api = KindCareAPI("http://backend:8000", session=session)

    with pytest.raises(DashboardNotFound, match="E404"):
        api.get_summary("E404")
    with pytest.raises(DashboardUnavailable, match="storage is unavailable"):
        api.get_summary("E001")


@pytest.mark.parametrize(
    "error",
    [requests.Timeout("slow"), requests.ConnectionError("offline")],
)
def test_api_translates_transport_errors_to_unavailable(error: Exception) -> None:
    api = KindCareAPI("http://backend:8000", session=Session([error]))

    with pytest.raises(DashboardUnavailable, match="Cannot reach KindCare API"):
        api.get_summary("E001")


def test_api_rejects_malformed_success_envelope() -> None:
    api = KindCareAPI(
        "http://backend:8000",
        session=Session([Response(200, {"success": False, "message": "bad"})]),
    )

    with pytest.raises(DashboardAPIError, match="invalid response"):
        api.get_summary("E001")


@pytest.mark.parametrize("payload", [[], "healthy", 42, None])
def test_api_rejects_non_object_json(payload: object) -> None:
    api = KindCareAPI(
        "http://backend:8000",
        session=Session([Response(200, payload)]),
    )

    with pytest.raises(DashboardAPIError, match="invalid response"):
        api.get_summary("E001")


def test_api_marks_reminder_taken_and_transitions_alert() -> None:
    session = Session(
        [
            Response(200, {"success": True, "data": {"status": "taken"}}),
            Response(200, {"success": True, "data": {"status": "resolved"}}),
        ]
    )
    api = KindCareAPI("http://backend:8000", session=session)

    assert api.mark_reminder_taken("E001", "reminder/1")["status"] == "taken"
    assert api.update_alert_status("alert/1", "resolved")["status"] == "resolved"
    assert session.calls == [
        (
            "http://backend:8000/api/reminders/reminder%2F1",
            {"elderly_id": "E001", "status": "taken"},
            5.0,
        ),
        ("http://backend:8000/api/alerts/alert%2F1", {"status": "resolved"}, 5.0),
    ]


def test_api_surfaces_active_source_conflict_message() -> None:
    api = KindCareAPI(
        "http://backend:8000",
        session=Session(
            [
                Response(
                    409,
                    {
                        "success": False,
                        "message": "Alert cannot be resolved while its source condition is still active",
                        "data": {"status": "not_found"},
                    },
                )
            ]
        ),
    )

    with pytest.raises(DashboardAPIError, match="source condition is still active"):
        api.update_alert_status("alert-1", "resolved")

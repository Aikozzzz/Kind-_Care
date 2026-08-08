from typing import Any
from urllib.parse import quote

import requests


class DashboardAPIError(Exception):
    """Raised when the backend returns an invalid or unsuccessful response."""


class DashboardNotFound(DashboardAPIError):
    """Raised when an active elderly profile does not exist."""


class DashboardUnavailable(DashboardAPIError):
    """Raised when the backend or its storage cannot be reached."""


class KindCareAPI:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        session: requests.Session | None = None,
        access_token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.access_token = access_token

    def login(self, login_name: str, password: str) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"login_name": login_name, "password": password},
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            raise DashboardUnavailable(f"Cannot reach KindCare API: {error}") from error
        return self._response_data(response)

    def logout(self) -> None:
        self._post("/api/auth/logout", {})

    def get_summary(self, elderly_id: str) -> dict[str, Any]:
        return self._get(f"/api/dashboard/{quote(elderly_id, safe='')}")

    def get_websocket_ticket(self, elderly_id: str) -> str:
        data = self._post(
            f"/api/auth/websocket-ticket/{quote(elderly_id, safe='')}", {}
        )
        return str(data["ticket"])

    def get_profiles(
        self, limit: int = 100, *, include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        return self._get(
            "/api/elderly",
            params={
                "include_inactive": include_inactive,
                "limit": limit,
                "offset": 0,
            },
        )

    def create_profile(self, profile: dict[str, object]) -> dict[str, Any]:
        return self._post("/api/elderly", profile)

    def update_profile(
        self, elderly_id: str, updates: dict[str, object]
    ) -> dict[str, Any]:
        return self._patch(f"/api/elderly/{quote(elderly_id, safe='')}", updates)

    def archive_profile(self, elderly_id: str) -> dict[str, Any]:
        return self._delete(f"/api/elderly/{quote(elderly_id, safe='')}")

    def restore_profile(self, elderly_id: str) -> dict[str, Any]:
        return self._post(
            f"/api/elderly/{quote(elderly_id, safe='')}/restore", {}
        )

    def create_account(self, account: dict[str, object]) -> dict[str, Any]:
        return self._post("/api/auth/accounts", account)

    def get_relationships(self, elderly_id: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/relationships",
            params={"elderly_id": elderly_id},
        )

    def create_relationship(self, relationship: dict[str, object]) -> dict[str, Any]:
        return self._post("/api/relationships", relationship)

    def update_relationship(
        self, relationship_id: str, permissions: list[str]
    ) -> dict[str, Any]:
        return self._patch(
            f"/api/relationships/{quote(relationship_id, safe='')}",
            {"permissions": permissions},
        )

    def revoke_relationship(self, relationship_id: str) -> dict[str, Any]:
        return self._delete(f"/api/relationships/{quote(relationship_id, safe='')}")

    def create_family_telegram_link(
        self, account_id: str, expires_in_seconds: int = 600
    ) -> dict[str, Any]:
        return self._post(
            f"/api/telegram/admin/link/{quote(account_id, safe='')}",
            {"expires_in_seconds": expires_in_seconds},
        )

    def get_telegram_bindings(self, elderly_id: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/telegram/admin/bindings",
            params={"elderly_id": elderly_id},
        )

    def revoke_telegram_binding(self, telegram_user_id: str) -> dict[str, Any]:
        return self._delete(
            f"/api/telegram/admin/bindings/{quote(telegram_user_id, safe='')}"
        )

    def get_health(self, elderly_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self._get(
            f"/api/health/{quote(elderly_id, safe='')}",
            params={"limit": limit, "offset": 0},
        )

    def get_alerts(self, elderly_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._get(
            f"/api/alerts/{quote(elderly_id, safe='')}",
            params={"limit": limit, "offset": 0},
        )

    def get_activity(self, elderly_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self._get(
            f"/api/activity/{quote(elderly_id, safe='')}",
            params={"limit": limit, "offset": 0},
        )

    def get_device_status(self, elderly_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._get(
            f"/api/device-status/{quote(elderly_id, safe='')}",
            params={"limit": limit, "offset": 0},
        )

    def mark_reminder_taken(
        self, elderly_id: str, reminder_id: str
    ) -> dict[str, Any]:
        return self._patch(
            f"/api/reminders/{quote(reminder_id, safe='')}",
            {"elderly_id": elderly_id, "status": "taken"},
        )

    def update_alert_status(self, alert_id: str, status: str) -> dict[str, Any]:
        return self._patch(
            f"/api/alerts/{quote(alert_id, safe='')}", {"status": status}
        )

    def _get(
        self,
        path: str,
        params: dict[str, object] | None = None,
    ) -> Any:
        try:
            kwargs: dict[str, object] = {"params": params, "timeout": self.timeout}
            if self.access_token:
                kwargs["headers"] = self._headers()
            response = self.session.get(f"{self.base_url}{path}", **kwargs)
        except requests.RequestException as error:
            raise DashboardUnavailable(f"Cannot reach KindCare API: {error}") from error

        return self._response_data(response)

    def _patch(self, path: str, body: dict[str, object]) -> Any:
        try:
            kwargs: dict[str, object] = {"json": body, "timeout": self.timeout}
            if self.access_token:
                kwargs["headers"] = self._headers()
            response = self.session.patch(f"{self.base_url}{path}", **kwargs)
        except requests.RequestException as error:
            raise DashboardUnavailable(f"Cannot reach KindCare API: {error}") from error
        return self._response_data(response)

    def _delete(self, path: str) -> Any:
        try:
            kwargs: dict[str, object] = {"timeout": self.timeout}
            if self.access_token:
                kwargs["headers"] = self._headers()
            response = self.session.delete(f"{self.base_url}{path}", **kwargs)
        except requests.RequestException as error:
            raise DashboardUnavailable(f"Cannot reach KindCare API: {error}") from error
        return self._response_data(response)

    def _post(self, path: str, body: dict[str, object]) -> Any:
        try:
            kwargs: dict[str, object] = {"json": body, "timeout": self.timeout}
            if self.access_token:
                kwargs["headers"] = self._headers()
            response = self.session.post(f"{self.base_url}{path}", **kwargs)
        except requests.RequestException as error:
            raise DashboardUnavailable(f"Cannot reach KindCare API: {error}") from error
        return self._response_data(response)

    def _headers(self) -> dict[str, str]:
        return (
            {"Authorization": f"Bearer {self.access_token}"}
            if self.access_token
            else {}
        )

    @staticmethod
    def _response_data(response: requests.Response) -> Any:
        try:
            payload = response.json()
        except (ValueError, TypeError) as error:
            raise DashboardAPIError("KindCare API returned an invalid response") from error
        if not isinstance(payload, dict):
            raise DashboardAPIError("KindCare API returned an invalid response")

        message = str(payload.get("detail") or payload.get("message") or "Request failed")
        if response.status_code == 404:
            raise DashboardNotFound(message)
        if response.status_code >= 500:
            raise DashboardUnavailable(message)
        if not 200 <= response.status_code < 300:
            raise DashboardAPIError(message)
        if payload.get("success") is not True or "data" not in payload:
            raise DashboardAPIError("KindCare API returned an invalid response")
        return payload["data"]

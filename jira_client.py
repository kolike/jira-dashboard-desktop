from typing import Any
import requests
from requests import Session
from requests.exceptions import RequestException


class JiraClient:
    def __init__(self) -> None:
        self.session: Session = requests.Session()
        self.field_name_map: dict[str, str] = {}
        self.region_field_ids: list[str] = []
        self.region_portal_field_ids: list[str] = []
        self.request_type_field_ids: list[str] = []
        self.request_type_name_map: dict[str, str] = {}
        self.issue_request_type_cache: dict[str, str] = {}

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "JiraFastWatcher/5.0",
        }

    def fetch_fields(self, base_url: str, token: str) -> None:
        url = f"{base_url.rstrip('/')}/rest/api/2/field"
        response = self.session.get(url, headers=self._headers(token), timeout=20)

        if not response.ok:
            raise RequestException(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        if not isinstance(data, list):
            return

        self.field_name_map.clear()
        self.region_field_ids.clear()
        self.region_portal_field_ids.clear()
        self.request_type_field_ids.clear()

        for item in data:
            if not isinstance(item, dict):
                continue

            field_id = str(item.get("id", "")).strip()
            field_name = str(item.get("name", "")).strip()
            if not field_id or not field_name:
                continue

            self.field_name_map[field_id] = field_name
            lowered = field_name.lower()
            if lowered == "регион":
                self.region_field_ids.append(field_id)
            elif lowered == "регион портал":
                self.region_portal_field_ids.append(field_id)
            elif lowered in {"тип запроса", "тип обращения", "request type"}:
                self.request_type_field_ids.append(field_id)

    def _resolve_request_type_name(self, base_url: str, token: str, request_type_id: str) -> str:
        if not request_type_id:
            return "Не указан"
        if request_type_id in self.request_type_name_map:
            return self.request_type_name_map[request_type_id]

        detail_url = f"{base_url.rstrip('/')}/rest/servicedeskapi/requesttype/{request_type_id}"
        detail_response = self.session.get(detail_url, headers=self._headers(token), timeout=20)
        if detail_response.ok:
            detail_payload = detail_response.json()
            payload = detail_payload if isinstance(detail_payload, dict) else {}
            name = str(payload.get("name") or "").strip()
            if name:
                self.request_type_name_map[request_type_id] = name
                return name

        return request_type_id

    def fetch_request_types(self, base_url: str, token: str) -> None:
        url = f"{base_url.rstrip('/')}/rest/servicedeskapi/requesttype"
        response = self.session.get(url, headers=self._headers(token), timeout=20)
        self.request_type_name_map.clear()
        if response.ok:
            data = response.json()
            values = data.get("values", []) if isinstance(data, dict) else []
            if isinstance(values, list):
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    request_type_id = str(item.get("id") or "").strip()
                    request_type_name = str(item.get("name") or "").strip()
                    if request_type_id and request_type_name:
                        self.request_type_name_map[request_type_id] = request_type_name

        desks_url = f"{base_url.rstrip('/')}/rest/servicedeskapi/servicedesk"
        desks_response = self.session.get(desks_url, headers=self._headers(token), timeout=20)
        if not desks_response.ok:
            return
        desks_data = desks_response.json()
        desks = desks_data.get("values", []) if isinstance(desks_data, dict) else []
        if not isinstance(desks, list):
            return

        for desk in desks:
            if not isinstance(desk, dict):
                continue
            desk_id = str(desk.get("id") or "").strip()
            if not desk_id:
                continue
            desk_types_url = f"{base_url.rstrip('/')}/rest/servicedeskapi/servicedesk/{desk_id}/requesttype"
            desk_types_response = self.session.get(desk_types_url, headers=self._headers(token), timeout=20)
            if not desk_types_response.ok:
                continue
            desk_types_data = desk_types_response.json()
            desk_types = desk_types_data.get("values", []) if isinstance(desk_types_data, dict) else []
            if not isinstance(desk_types, list):
                continue
            for item in desk_types:
                if not isinstance(item, dict):
                    continue
                request_type_id = str(item.get("id") or "").strip()
                request_type_name = str(item.get("name") or "").strip()
                if request_type_id and request_type_name:
                    self.request_type_name_map[request_type_id] = request_type_name

    def fetch_issues(self, base_url: str, token: str, jql: str) -> list[dict[str, Any]]:
        url = f"{base_url.rstrip('/')}/rest/api/2/search"
        params = {"jql": jql, "maxResults": 100, "fields": "*all"}

        response = self.session.get(url, params=params, headers=self._headers(token), timeout=20)
        if not response.ok:
            raise RequestException(f"HTTP {response.status_code}: {response.text}")

        data = response.json()
        issues = data.get("issues", [])
        return issues if isinstance(issues, list) else []

    def extract_region(self, fields: dict[str, Any]) -> str:
        for field_id in self.region_field_ids + self.region_portal_field_ids:
            parsed = self._parse_region_value(fields.get(field_id))
            if parsed:
                return parsed

        for key, value in fields.items():
            readable = self.field_name_map.get(key, key).strip().lower()
            if readable in {"регион", "регион портал"}:
                parsed = self._parse_region_value(value)
                if parsed:
                    return parsed

        return "Не указан"

    def extract_author(self, fields: dict[str, Any]) -> str:
        user = fields.get("creator") or fields.get("reporter")
        if isinstance(user, dict):
            return str(user.get("displayName") or user.get("name") or user.get("emailAddress") or "Неизвестно")
        return "Неизвестно"

    def extract_request_type(self, base_url: str, token: str, issue: dict[str, Any]) -> str:
        issue_key = str(issue.get("key") or "").strip()
        if not issue_key:
            return "Не указан"

        cached = self.issue_request_type_cache.get(issue_key)
        if cached:
            return cached

        url = f"{base_url.rstrip('/')}/rest/servicedeskapi/request/{issue_key}"
        response = self.session.get(url, headers=self._headers(token), timeout=20)
        if response.ok:
            response_data = response.json()
            data = response_data if isinstance(response_data, dict) else {}
            request_type_id = str(data.get("requestTypeId") or "").strip()
            if request_type_id:
                name = self._resolve_request_type_name(base_url, token, request_type_id)
                self.issue_request_type_cache[issue_key] = name
                return name

        fields = issue.get("fields", {}) or {}
        for field_id in self.request_type_field_ids:
            parsed = self._parse_region_value(fields.get(field_id))
            if parsed:
                if parsed.isdigit():
                    parsed = self._resolve_request_type_name(base_url, token, parsed)
                self.issue_request_type_cache[issue_key] = parsed
                return parsed

        for key, value in fields.items():
            readable = self.field_name_map.get(key, key).strip().lower()
            if readable in {"тип запроса", "тип обращения", "request type"}:
                parsed = self._parse_region_value(value)
                if parsed:
                    if parsed.isdigit():
                        parsed = self._resolve_request_type_name(base_url, token, parsed)
                    self.issue_request_type_cache[issue_key] = parsed
                    return parsed

        return "Не указан"

    @staticmethod
    def _parse_region_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("value", "name", "displayName"):
                candidate = value.get(key)
                if candidate:
                    return str(candidate).strip()
        if isinstance(value, list):
            if not value:
                return ""
            first = value[0]
            if isinstance(first, dict):
                for key in ("value", "name", "displayName"):
                    candidate = first.get(key)
                    if candidate:
                        return str(candidate).strip()
            return str(first).strip()
        return str(value).strip()

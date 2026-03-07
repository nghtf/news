from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class StateStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._lock = Lock()
        if self._path.exists() and self._path.is_dir():
            raise ValueError(
                f"STATE_FILE points to a directory, expected a file: {self._path}. "
                "If you use Docker bind mount, create state.json as a file on host."
            )
        if not self._path.exists():
            self._write({"seen_links": [], "pending": {}})
            return
        self._validate_state_file()

    def _validate_state_file(self) -> None:
        with self._lock:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in state file: {self._path}") from exc
        if not isinstance(data, dict) or "seen_links" not in data or "pending" not in data:
            raise ValueError(
                f"Invalid state file format: {self._path}. "
                "Expected JSON object with keys: seen_links, pending."
            )

    def _read(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_seen(self, link: str) -> bool:
        data = self._read()
        return link in data["seen_links"]

    def mark_seen(self, link: str) -> None:
        data = self._read()
        if link not in data["seen_links"]:
            data["seen_links"].append(link)
            self._write(data)

    def save_pending(self, draft_id: str, payload: dict[str, Any]) -> None:
        data = self._read()
        data["pending"][draft_id] = payload
        self._write(data)

    def get_pending(self, draft_id: str) -> dict[str, Any] | None:
        data = self._read()
        return data["pending"].get(draft_id)

    def find_pending_by_admin_message_id(self, admin_message_id: int) -> tuple[str, dict[str, Any]] | None:
        data = self._read()
        for draft_id, item in data["pending"].items():
            if int(item.get("admin_message_id", 0)) == int(admin_message_id):
                return draft_id, item
        return None

    def delete_pending(self, draft_id: str) -> None:
        data = self._read()
        if draft_id in data["pending"]:
            del data["pending"][draft_id]
            self._write(data)

    def pending_count(self) -> int:
        data = self._read()
        return len(data["pending"])

    def seen_count(self) -> int:
        data = self._read()
        return len(data["seen_links"])

    def has_pending_link(self, link: str) -> bool:
        data = self._read()
        for item in data["pending"].values():
            if item.get("link") == link:
                return True
        return False

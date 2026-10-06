from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class CloudClient:
    """Small stdlib-only client for ysgl.bot.cd/astrbot.

    The plugin intentionally caches the last successful payload locally so a
    temporary website/network outage never disables the game itself.
    """

    def __init__(self, base_url: str, api_key: str, cache_path: Path, timeout: int = 12):
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.cache_path = cache_path
        self.timeout = max(3, int(timeout or 12))

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _request(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/api.php?action={action}"
        body = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "AstrBot-GroupWorld/1.7.0",
            "X-AstrBot-Cloud-Key": self.api_key,
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"云端 HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"云端连接失败: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("云端返回的不是有效 JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeError("云端返回格式错误")
        if not data.get("ok", False):
            raise RuntimeError(str(data.get("message") or "云端请求失败"))
        return data

    async def sync(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("未配置云端地址或 API Key")
        data = await asyncio.to_thread(self._request, "plugin_sync", options or {})
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        payload.setdefault("bosses", [])
        payload.setdefault("products", [])
        payload.setdefault("tutorials", {})
        payload.setdefault("monsters", [])
        payload.setdefault("npcs", [])
        payload.setdefault("custom_json", [])
        payload["synced_at"] = int(time.time())
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)
        return payload

    def load_cache(self) -> dict[str, Any]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"configured": False, "reachable": False, "message": "未配置 API Key"}
        try:
            data = await asyncio.to_thread(self._request, "plugin_status")
            return {
                "configured": True,
                "reachable": True,
                "message": str(data.get("message") or "连接正常"),
                "site": data.get("site") or {},
                "user": data.get("user") or {},
            }
        except Exception as exc:
            return {"configured": True, "reachable": False, "message": str(exc)}

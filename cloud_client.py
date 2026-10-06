from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import httpx
except Exception:  # pragma: no cover - installation supplies httpx via requirements.txt
    httpx = None


class CloudClient:
    """YSGL 云端玩法 HTTP 客户端。

    设计目标：
    - 只通过 HTTPS + API Key 访问公开云端目录。
    - 同时发送多个常见 API Key Header，兼容宝塔/Nginx/Apache/FastCGI 的 Header 转发差异。
    - 不记录完整 API Key，异常消息里也不包含 Key。
    - 直接连接优先，代理环境仅作为第二兜底，减少服务器环境变量代理导致的连接失败。
    - 所有网络 IO 都放到 asyncio 线程池中，不阻塞 AstrBot 事件循环。
    """

    def __init__(self, base_url: str, api_key: str, cache_path: Path, timeout: int = 12):
        self.base_url = self._normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.cache_path = cache_path
        self.timeout = max(3, min(60, int(timeout or 12)))

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        raw = (value or "").strip()
        if not raw:
            return ""
        if not raw.lower().startswith(("http://", "https://")):
            raw = "https://" + raw
        parts = urlsplit(raw)
        path = parts.path.rstrip("/")
        # 允许用户误填 /api.php，但统一规范为站点目录。
        if path.lower().endswith("/api.php"):
            path = path[:-8].rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc, path, "", "")).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> dict[str, str]:
        # 不改变服务端既有协议，同时增加 X-API-Key 作为宝塔代理环境下的通用兜底。
        return {
            "Accept": "application/json",
            "User-Agent": "AstrBot-GroupWorld/1.9.2",
            "X-AstrBot-Cloud-Key": self.api_key,
            "X-AstrBot-API-Key": self.api_key,
            "X-API-Key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    def _request_once(
        self,
        action: str,
        payload: dict[str, Any] | None,
        *,
        trust_env: bool,
    ) -> dict[str, Any]:
        if httpx is None:
            raise RuntimeError("云端客户端缺少 httpx 依赖，请重新安装插件依赖")
        url = f"{self.base_url}/api.php"
        params = {"action": action}
        timeout = httpx.Timeout(self.timeout, connect=min(8.0, float(self.timeout)))
        # Cloud API 是小 JSON，不需要 HTTP/2；HTTP/1.1 在宝塔/FastCGI 下兼容性更高。
        request_payload = None if payload is None else dict(payload)
        if request_payload is not None:
            request_payload.setdefault("api_key", self.api_key)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            verify=True,
            trust_env=trust_env,
            http2=False,
        ) as client:
            # status is also POSTed so the API key can survive proxies/FastCGI
            # configurations that strip custom headers.
            response = client.post(url, params=params, json=request_payload or {"api_key": self.api_key}, headers=self._headers())

        # 站点被错误重定向到 HTTP 时，不继续信任跳转结果。
        final_scheme = (urlsplit(str(response.url)).scheme or "").lower()
        if self.base_url.startswith("https://") and final_scheme != "https":
            raise RuntimeError("云端 HTTPS 被重定向到非 HTTPS 地址，请检查宝塔强制 HTTPS/反向代理配置")

        raw = response.text.lstrip("\ufeff")
        try:
            data = response.json()
        except Exception as exc:
            snippet = raw.strip().replace("\n", " ")[:300]
            raise RuntimeError(
                f"云端返回不是有效 JSON（HTTP {response.status_code}）：{snippet or '空响应'}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"云端返回格式错误（HTTP {response.status_code}）")
        if response.status_code >= 400 or not data.get("ok", False):
            message = str(data.get("message") or f"云端 HTTP {response.status_code}")
            # 某些 Web 服务器只返回 401/403，不带可读提示。
            if response.status_code == 401 and not message:
                message = "API Key 无效或账号已停用"
            raise RuntimeError(message)
        return data

    def _request(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if httpx is None:
            raise RuntimeError("云端客户端缺少 httpx 依赖，请重新安装插件依赖")
        if not self.enabled:
            raise RuntimeError("未配置云端地址或 API Key")

        # Header 是首选认证方式；POST 同时附带 api_key 作为宝塔/FastCGI
        # 丢失自定义 Header 时的兼容兜底。服务端只使用它做本次认证，
        # 不会重新保存明文 Key。
        request_payload = dict(payload or {})
        request_payload.setdefault("api_key", self.api_key)

        last_exc: Exception | None = None
        # 直接网络优先，失败后再允许系统代理兜底；两种都失败才返回最终错误。
        for trust_env in (False, True):
            for attempt in range(2):
                try:
                    return self._request_once(action, payload, trust_env=trust_env)
                except httpx.ConnectError as exc:
                    last_exc = exc
                except httpx.TimeoutException as exc:
                    last_exc = exc
                except httpx.HTTPError as exc:
                    last_exc = exc
                except RuntimeError:
                    raise
                if attempt == 0:
                    time.sleep(0.35)

        if last_exc is not None:
            if isinstance(last_exc, httpx.TimeoutException):
                raise RuntimeError(f"云端连接超时（>{self.timeout} 秒）") from last_exc
            if isinstance(last_exc, httpx.ConnectError):
                host = urlsplit(self.base_url).netloc or "未知主机"
                raise RuntimeError(f"云端连接失败：无法连接 {host}，请检查服务器出网、DNS、证书或宝塔反向代理") from last_exc
            raise RuntimeError(f"云端网络错误：{type(last_exc).__name__}") from last_exc
        raise RuntimeError("云端请求失败")

    async def sync(self, options: dict[str, Any] | None = None) -> dict[str, Any]:
        data = await asyncio.to_thread(self._request, "plugin_sync", options or {})
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        for key, default in (
            ("bosses", []),
            ("products", []),
            ("tutorials", {}),
            ("monsters", []),
            ("npcs", []),
            ("community_packages", []),
            ("selected_packages", []),
        ):
            payload.setdefault(key, default)
        payload["synced_at"] = int(time.time())
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.cache_path)
        return payload

    async def site_info(self) -> dict[str, Any]:
        data = await asyncio.to_thread(self._request, "site_info", {"api_key": self.api_key})
        return data

    async def announcements(self) -> list[dict[str, Any]]:
        data = await asyncio.to_thread(self._request, "announcements", {"api_key": self.api_key})
        rows = data.get("announcements") if isinstance(data.get("announcements"), list) else []
        return [dict(x) for x in rows if isinstance(x, dict)]

    async def package(self, package_id: int = 0, package_no: str = "") -> dict[str, Any]:
        body: dict[str, Any] = {}
        if str(package_no).strip():
            body["package_no"] = str(package_no).strip()
        else:
            body["id"] = int(package_id)
        data = await asyncio.to_thread(self._request, "plugin_package", body)
        package = data.get("package") if isinstance(data.get("package"), dict) else {}
        return package

    def load_cache(self) -> dict[str, Any]:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "configured": False,
                "reachable": False,
                "message": "未配置云端地址或 API Key",
                "base_url": self.base_url,
            }
        try:
            # POST body carries a redundant API key fallback for 宝塔/Nginx/Apache
            # setups that strip custom request headers. The website never persists
            # this value from the request; it is used only for this authentication.
            data = await asyncio.to_thread(self._request, "plugin_status", {"api_key": self.api_key})
            return {
                "configured": True,
                "reachable": True,
                "message": str(data.get("message") or "连接正常"),
                "site": data.get("site") or {},
                "user": data.get("user") or {},
                "counts": data.get("counts") or {},
                "api_version": data.get("api_version") or "2",
                "base_url": self.base_url,
            }
        except Exception as exc:
            msg = str(exc)
            if "401" in msg or "API Key 无效" in msg:
                msg += "；请确认 Key 完整、未重新生成，且网站账号状态为启用"
            elif "403" in msg:
                msg += "；请检查网站管理员是否停用了账号或当前接口权限"
            elif "404" in msg:
                msg += "；请确认地址为 https://ysgl.bot.cd/astrbot，不要填写站点首页或其他路径"
            elif "JSON" in msg:
                msg += "；请检查宝塔 PHP 是否把 warning/notice 输出到了接口响应"
            return {
                "configured": True,
                "reachable": False,
                "message": msg,
                "base_url": self.base_url,
            }

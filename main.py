from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import secrets
import os
import shutil
import tracemalloc
import resource
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - optional metrics dependency
    psutil = None

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
try:
    import astrbot.api.message_components as Comp
except Exception:  # pragma: no cover - compatibility fallback
    Comp = None
from astrbot.api.star import Context, Star

try:
    from astrbot.api.web import error_response, file_response, json_response, request, PluginUploadFile
except Exception:  # pragma: no cover - compatibility fallback
    try:
        from astrbot.api.web import error_response, file_response, json_response, request
    except Exception:
        error_response = file_response = json_response = request = None
    PluginUploadFile = None

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - compatibility fallback
    get_astrbot_data_path = None

try:
    from .core.database import Database
    from .core.globaldb import GlobalPlayerDB
    from .core.engine import ACHIEVEMENT_INFO, PROFESSIONS, WORLD_REGIONS, WorldEngine
    from .cloud_client import CloudClient
except ImportError:  # AstrBot loader compatibility when main.py is imported as a standalone module
    from core.database import Database
    from core.globaldb import GlobalPlayerDB
    from core.engine import ACHIEVEMENT_INFO, PROFESSIONS, WORLD_REGIONS, WorldEngine
    from cloud_client import CloudClient

PLUGIN_NAME = "astrbot_plugin_group_world"
CLOUD_DEFAULT_URL = "https://ysgl.bot.cd/astrbot"


class Main(Star):
    """群聊世界 V1.13.5.

    The plugin deliberately relies on AstrBot's unified event/message layer.
    This keeps the game logic independent from QQ's transport while declaring
    support for the QQ official adapters in metadata.yaml.
    """

    def __init__(self, context: Context, config: Any):
        super().__init__(context)
        # AstrBot passes an AstrBotConfig (Dict-like + save_config()). Older/plugin-test
        # environments may pass a plain dict. Keep the original object whenever possible
        # so native WebUI configuration writes can persist correctly.
        self.config = config if config is not None else {}
        self._closed = False
        self._task: asyncio.Task | None = None
        self._web_tokens: dict[str, tuple[str, int]] = {}
        self._web_write_sessions: dict[str, int] = {}
        self._last_proactive_broadcast_at = 0.0
        self._web_action_guard: dict[tuple[str, str], float] = {}
        self._duel_lock = asyncio.Lock()
        self._broadcast_campaign_tasks: dict[int, asyncio.Task] = {}
        self._broadcast_oneoff_tasks: set[asyncio.Task] = set()
        self._cloud_data: dict[str, Any] = {}
        self._cloud_last_sync_at = 0.0
        self._cloud_sync_lock = asyncio.Lock()
        self._last_auto_cleanup_date = ""
        self._memory_trace_enabled = False
        self._schema = {}
        try:
            self._schema = json.loads((Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        except Exception:
            self._schema = {}
        self._migrate_help_menu_config()

        if get_astrbot_data_path:
            base = Path(get_astrbot_data_path())
        else:
            base = Path("data")
        self.data_dir = base / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            if not tracemalloc.is_tracing():
                tracemalloc.start(10)
                self._memory_trace_enabled = True
        except Exception:
            self._memory_trace_enabled = False
        self._cloud_cache_path = self.data_dir / "cloud-data.json"
        self._cloud_data = self._load_cloud_cache()
        self.db = Database(self.data_dir / "group_world.db")
        self.engine_db = GlobalPlayerDB(self.db)
        self.engine = WorldEngine(self.engine_db, self.config)
        self._apply_cloud_data_to_engine()
        self._cloud_client = CloudClient(
            str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            str(self.config.get("cloud_api_key") or ""),
            self._cloud_cache_path,
            int(self.config.get("cloud_timeout_seconds", 12) or 12),
        )

        self._register_web_api()
        logger.info("[群聊世界] 插件已加载，数据库：%s", self.db.path)

    # ------------------------- cloud data -------------------------

    def _load_cloud_cache(self) -> dict[str, Any]:
        try:
            data = json.loads(self._cloud_cache_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ------------------------- native config: help menu -------------------------

    def _migrate_help_menu_config(self) -> None:
        """兼容旧版平铺配置，但绝不覆盖已经存在的新嵌套配置。

        AstrBot 会按 _conf_schema.json 自动创建 object 配置。旧版本曾把三个
        菜单开关放在根级配置中；升级时仅在嵌套配置缺失/为空时迁移旧值，避免
        每次加载都把用户刚刚在原生配置页修改的值覆盖回去。
        """
        try:
            section = self.config.get("help_menu_settings")
            defaults = {"image_enabled": True, "image_first": True, "text_enabled": True}
            legacy = {
                "image_enabled": self.config.get("help_menu_image_enabled", None),
                "image_first": self.config.get("help_menu_image_first", None),
                "text_enabled": self.config.get("help_menu_text_enabled", None),
            }
            had_valid_nested = isinstance(section, dict) and any(k in section for k in defaults)
            if not had_valid_nested:
                section = dict(defaults)
                for key, value in legacy.items():
                    if value is not None:
                        section[key] = self._config_bool(value, defaults[key])
            else:
                section = {**defaults, **dict(section)}

            self.config["help_menu_settings"] = section
            # Hidden legacy keys remain synchronized for older integrations, but they
            # are never allowed to override the nested section.
            self.config["help_menu_image_enabled"] = self._config_bool(section["image_enabled"], True)
            self.config["help_menu_image_first"] = self._config_bool(section["image_first"], True)
            self.config["help_menu_text_enabled"] = self._config_bool(section["text_enabled"], True)
            if "_help_menu_config_migrated" in self.config:
                self.config["_help_menu_config_migrated"] = True
            self._save_config_object(self.config)
        except Exception as exc:
            logger.warning("[群聊世界] 帮助菜单配置迁移失败：%s", exc)

    @staticmethod
    def _config_bool(value: Any, default: bool = False) -> bool:
        """统一处理 AstrBot 配置中的 bool/int/string，尤其避免 bool("false")==True。"""
        if isinstance(value, bool): return value
        if value is None: return default
        if isinstance(value, (int, float)): return bool(value)
        text = str(value).strip().lower()
        if text in {"false","0","off","no","n","disabled","关闭","否"}: return False
        if text in {"true","1","on","yes","y","enabled","开启","是"}: return True
        return default

    def _help_menu_config_path(self) -> Path | None:
        config_path = getattr(self.config, "config_path", None)
        if config_path:
            try:
                return Path(str(config_path))
            except Exception:
                pass
        # AstrBot stores plugin configuration under data/config/<plugin>_config.json.
        # Keep this fallback even when the file does not exist yet, because the first
        # page save should be able to create it.
        try:
            base = Path(get_astrbot_data_path()) if get_astrbot_data_path else Path("data")
            return base / "config" / f"{PLUGIN_NAME}_config.json"
        except Exception:
            return None

    def _refresh_help_menu_config_from_disk(self) -> None:
        """优先读取 AstrBot 实际配置文件，避免旧内存值或字符串布尔值继续控制 /帮助。"""
        try:
            path = self._help_menu_config_path()
            if not path or not path.is_file():
                return
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            section = data.get("help_menu_settings") if isinstance(data, dict) else None
            if not isinstance(section, dict):
                section = {
                    "image_enabled": data.get("help_menu_image_enabled", True),
                    "text_enabled": data.get("help_menu_text_enabled", True),
                    "image_first": data.get("help_menu_image_first", True),
                }
            normalized = {
                "image_enabled": self._config_bool(section.get("image_enabled"), True),
                "text_enabled": self._config_bool(section.get("text_enabled"), True),
                "image_first": self._config_bool(section.get("image_first"), True),
            }
            self.config["help_menu_settings"] = normalized
            self.config["help_menu_image_enabled"] = normalized["image_enabled"]
            self.config["help_menu_text_enabled"] = normalized["text_enabled"]
            self.config["help_menu_image_first"] = normalized["image_first"]
        except Exception as exc:
            logger.debug("[群聊世界] 刷新帮助菜单原生配置失败：%s", exc)

    def _help_menu_value(self, key: str, default: Any) -> Any:
        section = self.config.get("help_menu_settings")
        if isinstance(section, dict) and key in section:
            return self._config_bool(section.get(key), default) if key in {"image_enabled","text_enabled","image_first"} else section.get(key)
        value = self.config.get(f"help_menu_{key}", default)
        return self._config_bool(value, default) if key in {"image_enabled","text_enabled","image_first"} else value

    def _set_help_menu_config(self, **changes: Any) -> None:
        """更新帮助菜单配置，并同步旧版平铺字段。

        这里必须只使用固定默认值，不能引用不存在的局部变量；旧版实现会在
        点击“保存设置”或上传图片时因为 NameError 导致整个操作失败。
        """
        defaults = {"image_enabled": True, "image_first": True, "text_enabled": True}
        section = self.config.get("help_menu_settings")
        if not isinstance(section, dict):
            section = {}
        else:
            section = dict(section)
        mapping = {
            "image_enabled": "help_menu_image_enabled",
            "image_first": "help_menu_image_first",
            "text_enabled": "help_menu_text_enabled",
        }
        for key, value in changes.items():
            if key not in mapping:
                continue
            normalized = self._config_bool(value, defaults[key])
            section[key] = normalized
            self.config[mapping[key]] = normalized
        for key, default in defaults.items():
            section[key] = self._config_bool(section.get(key), default)
        self.config["help_menu_settings"] = section

    # ------------------------- help menu image -------------------------

    @property
    def _menu_default_path(self) -> Path:
        return Path(__file__).parent / "menu_default.jpg"

    @property
    def _menu_custom_paths(self) -> tuple[Path, ...]:
        return tuple(self.data_dir / f"menu_image{ext}" for ext in (".jpg", ".png", ".webp"))

    def _menu_image_path(self) -> Path | None:
        if not bool(self._help_menu_value("image_enabled", True)):
            return None
        for custom in self._menu_custom_paths:
            if custom.exists() and custom.is_file():
                return custom
        default = self._menu_default_path
        if default.exists() and default.is_file():
            return default
        return None

    def _save_config_object(self, config: Any) -> bool:
        """保存 AstrBotConfig；对旧版/测试环境的普通 dict 提供文件落盘兜底。"""
        try:
            save = getattr(config, "save_config", None)
            if callable(save):
                result = save()
                # save_config 通常是同步函数；某些兼容封装可能返回 awaitable。
                if hasattr(result, "__await__"):
                    # This helper is intentionally sync; defer async-compatible objects
                    # to the manual file fallback instead of trying to run a nested loop.
                    raise RuntimeError("异步 save_config 无法在当前同步保存路径中等待")
                return True
        except Exception as exc:
            logger.debug("[群聊世界] AstrBotConfig.save_config 未成功，尝试文件落盘：%s", exc)

        path = self._help_menu_config_path()
        if path is None:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(config) if hasattr(config, "items") else {}
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)
            return True
        except Exception as exc:
            logger.warning("[群聊世界] 配置文件落盘失败：%s", exc)
            return False

    def _menu_status(self, include_image: bool = False) -> dict[str, Any]:
        path = self._menu_image_path()
        payload = {
            "enabled": bool(self._help_menu_value("image_enabled", True)),
            "first": bool(self._help_menu_value("image_first", True)),
            "send_image": bool(self._help_menu_value("image_enabled", True)),
            "send_text": bool(self._help_menu_value("text_enabled", True)),
            "has_custom": any(p.exists() for p in self._menu_custom_paths),
            "filename": path.name if path else "",
            "path_available": bool(path),
        }
        if include_image and path:
            try:
                raw = path.read_bytes()
                mime = {".jpg":"image/jpeg", ".jpeg":"image/jpeg", ".png":"image/png", ".webp":"image/webp"}.get(path.suffix.lower(), "application/octet-stream")
                payload["image_data_url"] = f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            except Exception as exc:
                payload["image_error"] = str(exc)
        return payload

    def _cloud_enabled(self) -> bool:
        return bool(self.config.get("cloud_enabled", False) and str(self.config.get("cloud_api_key") or "").strip())

    async def _cloud_sync_if_due(self, force: bool = False) -> dict[str, Any]:
        if not self._cloud_enabled():
            return self._cloud_data
        if not force and not bool(self.config.get("cloud_auto_sync", True)):
            return self._cloud_data
        interval = max(1, int(self.config.get("cloud_sync_interval_minutes", 15) or 15)) * 60
        if not force and (time.monotonic() - self._cloud_last_sync_at) < interval:
            return self._cloud_data
        async with self._cloud_sync_lock:
            if not force and (time.monotonic() - self._cloud_last_sync_at) < interval:
                return self._cloud_data
            self._cloud_client = CloudClient(
                str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
                str(self.config.get("cloud_api_key") or ""),
                self._cloud_cache_path,
                int(self.config.get("cloud_timeout_seconds", 12) or 12),
            )
            try:
                selected = self._cloud_selected_package_nos()
                self._cloud_data = await self._cloud_client.sync({
                    "include_official": bool(self.config.get("cloud_use_official_content", True)),
                    "include_own": bool(self.config.get("cloud_use_own_content", True)),
                    "include_community": bool(self.config.get("cloud_use_community_content", True)),
                    "include_packages": bool(self.config.get("cloud_sync_packages", True)),
                    "selected_package_nos": selected,
                })
                self._cloud_data['selected_package_nos'] = selected
                self._cloud_data.setdefault("custom_json_packages", [])
                self._cloud_data.setdefault("selected_packages", [])
                self._cloud_data.setdefault("crafting_recipes", {})
                self._cloud_last_sync_at = time.monotonic()
                self._apply_cloud_data_to_engine()
                logger.info(
                    "[群聊世界] 云端数据同步成功：Boss=%s 商品=%s 教程=%s JSON=%s 选中=%s",
                    len(self._cloud_data.get("bosses", [])),
                    len(self._cloud_data.get("products", [])),
                    len(self._cloud_data.get("tutorials", {})),
                    len(self._cloud_data.get("community_packages", [])),
                    len(selected),
                )
            except Exception as exc:
                logger.warning("[群聊世界] 云端数据同步失败，继续使用本地缓存：%s", exc)
        return self._cloud_data

    def _cloud_selected_package_nos(self) -> list[str]:
        raw = self.config.get("cloud_selected_package_nos", [])
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw[:50]:
            value = str(x).strip()
            if value and len(value) <= 64 and value not in out:
                out.append(value)
        return out

    def _cloud_shop_json(self) -> dict[str, Any]:
        data = self._cloud_data.get("products", []) if isinstance(self._cloud_data, dict) else []
        out: dict[str, Any] = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("item_no") or item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            if not code or not name:
                continue
            try:
                price = max(0, int(item.get("price", 0)))
            except Exception:
                continue
            out[code] = [name, price, str(item.get("description") or item.get("intro") or "云端自定义商品")]
        return out

    def _merge_cloud_package_into_cache(self, package: dict[str, Any]) -> dict[str, int]:
        """Cache a community package and merge only its safe, recognized data types."""
        payload = package.get("payload") if isinstance(package.get("payload"), dict) else {}
        self._cloud_data.setdefault("custom_json_packages", [])
        package_id = str(package.get("package_no") or package.get("id") or "").strip()
        if package_id:
            existing = {str(x.get("package_no") or x.get("id")) for x in self._cloud_data["custom_json_packages"] if isinstance(x, dict)}
            if package_id not in existing:
                self._cloud_data["custom_json_packages"].append(package)
        counts = {"bosses": 0, "products": 0, "monsters": 0, "npcs": 0, "tutorials": 0, "crafting_recipes": 0}
        for key in ("bosses", "products", "monsters", "npcs"):
            values = payload.get(key)
            if isinstance(values, list):
                current = self._cloud_data.setdefault(key, [])
                seen = {str(x.get("id") or x.get("item_no") or x.get("code") or "") for x in current if isinstance(x, dict)}
                for item in values[:500]:
                    if not isinstance(item, dict):
                        continue
                    ident = str(item.get("id") or item.get("item_no") or item.get("code") or "").strip()
                    if not ident or ident not in seen:
                        current.append(item)
                        seen.add(ident)
                        counts[key] += 1
        recipes = payload.get("crafting_recipes")
        if isinstance(recipes, dict):
            current_recipes = self._cloud_data.setdefault("crafting_recipes", {})
            for rid, value in list(recipes.items())[:200]:
                if str(rid).strip() and isinstance(value, dict) and value.get("name") and isinstance(value.get("materials"), dict):
                    current_recipes[str(rid)] = dict(value)
                    counts["crafting_recipes"] += 1
        pages = payload.get("tutorials")
        if isinstance(pages, dict):
            tutorials = self._cloud_data.setdefault("tutorials", {})
            for k, v in list(pages.items())[:100]:
                if str(k).strip() and str(k) not in tutorials and isinstance(v, str):
                    tutorials[str(k)] = v
                    counts["tutorials"] += 1
        payload=package.get('payload') if isinstance(package.get('payload'),dict) else {}
        if isinstance(payload.get('bounty_templates'),list):
            cur=self._cloud_data.setdefault('bounty_templates',[])
            existing={str(x.get('id') or x.get('code')) for x in cur if isinstance(x,dict)}
            for item in payload['bounty_templates'][:100]:
                if isinstance(item,dict):
                    iid=str(item.get('id') or '');
                    if iid and iid not in existing: cur.append(item); existing.add(iid)
                    elif iid:
                        for i,x in enumerate(cur):
                            if isinstance(x,dict) and str(x.get('id') or x.get('code'))==iid: cur[i]=item; break
            counts['bounty_templates']=len(payload['bounty_templates'])
        if isinstance(payload.get('global_boss'),dict):
            self._cloud_data['global_boss_profile']=dict(payload['global_boss']); counts['global_boss']=1
        return counts

    def _cloud_active_merge_counts(self) -> dict[str, int]:
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        counts = {"products": 0, "bosses": 0, "monsters": 0, "npcs": 0, "tutorials": 0, "crafting_recipes": 0}
        selected = data.get("selected_packages", [])
        if not isinstance(selected, list):
            return counts
        for pkg in selected:
            payload = pkg.get("payload") if isinstance(pkg, dict) and isinstance(pkg.get("payload"), dict) else {}
            for key in ("products", "bosses", "monsters", "npcs"):
                values = payload.get(key)
                if isinstance(values, list):
                    counts[key] += sum(1 for x in values[:500] if isinstance(x, dict))
            recipes = payload.get("crafting_recipes")
            if isinstance(recipes, dict):
                counts["crafting_recipes"] += sum(1 for rid, value in list(recipes.items())[:200] if str(rid).strip() and isinstance(value, dict) and value.get("name") and isinstance(value.get("materials"), dict))
            pages = payload.get("tutorials")
            if isinstance(pages, dict):
                counts["tutorials"] += sum(1 for k, v in list(pages.items())[:100] if str(k).strip() and isinstance(v, str))
        return counts

    def _apply_cloud_data_to_engine(self) -> None:
        """Build an isolated cloud catalog. Local custom config remains authoritative."""
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        data.setdefault("community_packages", [])
        data.setdefault("custom_json_packages", [])
        products: dict[str, Any] = {}
        bosses: dict[str, Any] = {}
        monsters: dict[str, Any] = {}
        npcs: list[dict[str, Any]] = []
        tutorials: dict[str, str] = {}
        crafting_recipes: dict[str, Any] = {}

        def merge_package_payload(pkg: dict[str, Any]) -> None:
            payload = pkg.get("payload") if isinstance(pkg.get("payload"), dict) else {}
            for key, target in (("products", products), ("bosses", bosses), ("monsters", monsters)):
                vals = payload.get(key)
                if not isinstance(vals, list):
                    continue
                for item in vals[:500]:
                    if not isinstance(item, dict):
                        continue
                    ident = str(item.get("item_no") or item.get("id") or item.get("code") or "").strip()
                    if ident:
                        target[ident] = item
            vals = payload.get("npcs")
            if isinstance(vals, list):
                for item in vals[:100]:
                    if isinstance(item, dict) and item not in npcs:
                        npcs.append(item)
            vals = payload.get("crafting_recipes")
            if isinstance(vals, dict):
                for rid, item in list(vals.items())[:200]:
                    if str(rid).strip() and isinstance(item, dict) and item.get("name") and isinstance(item.get("materials"), dict):
                        crafting_recipes[str(rid)] = dict(item)
            vals = payload.get("tutorials")
            if isinstance(vals, dict):
                for k, v in list(vals.items())[:100]:
                    if isinstance(v, str) and str(k) not in tutorials:
                        tutorials[str(k)] = v

        # Published catalog returned by the server.
        for item in data.get("products", []) if isinstance(data.get("products"), list) else []:
            if isinstance(item, dict):
                ident = str(item.get("item_no") or item.get("id") or "").strip()
                if ident:
                    products[ident] = item
        for item in data.get("bosses", []) if isinstance(data.get("bosses"), list) else []:
            if isinstance(item, dict):
                ident = str(item.get("id") or item.get("code") or item.get("boss_id") or "").strip()
                if ident:
                    bosses[ident] = item
        for item in data.get("monsters", []) if isinstance(data.get("monsters"), list) else []:
            if isinstance(item, dict):
                ident = str(item.get("id") or "").strip()
                if ident:
                    monsters[ident] = item
        for item in data.get("tutorials", {}) if isinstance(data.get("tutorials"), dict) else {}:
            if isinstance(item, str):
                tutorials[str(item)] = data["tutorials"][item]

        # Cached crafting recipes imported directly from a package are also safe data.
        cached_recipes = data.get("crafting_recipes")
        if isinstance(cached_recipes, dict):
            for rid, item in list(cached_recipes.items())[:200]:
                if str(rid).strip() and isinstance(item, dict) and item.get("name") and isinstance(item.get("materials"), dict):
                    crafting_recipes[str(rid)] = dict(item)

        # Selected JSON packages are additional content; a package can add entries but not run code.
        selected = data.get("selected_packages", [])
        if isinstance(selected, list):
            for pkg in selected:
                if isinstance(pkg, dict):
                    merge_package_payload(pkg)

        effects = {}
        for code, item in products.items():
            if isinstance(item, dict) and isinstance(item.get("effect"), dict):
                safe = {}
                for k in ("heal_hp", "heal_stamina", "add_coins", "add_exp"):
                    try:
                        safe[k] = max(0, min(10_000_000, int(item["effect"].get(k, 0) or 0)))
                    except Exception:
                        pass
                if safe:
                    effects[code] = safe

        self.config["cloud_crafting_recipe_json"] = json.dumps(crafting_recipes, ensure_ascii=False)

        self.config["cloud_shop_catalog_json"] = json.dumps({
            str(code): [str(item.get("name") or "云端商品"), max(0, int(item.get("price", 0) or 0)), str(item.get("description") or item.get("intro") or "云端自定义商品")]
            for code, item in products.items() if isinstance(item, dict)
        }, ensure_ascii=False)
        self.config["cloud_shop_effects_json"] = json.dumps(effects, ensure_ascii=False)
        self.config["cloud_monster_catalog_json"] = json.dumps(monsters, ensure_ascii=False)
        self.config["cloud_tutorial_pages_json"] = json.dumps(tutorials, ensure_ascii=False)
        self.config["cloud_boss_catalog_json"] = json.dumps(bosses, ensure_ascii=False)
        if isinstance(data.get('global_boss_profile'), dict) and data.get('global_boss_profile'): self.config['global_boss_profile_json'] = json.dumps(data['global_boss_profile'], ensure_ascii=False)
        self.config['cloud_bounty_templates_json'] = json.dumps(data.get('bounty_templates',[]) if isinstance(data.get('bounty_templates'),list) else [], ensure_ascii=False)
        self.config["cloud_npc_catalog_json"] = json.dumps(npcs, ensure_ascii=False)

    def _cloud_monster_list(self) -> list[dict[str, Any]]:
        data = self._cloud_data.get("monsters", []) if isinstance(self._cloud_data, dict) else []
        return [dict(x) for x in data if isinstance(x, dict) and x.get("id") and x.get("name")] if isinstance(data, list) else []

    # ------------------------- helpers -------------------------

    @staticmethod
    def _group(event: AstrMessageEvent) -> str | None:
        try:
            return event.get_group_id()
        except Exception:
            return None

    @staticmethod
    def _user(event: AstrMessageEvent) -> str:
        return str(event.get_sender_id())

    @staticmethod
    def _name(event: AstrMessageEvent) -> str:
        return event.get_sender_name() or str(event.get_sender_id())

    def _args(self, event: AstrMessageEvent) -> list[str]:
        text = (event.message_str or "").strip()
        parts = text.split()
        return parts[1:] if parts else []

    def _csv_set(self, key: str) -> set[str]:
        raw = self.config.get(key, "")
        if isinstance(raw, list):
            return {str(x).strip() for x in raw if str(x).strip()}
        return {x.strip() for x in str(raw).split(",") if x.strip()}

    def _admin_level(self, event: AstrMessageEvent) -> int:
        # Level 3 is the plugin owner/super administrator and is deliberately
        # independent from the current session/group administrator role.
        uid = self._user(event)
        if uid in (self._csv_set("owner_user_ids") | self._csv_set("admin_user_ids")):
            return 3
        try:
            is_admin = bool(event.is_admin())
        except Exception:
            is_admin = False
        return 2 if is_admin else 0

    def _has_admin_action(self, event: AstrMessageEvent, action: str) -> bool:
        level = self._admin_level(event)
        if level >= 3:
            return True
        if level < 2 or not bool(self.config.get("session_admin_enabled", False)):
            return False
        allowed = self._csv_set("session_admin_allowed_actions")
        return action in allowed or "*" in allowed

    def _is_operator(self, event: AstrMessageEvent) -> bool:
        return self._admin_level(event) >= 2

    def _disabled_or_private(self, event: AstrMessageEvent) -> str | None:
        group_id = self._group(event)
        if not group_id:
            return "🌎 群聊世界目前只在群聊中运行。"
        if not self.engine.group_enabled(group_id):
            return "🚫 本群的群聊世界已被关闭。"
        return None

    @staticmethod
    def _target_id(event: AstrMessageEvent, args: list[str]) -> str | None:
        # Prefer an actual At component because QQ official messages may not
        # include a numeric @ID in message_str.
        try:
            for component in event.get_messages():
                if component.__class__.__name__.lower() == "at":
                    for attr in ("qq", "user_id", "target"):
                        value = getattr(component, attr, None)
                        if value is not None:
                            return str(value)
        except Exception:
            pass
        for arg in args:
            uid_match = re.search(r"\bGW-[A-Za-z0-9]{10}\b", arg, flags=re.I)
            if uid_match:
                return uid_match.group(0).upper()
            m = re.search(r"(?:@)?(\d{5,})", arg)
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _fmt_seconds(seconds: int) -> str:
        if seconds >= 3600:
            return f"{seconds // 3600}小时{(seconds % 3600) // 60}分钟"
        if seconds >= 60:
            return f"{seconds // 60}分钟"
        return f"{seconds}秒"

    async def _safe_reply(self, event: AstrMessageEvent, text: str):
        try:
            event.should_call_llm(False)
        except Exception:
            pass
        yield event.plain_result(text)

    def _proactive_footer(self) -> str:
        footer = str(self.config.get("proactive_message_footer", "🎮 想开始游玩吗？输入 /注册 开始游玩群聊世界。") or "").strip()
        return ("\n\n" + footer) if footer else ""

    def _mention_chain(self, user_id: str, text: str):
        if Comp is not None:
            try:
                return [Comp.At(qq=str(user_id)), Comp.Plain(text)]
            except Exception:
                pass
        return MessageChain().message(f"@{user_id} {text}")

    async def _broadcast(self, origin: str, text: str, *, proactive: bool = False, mention_user_id: str | None = None) -> bool:
        if not origin:
            return False
        payload = text
        if proactive:
            payload = payload.rstrip() + self._proactive_footer()
        try:
            chain = self._mention_chain(mention_user_id, payload) if mention_user_id else MessageChain().message(payload)
            await self.context.send_message(origin, chain)
            return True
        except Exception as exc:
            logger.warning("[群聊世界] 主动消息发送失败: %s", exc)
            return False

    @staticmethod
    def _fmt_bytes(value: int | float) -> str:
        size = float(max(0, value))
        units = ("B", "KB", "MB", "GB", "TB")
        for unit in units:
            if size < 1024 or unit == units[-1]:
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} TB"

    def _dir_size(self, path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def _count_files(self, path: Path) -> int:
        count = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    count += 1
        except OSError:
            pass
        return count

    def _system_metrics(self) -> dict[str, Any]:
        total = available = used = 0
        percent = 0.0
        cpu_percent = 0.0
        process_rss = 0
        process_cpu = 0.0
        if psutil is not None:
            try:
                mem = psutil.virtual_memory()
                total, available, used, percent = int(mem.total), int(mem.available), int(mem.used), float(mem.percent)
            except Exception:
                pass
            try:
                cpu_percent = float(psutil.cpu_percent(interval=0.0))
                proc = psutil.Process(os.getpid())
                process_rss = int(proc.memory_info().rss)
                process_cpu = float(proc.cpu_percent(interval=0.0))
            except Exception:
                pass
        if not total:
            try:
                pages = os.sysconf("SC_PHYS_PAGES"); page_size = os.sysconf("SC_PAGE_SIZE")
                total = int(pages * page_size)
                process_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024
                available = max(0, total - process_rss)
                used = total - available
                percent = (used / total * 100) if total else 0.0
            except Exception:
                pass
        if not total:
            try:
                meminfo={}
                with open("/proc/meminfo","r",encoding="utf-8") as fh:
                    for line in fh:
                        k, _, raw = line.partition(":")
                        if not _:
                            continue
                        raw_parts=raw.strip().split()
                        if not raw_parts:
                            continue
                        value=int(raw_parts[0])
                        if len(raw_parts)>1 and raw_parts[1].lower()=="kb":
                            value*=1024
                        meminfo[k]=value
                total=int(meminfo.get("MemTotal",0))
                available=int(meminfo.get("MemAvailable",meminfo.get("MemFree",0)))
                used=max(0,total-available)
                percent=(used/total*100) if total else 0.0
                if not process_rss:
                    process_rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024
            except Exception:
                pass
        if not cpu_percent:
            try:
                load = os.getloadavg()[0]
                cores = max(1, os.cpu_count() or 1)
                cpu_percent = min(100.0, max(0.0, load / cores * 100.0))
            except Exception:
                cpu_percent = 0.0
        trace_current = trace_peak = 0
        if self._memory_trace_enabled:
            try:
                trace_current, trace_peak = tracemalloc.get_traced_memory()
            except Exception:
                pass
        data_size = self._dir_size(self.data_dir)
        cache_size = self._dir_size(self.cache_dir)
        disk_free = 0
        try:
            disk_free = shutil.disk_usage(self.data_dir).free
        except Exception:
            pass
        return {
            "timestamp": int(time.time()),
            "server": {"memory_total": total, "memory_used": used, "memory_available": available, "memory_percent": round(percent, 1), "cpu_percent": round(cpu_percent, 1), "disk_free": disk_free},
            "process": {"pid": os.getpid(), "rss": process_rss, "cpu_percent": round(process_cpu, 1)},
            "plugin": {"tracemalloc_current": trace_current, "tracemalloc_peak": trace_peak, "data_size": data_size, "cache_size": cache_size, "cache_files": self._count_files(self.cache_dir) + (1 if self._cloud_cache_path.exists() else 0), "cache_dir": str(self.cache_dir)},
        }

    def _cleanup_cache(self, retention_days: int | None = None) -> dict[str, Any]:
        days = max(0, int(retention_days if retention_days is not None else self.config.get("maintenance_cache_retention_days", 7) or 7))
        cutoff = time.time() - days * 86400
        removed = 0; removed_bytes = 0
        targets: list[Path] = []
        if self.cache_dir.exists():
            targets.extend([x for x in self.cache_dir.rglob("*") if x.is_file()])
        # cloud-data.json is a rebuildable cache, so it belongs to cache cleanup.
        if self._cloud_cache_path.exists():
            targets.append(self._cloud_cache_path)
        seen = set()
        for item in targets:
            if item in seen:
                continue
            seen.add(item)
            try:
                if item.stat().st_mtime > cutoff and days > 0:
                    continue
                size = item.stat().st_size
                item.unlink()
                removed += 1; removed_bytes += size
            except OSError:
                continue
        # Remove empty cache directories after cleanup.
        try:
            for d in sorted([x for x in self.cache_dir.rglob("*") if x.is_dir()], reverse=True):
                try: d.rmdir()
                except OSError: pass
        except OSError:
            pass
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return {"removed": removed, "bytes": removed_bytes, "retention_days": days}

    async def _maybe_auto_cleanup(self) -> None:
        if not bool(self.config.get("maintenance_auto_cleanup", True)):
            return
        target = str(self.config.get("maintenance_cleanup_time", "04:30") or "04:30").strip()
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.strftime("%H:%M") != target or self._last_auto_cleanup_date == today:
            return
        result = self._cleanup_cache()
        self._last_auto_cleanup_date = today
        logger.info("[群聊世界] 自动清理缓存完成：删除 %s 个文件，释放 %s", result["removed"], self._fmt_bytes(result["bytes"]))

    async def _run_broadcast_oneoff(self, group_ids: list[str], message: str, group_interval_seconds: int, operator: str = "web") -> dict[str, list[str]]:
        sent, failed, missing = [], [], []
        for index, gid in enumerate(group_ids):
            group = self.db.get_group(gid)
            if not group or not group["session_origin"]:
                missing.append(gid)
            else:
                ok = await self._broadcast(str(group["session_origin"]), message, proactive=False)
                if ok:
                    sent.append(gid)
                    self.db.add_admin_log(gid, operator, "broadcast_immediate_send", None, message[:500])
                else:
                    failed.append(gid)
            if index < len(group_ids) - 1:
                await asyncio.sleep(max(1, int(group_interval_seconds)))
        return {"sent": sent, "failed": failed, "missing": missing}

    async def _run_broadcast_campaign(self, campaign_id: int) -> None:
        try:
            row = self.db.get_broadcast_campaign(campaign_id)
            if not row or not int(row["enabled"]):
                return
            targets = self.db.get_broadcast_campaign_targets(campaign_id)
            message = str(row["message"] or "").strip()
            if not message or not targets:
                return
            self.db.mark_broadcast_campaign_started(campaign_id, int(time.time()))
            sent = failed = 0
            gap = max(1, int(row["group_interval_seconds"] or 1))
            for idx, target in enumerate(targets):
                current = self.db.get_broadcast_campaign(campaign_id)
                if not current or not int(current["enabled"]):
                    return
                gid = str(target["group_id"] or "")
                origin = str(target["session_origin"] or "")
                if not origin or not int(target["group_enabled"] or 0):
                    failed += 1
                else:
                    ok = await self._broadcast(origin, message, proactive=False)
                    if ok:
                        sent += 1
                        self.db.add_admin_log(gid, "broadcast_campaign", "broadcast_loop_send", campaign_id, message[:500])
                    else:
                        failed += 1
                if idx < len(targets) - 1:
                    await asyncio.sleep(gap)
            next_at = int(time.time()) + max(60, int(row["loop_interval_minutes"] or 1) * 60)
            self.db.mark_broadcast_campaign_finished(campaign_id, next_at, sent, failed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[群聊世界] 循环群发任务 #%s 异常：%s", campaign_id, exc)
            row = self.db.get_broadcast_campaign(campaign_id)
            if row and int(row["enabled"]):
                self.db.mark_broadcast_campaign_finished(campaign_id, int(time.time()) + 60, 0, 1)
        finally:
            self._broadcast_campaign_tasks.pop(int(campaign_id), None)

    def _schedule_due_broadcast_campaigns(self, now_ts: int) -> None:
        for row in self.db.get_due_broadcast_campaigns(now_ts, 20):
            cid = int(row["id"])
            task = self._broadcast_campaign_tasks.get(cid)
            if task and not task.done():
                continue
            # Reserve the next run before starting work so a long cycle never gets scheduled twice.
            self.db.execute("UPDATE broadcast_campaigns SET next_run_at=?,updated_at=? WHERE id=? AND enabled=1", (int(now_ts) + 60, datetime.now(timezone.utc).isoformat(timespec="seconds"), cid))
            self._broadcast_campaign_tasks[cid] = asyncio.create_task(self._run_broadcast_campaign(cid))

    async def _scheduled_group_broadcasts(self, now_ts: int) -> None:
        self._schedule_due_broadcast_campaigns(now_ts)
        for row in self.db.get_due_group_broadcasts(now_ts, 30):
            interval = max(1, int(row["interval_minutes"] or 60))
            next_at = int(now_ts) + interval * 60
            origin = str(row["session_origin"] or "")
            if not origin:
                self.db.mark_group_broadcast_missed(row["group_id"], next_at)
                continue
            ok = await self._broadcast(origin, str(row["message"]), proactive=False)
            if ok:
                self.db.mark_group_broadcast_sent(row["group_id"], next_at)
            else:
                # Retry on the next scheduler pass instead of burning the whole interval.
                self.db.mark_group_broadcast_missed(row["group_id"], int(now_ts) + 60)

    async def _ai_npc_reply(self, event: AstrMessageEvent, base: str, *, story: bool = False) -> str:
        try:
            group_id=self._group(event)
            enabled=bool(self.config.get("ai_enabled",False)) and bool(self.config.get("ai_npc_dialogue_enabled",True))
            if story:
                enabled = enabled and bool(self.config.get("ai_story_enabled",True))
            if not enabled:
                return base
            provider_id=await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
            prompt=str(self.config.get("ai_npc_prompt","你是群聊世界中的 NPC。只做自然、简短、有角色感的互动，不修改数值、不承诺额外奖励。"))
            resp=await self.context.llm_generate(chat_provider_id=provider_id,prompt=prompt+"\n已执行的游戏结果："+base+"\n请给出一句到三句 NPC 对话，最多"+str(int(self.config.get("ai_max_reply_chars",240)))+"字。")
            extra=(getattr(resp,"completion_text","") or "").strip()
            return base + ("\n\n🧠 NPC："+extra if extra else "")
        except Exception as exc:
            logger.debug("[群聊世界] AI NPC 对话不可用，回退普通逻辑：%s", exc)
            return base

    # ------------------------- new world systems helpers -------------------------
    async def _broadcast_all_groups(self, message: str, *, mention_user_id: str | None = None) -> int:
        count = 0
        rows = self.db.fetchall("SELECT DISTINCT session_origin FROM groups WHERE enabled=1 AND session_origin IS NOT NULL AND session_origin!=''")
        seen=set()
        for row in rows:
            origin=str(row['session_origin'] or '').strip()
            if not origin or origin in seen: continue
            seen.add(origin)
            if await self._broadcast(origin,message,proactive=False,mention_user_id=mention_user_id): count += 1
        return count

    async def _tick_new_world_systems(self, now_ts: int) -> None:
        try:
            # Bounty challenge requests expire automatically.
            self.db.expire_bounties(now_ts)
        except Exception as exc:
            logger.debug('[群聊世界] 悬赏过期清理失败：%s', exc)
        try:
            b=self.db.get_global_boss()
            if int(b['active']):
                if int(b['hp'] or 0) <= 0:
                    text=self.engine.finish_global_boss('玩家完成最后一击')
                    if 'Boss 结束' in text:
                        await self._broadcast_all_groups(text)
                elif int(b['ends_at'] or 0) and now_ts >= int(b['ends_at']):
                    text=self.engine.finish_global_boss('挑战超时')
                    if 'Boss 结束' in text:
                        await self._broadcast_all_groups(text)
            elif bool(self.config.get('global_boss_enabled',True)) and bool(self.config.get('global_boss_auto_spawn',True)):
                last=int(b['last_finished_at'] or 0)
                interval=max(1,int(self.config.get('global_boss_interval_hours',24) or 24))*3600
                groups=self.db.fetchall("SELECT COUNT(*) AS c FROM groups WHERE enabled=1 AND session_origin IS NOT NULL")
                if int(groups[0]['c'] or 0)>0 and (last<=0 or now_ts-last>=interval):
                    text=self.engine.spawn_global_boss()
                    await self._broadcast_all_groups(text)
        except Exception as exc:
            logger.exception('[群聊世界] 大世界 Boss 定时处理失败：%s', exc)

    # ------------------------- automatic world loop -------------------------

    def _ensure_scheduler(self) -> None:
        if self._closed or (self._task and not self._task.done()):
            return
        try:
            self._task = asyncio.get_running_loop().create_task(self._scheduler_loop())
        except RuntimeError:
            self._task = None

    async def _scheduler_loop(self) -> None:
        await asyncio.sleep(5)
        while not self._closed:
            try:
                await self._scheduler_tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("[群聊世界] 定时任务异常：%s", exc)
            await asyncio.sleep(30)

    async def _scheduler_tick(self) -> None:
        if not self.config.get("enabled", True):
            return
        if bool(self.config.get("cloud_auto_sync", True)):
            await self._cloud_sync_if_due(force=False)
        await self._tick_new_world_systems(int(time.time()))
        await self._maybe_auto_cleanup()
        await self._scheduled_group_broadcasts(int(time.time()))
        # Finish countdown-based revivals even when the player does not send a message.
        for rp in self.db.get_due_respawns(int(time.time()),100):
            try:
                gid=str(rp["last_combat_group_id"] or "")
                hp=max(1,int(int(rp["max_hp"])*float(self.engine.cfg("respawn_hp_percent",gid,0.5))))
                self.db.set_global_player_fields(rp["user_id"],{"hp":hp,"death_state":0,"respawn_at":0,"revive_count":int(rp["revive_count"])+1,"last_combat_at":datetime.now(timezone.utc).isoformat(timespec="seconds")})
                g=self.db.get_group(gid) if gid else None
                if g and g["session_origin"]:
                    await self._broadcast(g["session_origin"],f"✨【自动复活】{rp['name']} 的复活倒计时结束，已恢复 {hp}/{rp['max_hp']} 战斗生命。",proactive=False)
            except Exception as exc:
                logger.debug("[群聊世界] 自动复活失败：%s",exc)

        # Retry cross-group duel action/result notifications that could not be
        # delivered on the first attempt. The battle row is re-resolved so the
        # current group session origin is used after reconnects / adapter changes.
        for notice in self.db.get_pending_duel_notifications(50):
            try:
                battle=self.db.get_duel(int(notice["battle_id"]))
                if not battle:
                    self.db.mark_duel_notification_delivered(int(notice["id"]))
                    continue
                recipient=str(notice["recipient_user_id"] or "")
                delivered=False
                for origin in self._duel_candidate_origins(battle, recipient, str(notice["group_id"] or "")):
                    if await self._broadcast(origin, str(notice["message"]), proactive=False, mention_user_id=recipient):
                        delivered=True
                        break
                if delivered:
                    self.db.mark_duel_notification_delivered(int(notice["id"]))
                else:
                    # Keep the notification for the next scheduler pass.
                    pass
            except Exception as exc:
                logger.debug("[群聊世界] 决斗通知重试失败：%s", exc)

        # Deliver cross-group duel messages to the opponent's current session.
        for msg in self.db.fetchall("SELECT * FROM duel_messages WHERE delivered=0 ORDER BY id ASC LIMIT 50"):
            try:
                battle=self.db.get_duel(int(msg["battle_id"]))
                if not battle: continue
                recipient=str(msg["recipient_user_id"])
                origin = battle["p1_origin"] if battle["p1_user_id"]==recipient else battle["p2_origin"]
                sender = battle["p1_name"] if battle["p1_user_id"]==msg["sender_user_id"] else battle["p2_name"]
                if await self._broadcast(origin, f"💬【决斗战后留言】{sender}：{msg['message']}", proactive=False):
                    self.db.mark_duel_messages_delivered(recipient,[int(msg["id"])])
            except Exception as exc:
                logger.debug("[群聊世界] 决斗留言投递失败：%s",exc)
        # Expired queued matches are cleaned by the DB query when matching occurs.
        # Auto combat is stateful and runs independently of incoming messages.
        auto_interval=max(3,int(self.config.get("auto_battle_interval_seconds",8)))
        for ab in self.db.get_auto_battles_due(int(time.time()), auto_interval):
            try:
                text=self.engine.auto_battle_tick(ab["group_id"],ab["user_id"])
                if text:
                    g=self.db.get_group(ab["group_id"])
                    if g and g["session_origin"]:
                        await self._broadcast(g["session_origin"],text,proactive=False)
            except Exception as exc:
                logger.warning("[群聊世界] 自动战斗失败 %s/%s: %s",ab["group_id"],ab["user_id"],exc)
        groups = self.db.fetchall(
            "SELECT * FROM groups WHERE enabled=1 AND session_origin IS NOT NULL ORDER BY updated_at DESC"
        )
        now = time.time()
        cooldown = max(30, int(self.config.get("proactive_broadcast_cooldown_minutes", 15)) * 60)
        # One global proactive message per cooldown window. We still inspect every group
        # for due work, but only choose one target/event to broadcast.
        if now - self._last_proactive_broadcast_at < cooldown:
            return

        candidates: list[dict[str, Any]] = []
        for group in groups:
            group_id = group["group_id"]
            if not self.engine.group_enabled(group_id):
                continue
            origin = group["session_origin"]
            active = int(group["today_active_users"] or 0)
            # Expired Bosses are urgent. Queue them rather than immediately broadcasting
            # so multiple groups never speak at once.
            if group["boss_active"] and group["boss_ends_at"]:
                try:
                    end_value = float(group["boss_ends_at"])
                    if now > end_value:
                        candidates.append({"priority": 3, "kind": "boss_timeout", "group": group_id, "origin": origin})
                        continue
                except Exception:
                    pass

            if not group["last_event_at"]:
                self.db.update_group(group_id, last_event_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            else:
                try:
                    last_event = datetime.fromisoformat(group["last_event_at"]).timestamp()
                    interval = int(self.engine.cfg("world_event_interval_minutes", group_id, 90)) * 60
                    chance = int(self.engine.cfg("world_event_chance", group_id, 60))
                    if (group["world_event_enabled"] if "world_event_enabled" in group.keys() else 1) and bool(self.engine.cfg("enable_auto_world_events", group_id, True)) and active > 0 and now - last_event >= interval and secrets.randbelow(100) < max(0, min(100, chance)):
                        candidates.append({"priority": 2, "kind": "world_event", "group": group_id, "origin": origin})
                except Exception:
                    pass

            try:
                if self.engine.group_feature_enabled(group_id,"npc_enabled","npc_enabled",True):
                    last_npc = group["last_npc_at"] if "last_npc_at" in group.keys() else None
                    npc_interval = int(group["npc_interval_minutes"] if group["npc_interval_minutes"] is not None else self.engine.cfg("npc_interval_minutes", group_id, 120)) * 60
                    npc_chance = int(group["npc_chance_percent"] if group["npc_chance_percent"] is not None else self.engine.cfg("npc_chance_percent", group_id, 10))
                    if (not last_npc or now - datetime.fromisoformat(last_npc).timestamp() >= npc_interval) and active > 0 and secrets.randbelow(100) < max(0,min(100,npc_chance)):
                        candidates.append({"priority": 1, "kind": "npc", "group": group_id, "origin": origin})
            except Exception:
                pass

            try:
                if bool(self.config.get("proactive_tips_enabled", True)):
                    last_tip = group["last_tip_at"] if "last_tip_at" in group.keys() else None
                    tip_interval = int(self.engine.cfg("proactive_tip_interval_minutes", group_id, 180)) * 60
                    if (not last_tip) or now - datetime.fromisoformat(last_tip).timestamp() >= tip_interval:
                        candidates.append({"priority": 1, "kind": "tip", "group": group_id, "origin": origin})
            except Exception:
                pass

            if bool(self.config.get("enable_auto_boss", True)) and bool(self.engine.cfg("boss_enabled", group_id, True)) and active >= 2 and not group["boss_active"]:
                if group["last_boss_at"]:
                    try:
                        last_boss = datetime.fromisoformat(group["last_boss_at"]).timestamp()
                        interval_hours = int(self.engine.cfg("boss_interval_hours", group_id, 12))
                        if now - last_boss >= interval_hours * 3600:
                            candidates.append({"priority": 2, "kind": "auto_boss", "group": group_id, "origin": origin})
                    except Exception:
                        pass

        if not candidates:
            return

        # Pick the highest priority tier, then randomly pick one group within that tier.
        top_priority = max(c["priority"] for c in candidates)
        top = [c for c in candidates if c["priority"] == top_priority]
        chosen = secrets.choice(top)
        group_id = chosen["group"]
        origin = chosen["origin"]
        kind = chosen["kind"]

        try:
            text = ""
            if kind == "boss_timeout":
                text = self.engine.finish_boss(group_id) + "\n\n⏰ Boss 已因超时结束。"
            elif kind == "world_event":
                text = self.engine.random_world_event(group_id)
            elif kind == "tip":
                text = self.engine.random_tip(group_id)
            elif kind == "npc":
                text = self.engine.spawn_npc(group_id)
            elif kind == "auto_boss":
                text = self.engine.spawn_boss(group_id)
            if text:
                sent = await self._broadcast(origin, text, proactive=True)
                if sent:
                    self._last_proactive_broadcast_at = time.time()
                    if kind == "tip":
                        self.db.update_group(group_id, last_tip_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    elif kind == "npc":
                        self.db.update_group(group_id, last_npc_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                    elif kind in {"world_event"}:
                        self.db.update_group(group_id, last_event_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except Exception as exc:
            logger.exception("[群聊世界] 主动消息处理失败：%s", exc)

    # ------------------------- event listener -------------------------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """Collect lightweight activity statistics for ranking/dashboard."""
        group_id = self._group(event)
        if not group_id:
            return
        if not self.engine.group_enabled(group_id):
            return
        try:
            # 收件人下一条消息也会触发一次兜底投递：即使 QQ/适配器在跨群主动
            # 推送瞬间拒绝了旧会话地址，玩家也不会永远错过“匹配成功/被攻击”提示。
            self.db.upsert_group(group_id, event.unified_msg_origin)
            recipient=self._user(event)
            # duel_notifications 使用独立 outbox；这里直接按当前事件 origin 投递。
            for notice in self.db.get_pending_duel_notifications(50):
                if str(notice["recipient_user_id"]) != recipient:
                    continue
                battle=self.db.get_duel(int(notice["battle_id"]))
                if battle and await self._broadcast(str(event.unified_msg_origin or ""), str(notice["message"]), proactive=False, mention_user_id=recipient):
                    self.db.mark_duel_notification_delivered(int(notice["id"]))
        except Exception as exc:
            logger.debug("[群聊世界] 决斗收件箱兜底投递失败：%s", exc)
        try:
            self._ensure_scheduler()
            self.db.upsert_group(group_id, event.unified_msg_origin)
            self.db.touch_message(group_id, self._user(event), event.unified_msg_origin)
        except Exception as exc:
            logger.warning("[群聊世界] 统计记录失败：%s", exc)

    # ------------------------- player commands -------------------------

    @filter.command("群聊世界状态", alias={"世界状态", "服务器状态"})
    async def group_world_status(self, event: AstrMessageEvent):
        """查看服务器、进程、插件内存与 CPU 状态，并优先使用 AstrBot 文转图发送。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        m = self._system_metrics()
        s, p, ext = m["server"], m["process"], m["plugin"]
        now = datetime.fromtimestamp(m["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        text = (
            "🖥️【群聊世界 · 服务器状态】\n"
            f"更新时间：{now}\n\n"
            f"🧠 服务器内存：{self._fmt_bytes(s['memory_used'])} / {self._fmt_bytes(s['memory_total'])}（{s['memory_percent']:.1f}%）\n"
            f"🟢 可用内存：{self._fmt_bytes(s['memory_available'])}\n"
            f"⚙️ CPU：{s['cpu_percent']:.1f}%\n"
            f"💽 磁盘可用：{self._fmt_bytes(s['disk_free'])}\n\n"
            f"📦 插件进程内存：{self._fmt_bytes(p['rss'])}\n"
            f"📊 插件追踪分配：{self._fmt_bytes(ext['tracemalloc_current'])}（峰值 {self._fmt_bytes(ext['tracemalloc_peak'])}）\n"
            f"🗃️ 插件数据目录：{self._fmt_bytes(ext['data_size'])}\n"
            f"🧹 缓存目录：{self._fmt_bytes(ext['cache_size'])}\n\n"
            "说明：插件追踪分配为 tracemalloc 统计，并不等同于整个进程 RSS。"
        )
        try:
            url = await self.text_to_image(text)
            yield event.image_result(url)
        except Exception as exc:
            logger.debug("[群聊世界] 世界状态文转图失败，回退文字：%s", exc)
            yield event.plain_result(text)

    @filter.command("世界", alias={"world", "群聊世界"})
    async def world(self, event: AstrMessageEvent):
        """查看本群的世界状态和入口。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        group_id = self._group(event)
        self.db.upsert_group(group_id, event.unified_msg_origin)
        yield event.plain_result(self.engine.world_status(group_id))

    @filter.command("地图", alias={"世界地图", "大世界地图"})
    async def world_map(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.world_map())

    @filter.command("声望", alias={"世界声望", "renown"})
    async def renown(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked); return
        gid=self._group(event); uid=self._user(event); name=self._name(event)
        yield event.plain_result(self.engine.player_world_status(uid,name,gid))

    @filter.command("世界位置", alias={"位置", "世界坐标"})
    async def world_position(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked); return
        gid=self._group(event); uid=self._user(event); name=self._name(event)
        yield event.plain_result(self.engine.player_world_status(uid,name,gid))

    @filter.command("旅行", alias={"前往", "worldtravel"})
    async def world_travel(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked); return
        args=self._args(event); gid=self._group(event)
        if not args:
            yield event.plain_result("用法：`/旅行 地区名`\n先输入 `/地图` 查看地区、危险等级和旅行费用。"); return
        yield event.plain_result(self.engine.travel(self._user(event),self._name(event),gid," ".join(args)))

    @filter.command("世界设置", alias={"worldsettings", "世界配置"})
    async def world_settings(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        gid=self._group(event)
        if not gid: yield event.plain_result("❌ 世界设置只能在群聊中使用。"); return
        self.db.upsert_group(gid,event.unified_msg_origin)
        args=self._args(event)
        if not args:
            yield event.plain_result(self.engine.world_settings_text(gid)); return
        if not self._has_admin_action(event,"world_settings"):
            yield event.plain_result("🚫 你没有世界设置权限。"); return
        sub=args[0]
        regions=WORLD_REGIONS
        if sub in {'地区','区域'} and len(args)>=2:
            target=" ".join(args[1:]).strip()
            rid=target if target in regions else next((k for k,v in regions.items() if v['name']==target),None)
            if not rid: yield event.plain_result("❌ 地区不存在。输入 `/地图` 查看地区。"); return
            self.db.update_group(gid,world_region_id=rid,world_location=regions[rid]['name'])
            yield event.plain_result(f"✅ 已将本群世界地区设置为【{regions[rid]['name']}】（危险等级 {regions[rid]['tier']}）。"); return
        if sub in {'阵营','faction'} and len(args)>=2:
            faction=" ".join(args[1:]).strip(); allowed={'王国','联盟','深渊','自然','中立'}
            if faction not in allowed: yield event.plain_result("❌ 阵营只能是：王国、联盟、深渊、自然、中立。"); return
            self.db.update_group(gid,world_faction=faction); yield event.plain_result(f"✅ 本群阵营已设置为【{faction}】。"); return
        if sub in {'天气','weather'} and len(args)>=2:
            weather=" ".join(args[1:]).strip()[:40]; self.db.update_group(gid,world_weather=weather); yield event.plain_result(f"✅ 本群天气已设置为【{weather}】。"); return
        if sub in {'事件','event'} and len(args)>=2:
            on=args[1] in {'开启','开','on','1','true'}
            self.db.update_group(gid,world_event_enabled=1 if on else 0); yield event.plain_result(f"✅ 世界事件已{'开启' if on else '关闭'}。"); return
        if sub in {'重置','reset'}:
            self.db.update_group(gid,world_region_id='starter',world_location='新手村',world_faction='中立',world_weather='晴朗',world_event_enabled=1)
            yield event.plain_result("✅ 本群世界设置已恢复为【新手村 / 中立 / 晴朗 / 世界事件开启】。"); return
        yield event.plain_result(self.engine.world_settings_text(gid))

    @filter.command("帮助", alias={"世界帮助", "worldhelp", "菜单", "世界菜单", "menu"})
    async def help(self, event: AstrMessageEvent):
        """查看群聊世界完整命令。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        text = (
            "📖 【群聊世界命令】\n\n"
            "👤 玩家：/注册 [邀请码] /我的 /签到 /邀请码 /教程 /继续教程 /跳过教程 /地图 /声望 /世界位置\n"
            "🗺️ 冒险：/探索 /探索 深度 /探索 危险 /钓鱼 /挖矿 /打工 /怪物\n"
            "✨ 战斗：/技能 /技能学习 /技能装备 /技能卸下 /攻击怪物 /技能使用 /自动战斗 /逃跑 /决斗匹配 /决斗状态 /决斗攻击 /决斗技能 /决斗防御 /战后留言 /复活状态 /群聊世界状态\n"
            "🎒 物品：/背包 /商店 /购买 ID 数量 /使用 ID 数量 /装备 /穿戴 ID /强化 ID /合成列表 /合成 ID /自动合成 /拆解 ID\n"
            "🐾 宠物：/宠物 /抽宠物 /出战宠物 ID\n"
            "⭐ 成长：/职业 /转职 职业 /任务 /任务领取 /成就\n"
            "🏆 排行：/排行榜\n"
            "🎮 游戏：/游戏 /猜数字 /猜 N /猜拳 石头 /骰子 /炸弹 /抽炸弹\n"
            "🐉 Boss：/Boss /攻击\n"
            "🎯 悬赏：/悬赏 /悬赏 规则 /悬赏 发布 GW-UID 金币 内容 /悬赏 我的 /悬赏 详情 ID /悬赏 领取 ID /悬赏 接受 ID /悬赏 拒绝 ID /悬赏 取消 ID\n"
            "🌍 大世界 Boss：/大世界Boss /大世界Boss 攻击 /大世界Boss 技能 技能名 /大世界Boss 排行\n"
            "💸 社交：/转账 用户ID 金额\n\n"
            "管理员：/世界管理"
        )
        self._refresh_help_menu_config_from_disk()
        image_enabled = self._config_bool(self._help_menu_value("image_enabled", True), True)
        send_text = self._config_bool(self._help_menu_value("text_enabled", True), True)
        send_image_first = self._config_bool(self._help_menu_value("image_first", True), True)
        image_path = self._menu_image_path() if image_enabled else None
        send_image = bool(image_path)

        async def send_text_now() -> None:
            # 直接走 event.send()，这样适配器的真实发送异常能够被捕获。
            await event.send(MessageChain().message(text))

        async def send_image_now(path: Path) -> None:
            if Comp is None or not hasattr(Comp, "Image"):
                raise RuntimeError("当前 AstrBot 版本缺少 Image 消息组件。")
            chain = MessageChain([Comp.Image.fromFileSystem(str(path))])
            await event.send(chain)

        if send_image and send_image_first:
            try:
                await send_image_now(image_path)
            except Exception as exc:
                logger.warning("[群聊世界] 菜单图片真实发送失败，自动文字兜底：%s", exc)
                # 只有开启“发送文字帮助”时才启用文字兜底；关闭后不发送任何文字。
                if send_text:
                    try:
                        await send_text_now()
                    except Exception as text_exc:
                        logger.error("[群聊世界] 菜单图片失败后的文字兜底也失败：%s", text_exc)
                return
            if send_text:
                try:
                    await send_text_now()
                except Exception as exc:
                    logger.warning("[群聊世界] 菜单文字发送失败：%s", exc)
            return

        if send_text:
            try:
                await send_text_now()
            except Exception as exc:
                logger.warning("[群聊世界] 菜单文字发送失败：%s", exc)

        if send_image and not send_image_first:
            try:
                await send_image_now(image_path)
            except Exception as exc:
                logger.warning("[群聊世界] 菜单图片真实发送失败：%s", exc)
        return
        if False:
            yield event.plain_result("")

    @filter.command("注册")
    async def register(self, event: AstrMessageEvent):
        """注册成为群聊世界玩家，可在注册时填写邀请码。"""
        group_id=self._group(event)
        if not group_id:
            yield event.plain_result("🌎 请在群聊中注册。")
            return
        if not self.engine.group_enabled(group_id):
            yield event.plain_result("🚫 本群的群聊世界已关闭。")
            return
        args=self._args(event)
        invite_code=args[0] if args else ""
        player,created=self.engine.ensure_player(group_id,self._user(event),self._name(event))
        if not created:
            yield event.plain_result("👤 你已经注册过了，发送 `/我的` 查看角色。\n\n🎁 想查看自己的邀请码：`/邀请码`")
            return
        invite_msg=""
        if invite_code:
            ok,msg=self.engine.apply_invite(invite_code,self._user(event))
            invite_msg="\n\n"+(msg if ok else f"⚠️ 邀请码未使用成功：{msg}")
        code=self.db.ensure_invite_code(self._user(event),int(self.config.get("invite_code_length",6)))
        yield event.plain_result(f"🎉 注册成功！\n欢迎 {player['name']} 来到群聊世界。\n\n🎁 新人礼包：💰 {player['coins']} 金币 + 💎 {player['gems']} 钻石\n🛡️ 新人保护已经开启。\n🎁 你的邀请码：`{code}`{invite_msg}\n\n推荐：/签到 /任务 /探索 /技能 /背包")
        if bool(self.config.get("tutorial_enabled",True)) and bool(self.config.get("tutorial_auto_start",True)):
            yield event.plain_result(self.engine.tutorial_begin(group_id,self._user(event),self._name(event)))

    @filter.command("邀请码", alias={"邀请", "我的邀请码"})
    async def invite_code(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.invite_info(self._user(event),self._name(event)))

    @filter.command("教程", alias={"新手教程", "世界教程"})
    async def tutorial(self, event: AstrMessageEvent):
        """查看或重新进入新手教程。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        state = self.engine.tutorial_state(self._group(event), self._user(event), self._name(event))
        if state["status"] in {"completed", "skipped"}:
            yield event.plain_result("📖【新手教程】\n\n教程已结束。输入 `/教程 重开` 可以从第一页重新开始。")
            return
        args = self._args(event)
        if args and args[0] in {"重开", "重新"}:
            yield event.plain_result(self.engine.tutorial_begin(self._group(event), self._user(event), self._name(event)))
            return
        step = max(1, state["step"] or 1)
        yield event.plain_result(self.engine.tutorial_page(step))

    @filter.command("继续教程", alias={"教程继续"})
    async def tutorial_continue(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        result = self.engine.tutorial_continue(self._group(event), self._user(event), self._name(event))
        yield event.plain_result(result.text)

    @filter.command("跳过教程", alias={"跳过新手教程"})
    async def tutorial_skip(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        if not bool(self.config.get("tutorial_allow_skip", True)):
            yield event.plain_result("📖 管理员关闭了跳过教程选项，请继续完成教程。")
            return
        yield event.plain_result(self.engine.tutorial_skip(self._group(event), self._user(event), self._name(event)))

    @filter.command("我的", alias={"个人资料", "profile"})
    async def profile(self, event: AstrMessageEvent):
        """查看玩家资料。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.profile(self._group(event), self._user(event), self._name(event)).text)

    @filter.command("签到", alias={"checkin"})
    async def checkin(self, event: AstrMessageEvent):
        """每日签到。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.checkin(self._group(event), self._user(event), self._name(event)).text)

    @filter.command("探索", alias={"探险", "explore"})
    async def explore(self, event: AstrMessageEvent):
        """进行一次探索，可选 normal/deep/danger。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        mode_map = {"深度": "deep", "危险": "danger", "deep": "deep", "danger": "danger"}
        mode = mode_map.get(args[0], "normal") if args else "normal"
        result = self.engine.explore(self._group(event), self._user(event), self._name(event), mode)
        yield event.plain_result(result.text)

    @filter.command("技能", alias={"技能列表", "skill"})
    async def skills(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.skill_list(self._user(event), self._name(event)))

    @filter.command("技能学习")
    async def skill_learn(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("📘 用法：`/技能学习 技能名`\n输入 `/技能` 查看可学习技能。") ; return
        yield event.plain_result(self.engine.learn_skill(self._user(event), args[0], self._name(event)))

    @filter.command("技能装备")
    async def skill_equip(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("🧩 用法：`/技能装备 技能名`，最多装备 4 个技能。"); return
        yield event.plain_result(self.engine.equip_skill(self._user(event), args[0], self._name(event)))

    @filter.command("技能卸下")
    async def skill_unequip(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("🧩 用法：`/技能卸下 技能名`"); return
        yield event.plain_result(self.engine.unequip_skill(self._user(event), args[0], self._name(event)))

    @filter.command("怪物", alias={"怪物状态", "monster"})
    async def monster(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.monster_status(self._group(event), self._user(event), self._name(event)))

    @filter.command("攻击怪物", alias={"打怪", "attackmonster"})
    async def attack_monster(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event); skill=args[0] if args else "普攻"
        result=self.engine._monster_hit(self._group(event), self._user(event), self._name(event), skill)
        yield event.plain_result(result.text)

    @filter.command("技能使用")
    async def use_skill(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        group=self._group(event); uid=self._user(event); name=self._name(event)
        if args:
            skill_id=args[0]
        else:
            player=self.db.get_player("__GLOBAL_USER__",uid)
            if not player:
                self.engine.ensure_player(group,uid,name)
                player=self.db.get_player("__GLOBAL_USER__",uid)
            skill_id=self.engine._pick_duel_skill(uid, player=player) or "普攻"
        monster=self.db.get_active_monster(group,uid)
        if monster:
            result=self.engine._monster_hit(group,uid,name,skill_id)
        else:
            result=self.engine.boss_use_skill(group,uid,name,skill_id)
        yield event.plain_result(result.text)

    @filter.command("战斗", alias={"战斗中心","战斗状态","combat"})
    async def combat_center(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        uid=self._user(event); gid=self._group(event); name=self._name(event)
        p,_=self.engine.ensure_player(gid,uid,name)
        eq=self.db.get_equipped_stats("__GLOBAL_USER__",uid); pet=self.db.get_active_pet("__GLOBAL_USER__",uid)
        auto=self.db.get_auto_battle(gid,uid); duel=self.db.get_active_duel_for_user(uid); queue=self.db.get_duel_queue_user(uid)
        lines=["⚔️【玩家战斗中心】",f"UID：{p['player_uid']}｜QQ/平台ID：{p['user_id']}",f"❤️ 战斗生命：{p['hp']}/{p['max_hp']}｜⚡体力：{p['stamina']}/{p['max_stamina']}",f"⚔️ 攻击：{p['battle_attack']+eq.get('attack',0)+(int(pet['attack'])*2 if pet else 0)}｜🛡️ 防御：{p['battle_defense']+eq.get('defense',0)+(int(pet['defense']) if pet else 0)}｜💥暴击：{p['battle_crit_rate']}%｜💨闪避：{p['battle_dodge_rate']}%｜⚡速度：{p['battle_speed']}",f"🏆 PVP：{p['pvp_rating']}｜胜 {p['battle_wins']} / 负 {p['battle_losses']} / 平 {p['battle_draws']}｜击杀 {p['battle_kills']} / 死亡 {p['battle_deaths']}"]
        if p['death_state']:
            left=max(0,int(p['respawn_at'])-int(time.time())); lines.append(f"💀 当前：死亡，自动复活剩余 {self._fmt_seconds(left)}")
        else: lines.append("✅ 当前：存活")
        if auto: lines.append(f"🤖 自动战斗：开启｜目标：{'Boss' if auto['target_type']=='boss' else '当前怪物'}｜已执行 {auto['turns']} 回合")
        if duel: lines.append(f"⚔️ 决斗：#{duel['id']}｜{'你的回合' if duel['turn_user_id']==uid else '等待对手'}｜对手 {duel['p2_name'] if duel['p1_user_id']==uid else duel['p1_name']}")
        elif queue: lines.append(f"🔎 匹配：排队中｜PVP {queue['rating']}｜剩余 {self._fmt_seconds(max(0,int(queue['expires_at'])-int(time.time())))}")
        lines.append("\n指令：/自动战斗 开启｜/自动战斗 Boss｜/自动战斗 关闭｜/决斗 匹配｜/决斗 状态｜/复活状态")
        yield event.plain_result("\n".join(lines))

    def _duel_candidate_origins(self, b, recipient_user_id: str, preferred_group_id: str = "") -> list[str]:
        """按优先级返回对手当前可用的会话地址。

        决斗消息不能只依赖匹配时保存的 unified_msg_origin：QQ/适配器重连、群会话
        首次建立顺序变化时都可能导致旧 origin 失效。这里同时尝试当前群记录、战斗
        记录和事件传入的当前 origin，并去重。
        """
        if not b:
            return []
        uid = str(recipient_user_id)
        side = "p1" if str(b["p1_user_id"]) == uid else "p2"
        group_id = str(preferred_group_id or b[side + "_group_id"] or "")
        origins=[]
        if group_id:
            try:
                group=self.db.get_group(group_id)
                current=str(group["session_origin"] or "") if group else ""
                if current: origins.append(current)
            except Exception:
                pass
        stored=str(b[side + "_origin"] or "")
        if stored: origins.append(stored)
        out=[]
        seen=set()
        for x in origins:
            x=str(x).strip()
            if x and x not in seen:
                seen.add(x); out.append(x)
        return out

    async def _deliver_duel_notification(self, b, recipient_user_id: str, message: str, *, preferred_group_id: str = "", dedupe_key: str = "") -> bool:
        """先入站队列，再即时投递；即时失败由调度器和收件人下次发言兜底。"""
        if not b or not recipient_user_id:
            return False
        recipient=str(recipient_user_id)
        group_id=str(preferred_group_id or (b["p1_group_id"] if str(b["p1_user_id"])==recipient else b["p2_group_id"]))
        key=dedupe_key or f"notice:{b['id']}:{recipient}:{hash(message)}"
        self.db.enqueue_duel_notification(int(b["id"]), recipient, group_id, message, recipient, key)
        for origin in self._duel_candidate_origins(b, recipient, group_id):
            if await self._broadcast(origin, message, proactive=False, mention_user_id=recipient):
                # Queue remains until marked, so restart/retry is safe.
                pending=self.db.fetchone("SELECT id FROM duel_notifications WHERE dedupe_key=? LIMIT 1", (key,))
                if pending:
                    self.db.mark_duel_notification_delivered(int(pending["id"]))
                return True
        return False

    def _duel_target_origin(self, b, recipient_user_id: str) -> str:
        """Resolve the opponent's current group session instead of trusting a stale battle origin."""
        if not b:
            return ""
        recipient_user_id = str(recipient_user_id)
        side = "p1" if str(b["p1_user_id"]) == recipient_user_id else "p2"
        group_id = str(b[side + "_group_id"] or "")
        if group_id:
            try:
                group = self.db.get_group(group_id)
                current = str(group["session_origin"] or "") if group else ""
                if current:
                    return current
            except Exception:
                pass
        return str(b[side + "_origin"] or "")

    def _refresh_duel_actor_origin(self, b, user_id: str, group_id: str | None, origin: str):
        if not b or not origin:
            return b
        uid = str(user_id)
        fields = {}
        if str(b["p1_user_id"]) == uid:
            fields["p1_origin"] = str(origin); fields["p1_group_id"] = str(group_id or b["p1_group_id"] or "")
        elif str(b["p2_user_id"]) == uid:
            fields["p2_origin"] = str(origin); fields["p2_group_id"] = str(group_id or b["p2_group_id"] or "")
        if fields:
            try:
                self.db.update_duel(int(b["id"]), **fields)
                return self.db.get_duel(int(b["id"])) or b
            except Exception:
                return b
        return b

    def _duel_origin_pair(self, b, user_id):
        if not b:
            return "", "", ""
        me = "p1" if str(b["p1_user_id"]) == str(user_id) else "p2"
        op = "p2" if me == "p1" else "p1"
        target_user = str(b[op+"_user_id"] or "")
        return self._duel_target_origin(b, target_user), target_user, str(b[op+"_name"] or "对手")

    def _duel_notice_for_user(self, b, user_id: str, actor_user_id: str, text: str) -> str:
        """将一次决斗行动转换成收件人视角，并明确告诉下一位行动者该做什么。"""
        if not b:
            return text
        user_id = str(user_id)
        actor_user_id = str(actor_user_id)
        if str(b["state"]) == "finished":
            return self._duel_result_for_user(b, user_id, text)
        me = "p1" if b["p1_user_id"] == user_id else "p2"
        op = "p2" if me == "p1" else "p1"
        lines = [f"🔔【决斗 #{b['id']}】对手【{b[op+'_name']}】刚刚行动。", text, "", f"你的生命：❤️ {b[me+'_hp']}/{b[me+'_max_hp']} · ⚡ {b[me+'_stamina']}/100", f"对手生命：❤️ {b[op+'_hp']}/{b[op+'_max_hp']}"]
        if str(b["turn_user_id"] or "") == user_id:
            lines += ["", "👉 现在轮到你行动。", self.engine._duel_action_help(user_id)]
        else:
            lines += ["", f"⏳ 现在等待【{b[b['turn_user_id']+'_name']}】行动。"]
        return "\n".join(lines)

    def _duel_mention_text(self, user_id: str, text: str):
        return self._mention_chain(str(user_id), text)

    def _duel_pair_side(self, b, user_id: str):
        me = "p1" if b["p1_user_id"] == str(user_id) else "p2"
        return me, ("p2" if me == "p1" else "p1")

    def _duel_result_for_user(self, b, user_id, text):
        """把决斗结果转成当前玩家视角，避免胜者文案被原样转给败者。"""
        if not b:
            return text
        if str(b.get("state") if hasattr(b, "get") else b["state"]) != "finished":
            return text
        winner = str(b["winner_user_id"] or "")
        loser = str(b["loser_user_id"] or "")
        detail = text.split("\n", 1)[1] if "\n" in text else text
        if str(user_id) == winner:
            return "🏆【决斗结束】你获胜！\n" + detail
        if str(user_id) == loser:
            death_detail = ""
            try:
                raw_result = b["result_json"] or "{}"
                payload = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
                death_detail = str(payload.get("loser_death_text") or "").strip()
            except Exception:
                death_detail = ""
            return "💀【决斗结束】你已落败。\n" + detail + (("\n" + death_detail) if death_detail and death_detail not in detail else "")
        return text

    def _name_from_battle(self, b, user_id: str) -> str:
        return str(b["p1_name"] if str(b["p1_user_id"]) == str(user_id) else b["p2_name"])

    @filter.command("决斗", alias={"跨群决斗","pvp"})
    async def duel(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event); action=(args[0] if args else "匹配")
        g=self._group(event); u=self._user(event); n=self._name(event)
        if action in {"状态","status"}: yield event.plain_result(self.engine.duel_status(u)); return
        if action in {"取消","退出"}:
            self.db.clear_duel_queue_user(u); yield event.plain_result("🛑 已退出决斗匹配队列。"); return
        if action in {"匹配","开始","join"}:
            origin=getattr(event,"unified_msg_origin","") or (self.db.get_group(g)["session_origin"] if g and self.db.get_group(g) else "")
            if g and origin:
                self.db.upsert_group(g, origin)
            async with self._duel_lock:
                text=self.engine.duel_join(g,u,n,origin)
                b=self.db.get_active_duel_for_user(u)
            if b:
                opponent_id = str(b["p2_user_id"] if b["p1_user_id"]==u else b["p1_user_id"])
                opponent_name = str(b["p2_name"] if b["p1_user_id"]==u else b["p1_name"])
                first = str(b["p1_name"] if b["turn_user_id"]==b["p1_user_id"] else b["p2_name"])
                own = f"⚔️【跨群决斗 #{b['id']}】匹配成功！\n你已匹配到【{opponent_name}】。\n\n🔥 {first} 先手。\n{self.engine._duel_action_help(u) if str(b['turn_user_id'])==u else '⏳ 等待对手行动。'}"
                # 跨群主动通知必须在 yield 当前事件回复之前执行；兼容只消费首个 yield 的调度器。
                other_uid = str(b['p2_user_id'] if b['p1_user_id']==u else b['p1_user_id'])
                if other_uid:
                    other_group = str(b['p2_group_id'] if str(b['p2_user_id'])==other_uid else b['p1_group_id'])
                    other_text = f"⚔️【跨群决斗 #{b['id']}】匹配成功！\n你已匹配到【{self._name_from_battle(b, other_uid)}】。\n\n🔥 {first} 先手。\n{self.engine._duel_action_help(other_uid) if str(b['turn_user_id'])==other_uid else '⏳ 等待对手行动。'}"
                    await self._deliver_duel_notification(b, other_uid, other_text, preferred_group_id=other_group, dedupe_key=f"match:{b['id']}:{other_uid}")
                yield event.chain_result(self._mention_chain(u, own))
                return
            yield event.plain_result(text)
            return
        # Shortcuts: /决斗 攻击 /决斗 防御 /决斗 技能 [技能名]
        if action in {"攻击","attack"}:
            action="攻击"
        elif action in {"防御","guard"}:
            action="防御"
        elif action in {"技能","skill"}:
            action="技能"
        else:
            yield event.plain_result("用法：/决斗 匹配｜/决斗 状态｜/决斗 取消｜/决斗 攻击｜/决斗 技能 [技能名]｜/决斗 防御")
            return
        async with self._duel_lock:
            skill=args[1] if action=="技能" and len(args)>1 else ""
            kind={"攻击":"attack","技能":"skill","防御":"guard"}[action]
            text,b=self.engine.duel_action(u,kind,skill)
            if b:
                b=self._refresh_duel_actor_origin(b,u,g,getattr(event,"unified_msg_origin","") or "")
        personal=self._duel_result_for_user(b,u,text)
        if b:
            other_uid = str(b["p2_user_id"] if str(b["p1_user_id"])==u else b["p1_user_id"])
            if other_uid:
                other_text=self._duel_notice_for_user(b,other_uid,u,text)
                target_group=str(b["p1_group_id"] if str(b["p1_user_id"])==other_uid else b["p2_group_id"])
                await self._deliver_duel_notification(b, other_uid, other_text, preferred_group_id=target_group, dedupe_key=f"action:{b['id']}:{other_uid}:round:{b['round_no']}:turn:{b['turn_user_id']}")
        yield event.chain_result(self._mention_chain(u, personal))

    @filter.command("决斗攻击", alias={"pvp攻击"})
    async def duel_attack(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        u=self._user(event); g=self._group(event)
        origin=getattr(event,"unified_msg_origin","") or ""
        if g and origin:self.db.upsert_group(g, origin)
        async with self._duel_lock:
            text,b=self.engine.duel_action(u,"attack")
            if b:b=self._refresh_duel_actor_origin(b,u,g,origin)
        personal=self._duel_result_for_user(b,u,text)
        if b:
            other_uid = str(b["p2_user_id"] if str(b["p1_user_id"])==u else b["p1_user_id"])
            if other_uid:
                other_text=self._duel_notice_for_user(b,other_uid,u,text)
                target_group=str(b["p1_group_id"] if str(b["p1_user_id"])==other_uid else b["p2_group_id"])
                await self._deliver_duel_notification(b, other_uid, other_text, preferred_group_id=target_group, dedupe_key=f"action:{b['id']}:{other_uid}:round:{b['round_no']}:turn:{b['turn_user_id']}")
        yield event.chain_result(self._mention_chain(u, personal))

    @filter.command("决斗技能", alias={"pvp技能"})
    async def duel_skill(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event); u=self._user(event); g=self._group(event); skill=args[0] if args else ""
        origin=getattr(event,"unified_msg_origin","") or ""
        if g and origin:self.db.upsert_group(g, origin)
        async with self._duel_lock:
            text,b=self.engine.duel_action(u,"skill",skill)
            if b:b=self._refresh_duel_actor_origin(b,u,g,origin)
        personal=self._duel_result_for_user(b,u,text)
        if b:
            other_uid = str(b["p2_user_id"] if str(b["p1_user_id"])==u else b["p1_user_id"])
            if other_uid:
                other_text=self._duel_notice_for_user(b,other_uid,u,text)
                target_group=str(b["p1_group_id"] if str(b["p1_user_id"])==other_uid else b["p2_group_id"])
                await self._deliver_duel_notification(b, other_uid, other_text, preferred_group_id=target_group, dedupe_key=f"action:{b['id']}:{other_uid}:round:{b['round_no']}:turn:{b['turn_user_id']}")
        yield event.chain_result(self._mention_chain(u, personal))

    @filter.command("决斗防御", alias={"pvp防御"})
    async def duel_guard(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        u=self._user(event); g=self._group(event)
        origin=getattr(event,"unified_msg_origin","") or ""
        if g and origin:self.db.upsert_group(g, origin)
        async with self._duel_lock:
            text,b=self.engine.duel_action(u,"guard")
            if b:b=self._refresh_duel_actor_origin(b,u,g,origin)
        personal=self._duel_result_for_user(b,u,text)
        if b:
            other_uid = str(b["p2_user_id"] if str(b["p1_user_id"])==u else b["p1_user_id"])
            if other_uid:
                other_text=self._duel_notice_for_user(b,other_uid,u,text)
                target_group=str(b["p1_group_id"] if str(b["p1_user_id"])==other_uid else b["p2_group_id"])
                await self._deliver_duel_notification(b, other_uid, other_text, preferred_group_id=target_group, dedupe_key=f"action:{b['id']}:{other_uid}:round:{b['round_no']}:turn:{b['turn_user_id']}")
        yield event.chain_result(self._mention_chain(u, personal))

    @filter.command("战后留言", alias={"决斗留言"})
    async def duel_message(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("用法：/战后留言 内容"); return
        text,b,msg_id=self.engine.duel_message(self._user(event)," ".join(args))
        if b:
            other=self._duel_target_origin(b, b["p2_user_id"] if b["p1_user_id"]==self._user(event) else b["p1_user_id"])
            sender=b["p1_name"] if b["p1_user_id"]==self._user(event) else b["p2_name"]
            delivered=False if not other else await self._broadcast(other,f"💬【决斗战后留言】{sender}：{' '.join(args)}",proactive=False)
            if delivered and msg_id:
                self.db.mark_duel_messages_delivered(self._user(event),[int(msg_id)])
        yield event.plain_result(text)

    @filter.command("复活状态", alias={"死亡状态","复活"})
    async def revive_status(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.death_status(self._group(event),self._user(event)))

    @filter.command("自动战斗", alias={"auto战斗", "挂机战斗"})
    async def auto_battle(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event); sub=args[0] if args else "状态"
        if sub in {"关闭","停止","off","stop"}:
            yield event.plain_result(self.engine.auto_battle_stop(self._group(event),self._user(event))); return
        if sub in {"开启","开始","on","start","Boss","boss","世界Boss"}:
            target="boss" if sub.lower() in {"boss","世界boss"} else ""
            yield event.plain_result(self.engine.auto_battle_start(self._group(event),self._user(event),self._name(event),target)); return
        yield event.plain_result(self.engine.auto_battle_status(self._group(event),self._user(event)))

    @filter.command("逃跑", alias={"逃脱","run"})
    async def flee_monster(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.monster_flee(self._group(event),self._user(event),self._name(event)))

    @filter.command("NPC", alias={"npc状态","神秘NPC"})
    async def npc(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        group=self._group(event); uid=self._user(event); name=self._name(event)
        if not args:
            yield event.plain_result(self.engine.npc_status(group)); return
        result=self.engine.npc_interact(group,uid,name,args[0])
        # Optional AI only enriches NPC dialogue; gameplay rewards are deterministic and stay usable without AI.
        if len(args)>=2 and args[0].lower() in {"talk","对话","聊天"}:
            result = await self._ai_npc_reply(event, result, story=True)
        yield event.plain_result(result)

    @filter.command("NPC对话", alias={"与NPC对话","NPC聊天"})
    async def npc_talk(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        base=self.engine.npc_interact(self._group(event),self._user(event),self._name(event),"talk")
        yield event.plain_result(await self._ai_npc_reply(event,base,story=True))

    @filter.command("地图")
    async def map(self, event: AstrMessageEvent):
        """查看世界地图。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.map_text(self._group(event)))

    @filter.command("背包", alias={"inventory"})
    async def inventory(self, event: AstrMessageEvent):
        """查看背包和装备。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.inventory_text(self._group(event), self._user(event), self._name(event)))

    @filter.command("宠物", alias={"我的宠物"})
    async def pets(self, event: AstrMessageEvent):
        """查看宠物。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.pets_text(self._group(event), self._user(event), self._name(event)))

    @filter.command("抽宠物")
    async def draw_pet(self, event: AstrMessageEvent):
        """花费金币抽取随机宠物。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.draw_pet(self._group(event), self._user(event), self._name(event)).text)

    @filter.command("出战宠物")
    async def activate_pet(self, event: AstrMessageEvent):
        """设置出战宠物。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args or not args[0].isdigit():
            yield event.plain_result("用法：/出战宠物 宠物ID")
            return
        yield event.plain_result(
            self.engine.activate_pet(self._group(event), self._user(event), int(args[0]), self._name(event))
        )

    @filter.command("商店")
    async def shop(self, event: AstrMessageEvent):
        """查看世界商店。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.shop_text(self._group(event)))

    @filter.command("购买")
    async def buy(self, event: AstrMessageEvent):
        """购买商店物品。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args:
            yield event.plain_result("用法：/购买 物品ID [数量]")
            return
        item_id = args[0]
        try:
            qty = int(args[1]) if len(args) > 1 else 1
        except ValueError:
            qty = 1
        yield event.plain_result(self.engine.buy(self._group(event), self._user(event), self._name(event), item_id, qty))

    @filter.command("装备")
    async def equipment(self, event: AstrMessageEvent):
        """查看装备。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        equip = self.engine.db.get_equipment(self._group(event), self._user(event))
        if not equip:
            yield event.plain_result("⚔️ 你还没有装备。探索有概率获得装备。")
            return
        lines = ["⚔️ 【装备栏】"]
        for row in equip:
            flag = " ⭐已装备" if row["equipped"] else ""
            revive = f"｜🕊️死亡触发{row['revive_chance']}%" if int(row['revive_chance'] or 0) else ""
            lines.append(f"#{row['id']} {row['name']} [{row['rarity']}] Lv.{row['level']}｜部位:{row['slot']}｜⚔️{row['attack']} 🛡️{row['defense']} 🗺️+{row['explore_bonus']}%{revive}{flag}")
        lines.append("\n穿戴：/穿戴 装备ID｜强化：/强化 装备ID｜拆解：/拆解 装备ID｜配方：/合成列表")
        yield event.plain_result("\n".join(lines))

    @filter.command("合成列表", alias={"装备合成", "合成配方"})
    async def crafting_list(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        yield event.plain_result(self.engine.crafting_text(self._group(event),self._user(event),self._name(event)))

    @filter.command("合成", alias={"制作", "craft"})
    async def craft_equipment(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args:
            yield event.plain_result("用法：/合成 装备ID [数量]\n先输入 `/合成列表` 查看材料需求。"); return
        try:qty=int(args[1]) if len(args)>1 else 1
        except ValueError:qty=1
        yield event.plain_result(self.engine.craft_equipment(self._group(event),self._user(event),self._name(event),args[0],qty).text)

    @filter.command("自动合成", alias={"自动制作", "autocraft"})
    async def auto_craft_equipment(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        try:qty=int(args[0]) if args else 1
        except ValueError:qty=1
        yield event.plain_result(self.engine.auto_craft_equipment(self._group(event),self._user(event),self._name(event),qty).text)

    @filter.command("拆解", alias={"分解装备"})
    async def disassemble_equipment(self, event: AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args or not args[0].isdigit():
            yield event.plain_result("用法：/拆解 装备ID\n只能拆解未穿戴装备。"); return
        yield event.plain_result(self.engine.disassemble_equipment(self._group(event),self._user(event),int(args[0]),self._name(event)).text)

    @filter.command("穿戴")
    async def equip(self, event: AstrMessageEvent):
        """穿戴装备。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args or not args[0].isdigit():
            yield event.plain_result("用法：/穿戴 装备ID")
            return
        yield event.plain_result(self.engine.equip(self._group(event), self._user(event), int(args[0]), self._name(event)))

    @filter.command("使用", alias={"使用物品", "use"})
    async def use_item(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args:
            yield event.plain_result("用法：/使用 物品ID [数量]\n示例：/使用 potion 1")
            return
        try:
            qty = int(args[1]) if len(args) > 1 else 1
        except ValueError:
            qty = 1
        result = self.engine.use_item(self._group(event), self._user(event), self._name(event), args[0], qty)
        yield event.plain_result(result.text)

    @filter.command("强化")
    async def upgrade(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args or not args[0].isdigit():
            yield event.plain_result("用法：/强化 装备ID")
            return
        yield event.plain_result(self.engine.upgrade_equipment(self._group(event), self._user(event), int(args[0]), self._name(event)).text)

    @filter.command("打工", alias={"工作"})
    async def work(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        job = args[0] if args and args[0] in {"普通打工", "搬砖", "夜班"} else "普通打工"
        yield event.plain_result(self.engine.working(self._group(event), self._user(event), self._name(event), job).text)

    @filter.command("钓鱼")
    async def fishing(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.fishing(self._group(event), self._user(event), self._name(event)).text)

    @filter.command("挖矿")
    async def mining(self, event: AstrMessageEvent):
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.mining(self._group(event), self._user(event), self._name(event)).text)

    @filter.command("职业")
    async def profession(self, event: AstrMessageEvent):
        """查看职业。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        group = self._group(event)
        player, _ = self.engine.ensure_player(group, self._user(event), self._name(event))
        lines = [f"🧭 当前职业：{player['profession']}"]
        for key, desc in PROFESSIONS.items():
            lines.append(f"• {key}：{desc}")
        cost = int(self.engine.cfg("profession_change_cost", group, 5000))
        days = int(self.engine.cfg("profession_cooldown_days", group, 7))
        lines.append(f"\n转职：`/转职 职业`｜Lv.10+，费用 {cost} 金币，冷却 {days} 天。")
        yield event.plain_result("\n".join(lines))

    @filter.command("转职")
    async def change_profession(self, event: AstrMessageEvent):
        """更换职业。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args or args[0] not in PROFESSIONS or args[0] == "无职业":
            yield event.plain_result("请选择：" + "、".join(p for p in PROFESSIONS if p != "无职业"))
            return
        group = self._group(event)
        user = self._user(event)
        name = self._name(event)
        player, _ = self.engine.ensure_player(group, user, name)
        if player["level"] < 10:
            yield event.plain_result("🧭 需要达到 Lv.10 才能转职。")
            return
        if player["profession"] == args[0]:
            yield event.plain_result("你已经是这个职业了。")
            return
        cd = self.engine.db.cooldown_remaining(group, user, "change_profession")
        if cd:
            yield event.plain_result(f"⏳ 转职冷却中，还需 {self._fmt_seconds(cd)}。")
            return
        cost = int(self.engine.cfg("profession_change_cost", group, 5000))
        days = int(self.engine.cfg("profession_cooldown_days", group, 7))
        if player["coins"] < cost:
            yield event.plain_result(f"💰 转职需要 {cost} 金币。")
            return
        self.engine.db.wallet_change(group, user, coins_delta=-cost, kind="profession", note=f"转职为{args[0]}")
        self.engine.db.set_player_field(group, user, "profession", args[0])
        self.engine.db.set_cooldown(group, user, "change_profession", days * 86400)
        yield event.plain_result(f"✅ 转职成功！\n现在职业：{args[0]}\n特性：{PROFESSIONS[args[0]]}")

    @filter.command("任务")
    async def tasks(self, event: AstrMessageEvent):
        """查看每日任务。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.tasks_text(self._group(event), self._user(event), self._name(event)) + "\n\n领取：/任务领取")

    @filter.command("任务领取")
    async def claim_tasks(self, event: AstrMessageEvent):
        """领取已完成的每日任务。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.claim_completed_tasks(self._group(event), self._user(event)))

    @filter.command("成就")
    async def achievements(self, event: AstrMessageEvent):
        """查看成就。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.achievements_text(self._group(event), self._user(event), self._name(event)))

    @filter.command("排行榜", alias={"排行", "榜单"})
    async def rankings(self, event: AstrMessageEvent):
        """查看群排行榜。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.rankings(self._group(event)))

    # ------------------------- games -------------------------

    @filter.command("游戏")
    async def games(self, event: AstrMessageEvent):
        """查看小游戏菜单。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(
            "🎮 【小游戏中心】\n\n"
            "🎯 /猜数字 → /猜 50\n"
            "✊ /猜拳 石头\n"
            "🎲 /骰子\n"
            "💣 /炸弹 → /抽炸弹\n\n"
            "每日小游戏参与都会提升每日任务进度。"
        )

    @filter.command("猜数字")
    async def guess_start(self, event: AstrMessageEvent):
        """开始猜数字。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.game_start(self._group(event), self._user(event), "guess"))

    @filter.command("猜")
    async def guess_number(self, event: AstrMessageEvent):
        """在猜数字游戏中提交数字。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        if not args:
            yield event.plain_result("用法：/猜 1-100之间的数字")
            return
        try:
            guess = int(args[0])
        except ValueError:
            yield event.plain_result("❌ 请输入整数。")
            return
        if not 1 <= guess <= 100:
            yield event.plain_result("请输入 1~100 的整数。")
            return
        yield event.plain_result(self.engine.game_guess(self._group(event), self._user(event), guess, self._name(event)))

    @filter.command("猜拳")
    async def rps(self, event: AstrMessageEvent):
        """猜拳小游戏。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        group = self._group(event)
        user = self._user(event)
        if not args:
            current = self.engine.db.get_game(group)
            if current and current["game_type"] == "rps":
                yield event.plain_result("✊ 请出拳：/猜拳 石头、/猜拳 剪刀、/猜拳 布")
            else:
                yield event.plain_result(self.engine.game_start(group, user, "rps"))
            return
        current = self.engine.db.get_game(group)
        if not current:
            self.engine.game_start(group, user, "rps")
        yield event.plain_result(self.engine.game_rps(group, user, args[0], self._name(event)))

    @filter.command("骰子")
    async def dice(self, event: AstrMessageEvent):
        """掷骰子。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        import random

        value = random.randint(1, 6)
        reward = value * 20 if value == 6 else 0
        text = f"🎲 {self._name(event)} 掷出了 {value}。"
        if reward:
            self.engine.db.wallet_change(self._group(event), self._user(event), coins_delta=reward, kind="game_dice", note="掷出6")
            day = self.engine.db.get_today_key()
            self.engine.db.ensure_daily_tasks(self._group(event), self._user(event), day)
            self.engine.db.progress_task(self._group(event), self._user(event), day, "game", 1)
            text += f"\n🎉 欧气爆发！+{reward}金币"
        yield event.plain_result(text)

    @filter.command("炸弹")
    async def bomb_start(self, event: AstrMessageEvent):
        """开始炸弹小游戏。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.game_start(self._group(event), self._user(event), "bomb"))

    @filter.command("抽炸弹")
    async def bomb_draw(self, event: AstrMessageEvent):
        """查看自己是否抽到炸弹。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        row = self.engine.db.get_game(self._group(event))
        if not row or row["game_type"] != "bomb":
            yield event.plain_result("💣 当前没有炸弹游戏，请先 `/炸弹`。")
            return
        data = json.loads(row["data_json"])
        if data.get("bomb") == self._user(event):
            reward = 0
            yield event.plain_result("💣 BOOM！你抽到了炸弹。\n本轮失败，下一轮再战！")
        else:
            reward = 80
            self.engine.db.wallet_change(self._group(event), self._user(event), coins_delta=reward, kind="game_bomb", note="躲过炸弹")
            yield event.plain_result(f"😎 安全！你没有抽到炸弹。\n💰 +{reward}")
        self.engine.db.clear_game(self._group(event))

    @filter.command("悬赏", alias={"赏金","bounty"})
    async def bounty(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        if not bool(self.config.get('bounty_enabled',True)):
            yield event.plain_result('🎯 管理员已关闭悬赏系统。'); return
        args=self._args(event); uid=self._user(event); gid=self._group(event); name=self._name(event)
        if not args:
            yield event.plain_result(self.engine.bounty_list()); return
        sub=args[0]
        if sub in {'规则','规则说明','rules'}:
            yield event.plain_result(
                '🎯【悬赏规则 2.0】\n\n'
                f"每天最多发布：{int(self.config.get('bounty_player_daily_limit',3) or 3)} 次\n"
                f"同时进行中：{int(self.config.get('bounty_player_max_open',3) or 3)} 个\n"
                f"单次金额：{int(self.config.get('bounty_min_coins',100) or 100)} ~ {int(self.config.get('bounty_max_coins',20000) or 20000)} 金币\n"
                f"发布费：{int(self.config.get('bounty_publish_fee_coins',20) or 20)} 金币\n"
                f"同一目标冷却：{max(0,int(self.config.get('bounty_same_target_cooldown_seconds',21600) or 21600))//3600} 小时\n\n"
                '发布时奖励金币会先进入托管；只有正式决斗中挑战者战胜目标才结算。目标获胜、拒绝或决斗超时，不会凭空生成奖励金币。\n'
                '格式：`/悬赏 发布 GW-XXXXXXXXXX 500 与目标进行正式决斗`'
            ); return
        if sub in {'我的','mine'}:
            rows=self.db.get_user_bounties(uid,20)
            if not rows: yield event.plain_result('🎯 你还没有发布过玩家悬赏。'); return
            labels={'open':'开放中','challenge':'等待目标','dueling':'决斗中','completed':'已完成','expired':'已过期','cancelled':'已取消'}
            lines=['🎯【我的悬赏】']
            for r in rows:
                lines.append(f"#{r['id']}｜目标 {r['target_player_uid'] or r['target_name'] or '—'}｜💰{int(r['reward_coins'])}｜{labels.get(r['status'],r['status'])}")
            yield event.plain_result('\n'.join(lines)); return
        if sub in {'发布','创建','post','create'}:
            if len(args)<3:
                yield event.plain_result('用法：`/悬赏 发布 GW-XXXXXXXXXX 金币 内容`\n例如：`/悬赏 发布 GW-A1B2C3D4E5 500 与目标正式决斗`'); return
            target_ref=args[1].strip().upper()
            target=self.db.find_player_by_uid(target_ref)
            if not target:
                yield event.plain_result('❌ 找不到该 GW-UID。请使用 `/排行榜` 或 `/我的` 获取正确 UID。'); return
            target_uid=str(target['user_id'] or '')
            if target_uid==uid:
                yield event.plain_result('❌ 不能悬赏自己。'); return
            if int(target['banned'] or 0):
                yield event.plain_result('❌ 该玩家当前已被封禁，不能作为悬赏目标。'); return
            if self.db.has_active_bounty(uid,target_uid):
                yield event.plain_result('❌ 你已经有一个针对该目标的进行中悬赏。'); return
            daily=int(self.config.get('bounty_player_daily_limit',3) or 3)
            if daily>0 and self.db.count_bounties_published(uid,int(time.time())-86400)>=daily:
                yield event.plain_result(f'❌ 你今天已经发布 {daily} 次悬赏，明天再来。'); return
            max_open=int(self.config.get('bounty_player_max_open',3) or 3)
            open_rows=[r for r in self.db.get_user_bounties(uid,100) if str(r['status']) in {'open','challenge','dueling'}]
            if max_open>0 and len(open_rows)>=max_open:
                yield event.plain_result(f'❌ 你的进行中悬赏已达到上限 {max_open} 个。'); return
            cooldown=max(0,int(self.config.get('bounty_same_target_cooldown_seconds',21600) or 21600))
            if cooldown>0 and self.db.recent_bounty_against_target(uid,target_uid,int(time.time())-cooldown):
                yield event.plain_result(f'❌ 你最近已经针对该玩家发布过悬赏，请等待 {cooldown//3600} 小时冷却。'); return
            try: reward=int(args[2])
            except ValueError:
                yield event.plain_result('❌ 悬赏金额必须是整数。'); return
            min_reward=max(1,int(self.config.get('bounty_min_coins',100) or 100)); max_reward=max(min_reward,int(self.config.get('bounty_max_coins',20000) or 20000))
            if reward<min_reward or reward>max_reward:
                yield event.plain_result(f'❌ 悬赏金额必须在 {min_reward} ~ {max_reward} 金币之间。'); return
            desc=' '.join(args[3:]).strip()[:1000]
            if not desc: desc='与指定玩家进行正式跨群决斗并取胜。'
            hours=max(1,min(720,int(self.config.get('bounty_default_duration_hours',24) or 24)))
            fee=max(0,int(self.config.get('bounty_publish_fee_coins',20) or 20))
            target_groups=self.db.get_user_groups(target_uid); target_group=target_groups[0] if target_groups else None
            target_group_id=str(target_group['group_id']) if target_group else ''
            target_origin=''
            if target_group:
                g=self.db.get_group(target_group_id); target_origin=str(g['session_origin'] or '') if g else ''
            ok,msg,bid=self.db.create_player_bounty(
                publisher_user_id=uid,publisher_name=name,publisher_group_id=gid,publisher_origin=getattr(event,'unified_msg_origin','') or '',
                target_user_id=target_uid,target_player_uid=str(target['player_uid']),target_name=str(target['name'] or '冒险者'),target_group_id=target_group_id,
                title='玩家悬赏：与指定冒险者正式决斗',description=desc,reward_coins=reward,expires_at=int(time.time())+hours*3600,publish_fee=fee,
            )
            if not ok:
                yield event.plain_result(f'❌ {msg}'); return
            notice=(f"🎯【悬赏通缉令】\n你被玩家 {name}（{uid}）发布为决斗悬赏目标！\n悬赏编号：#{bid}\n奖励：💰 {reward}\n\n"
                    f"目标描述：{desc}\n\n任何其他玩家可输入 `/悬赏 领取 {bid}` 发起挑战；只有挑战者正式获胜才能获得托管奖励。")
            if target_origin:
                await self._broadcast(target_origin,notice,proactive=False,mention_user_id=target_uid)
            yield event.plain_result(f'✅ 悬赏 #{bid} 发布成功！\n目标：{target["player_uid"]} · {target["name"]}\n💰 托管奖励：{reward}\n💸 发布费：{fee}\n⏳ 有效期：{hours} 小时\n\n已通知目标玩家。'); return
        if sub in {'详情','detail','查看'} and len(args)>=2 and args[1].isdigit():
            yield event.plain_result(self.engine.bounty_detail(int(args[1]))); return
        if sub in {'领取','接取','claim'} and len(args)>=2 and args[1].isdigit():
            bid=int(args[1]); row=self.db.get_bounty(bid)
            if not row or row['status']!='open': yield event.plain_result('❌ 这个悬赏当前不可领取。'); return
            if str(row['target_user_id'] or '')==uid: yield event.plain_result('❌ 目标本人不能领取针对自己的决斗悬赏。'); return
            if self.db.get_active_duel_for_user(uid): yield event.plain_result('⏳ 你当前已经在决斗中。'); return
            challenge_exp=int(time.time())+max(30,int(self.config.get('bounty_challenge_timeout_seconds',300) or 300))
            if not self.db.claim_bounty(bid,uid,name,gid,getattr(event,'unified_msg_origin','') or '',challenge_exp):
                yield event.plain_result('❌ 悬赏已被其他玩家接取、已失效，或你不能接取该悬赏。'); return
            target_origin=str(row['target_origin'] or '')
            if not target_origin:
                groups=self.db.get_user_groups(str(row['target_user_id'] or ''))
                if groups:
                    g=self.db.get_group(str(groups[0]['group_id']))
                    target_origin=str(g['session_origin'] or '') if g else ''
            notice=f"🎯【悬赏挑战】\n玩家 {name}（{uid}）已接取悬赏 #{bid}。\n{row['title']}\n\n请在倒计时内选择：\n✅ `/悬赏 接受 {bid}` 开始正式决斗\n❌ `/悬赏 拒绝 {bid}` 放弃此次挑战"
            if target_origin:
                await self._broadcast(target_origin,notice,proactive=False,mention_user_id=str(row['target_user_id'] or ''))
            yield event.plain_result(f'✅ 已接取悬赏 #{bid}，挑战请求已通知目标。\n⏳ 等待目标接受，超时将自动重新开放。'); return
        if sub in {'接受','accept'} and len(args)>=2 and args[1].isdigit():
            bid=int(args[1]); row=self.db.get_bounty(bid)
            if not row or row['status']!='challenge' or str(row['target_user_id'] or '')!=uid: yield event.plain_result('❌ 这个悬赏没有等待你接受，或挑战已经过期。'); return
            if self.db.get_active_duel_for_user(uid): yield event.plain_result('⏳ 你当前已经在其他决斗中。'); return
            p=self.engine_db.get_global_player(uid)
            if p and int(p['death_state'] or 0): yield event.plain_result('💀 你当前处于死亡状态，不能接受悬赏决斗。'); return
            if not self.db.accept_bounty(bid,uid): yield event.plain_result('❌ 接受失败，挑战可能已经超时。'); return
            text,battle=self.engine.create_direct_duel_for_bounty(bid,int(self.config.get('duel_turn_timeout_seconds',120) or 120))
            if not battle:
                self.db.execute("UPDATE bounties SET status='open',claimed_by_user_id=NULL,claimed_by_name=NULL,claimed_group_id=NULL,claimed_origin=NULL,challenge_expires_at=0,duel_battle_id=NULL WHERE id=? AND status='dueling'",(bid,))
                yield event.plain_result(text); return
            claimant=str(row['claimed_by_user_id'] or ''); claimant_origin=str(row['claimed_origin'] or '')
            if claimant_origin:
                await self._broadcast(claimant_origin,f"⚔️【悬赏决斗成立】\n目标 {name} 已接受悬赏 #{bid}。\n现在开始正式战斗。\n输入 `/决斗攻击`、`/决斗技能` 或 `/决斗防御`。",proactive=False,mention_user_id=claimant)
            yield event.plain_result(text); return
        if sub in {'拒绝','reject'} and len(args)>=2 and args[1].isdigit():
            bid=int(args[1]); row=self.db.get_bounty(bid)
            if not row or row['status']!='challenge' or str(row['target_user_id'] or '')!=uid: yield event.plain_result('❌ 这个悬赏没有等待你拒绝，或挑战已经过期。'); return
            if not self.db.reject_bounty(bid,uid): yield event.plain_result('❌ 拒绝失败。'); return
            if row['claimed_origin']:
                await self._broadcast(str(row['claimed_origin']),f"❌【悬赏 #{bid}】目标玩家拒绝了这次挑战，悬赏已重新开放。",proactive=False,mention_user_id=str(row['claimed_by_user_id'] or ''))
            yield event.plain_result(f'✅ 已拒绝悬赏 #{bid}，现在其他玩家可以重新接取。'); return
        if sub in {'取消','cancel'} and len(args)>=2 and args[1].isdigit():
            result=self.db.cancel_bounty(int(args[1]),requester_user_id=uid)
            yield event.plain_result((f"✅ {result['message']}\n💰 已退回托管金币：{int(result.get('refund_coins',0))}" if result.get('ok') else f"❌ {result.get('message','取消失败。')}")); return
        yield event.plain_result('用法：`/悬赏` 查看大厅｜`/悬赏 规则`｜`/悬赏 发布 GW-UID 金币 内容`｜`/悬赏 我的`｜`/悬赏 详情 ID`｜`/悬赏 领取 ID`｜`/悬赏 接受 ID`｜`/悬赏 拒绝 ID`｜`/悬赏 取消 ID`')

    @filter.command("大世界Boss", alias={"大世界BOSS","GlobalBoss","globalboss","世界大Boss"})
    async def global_boss(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        if not bool(self.config.get('global_boss_enabled',True)):
            yield event.plain_result('🌍 管理员已关闭大世界 Boss。'); return
        args=self._args(event); sub=(args[0] if args else '状态')
        if sub in {'状态','status','查看',''}: yield event.plain_result(self.engine.global_boss_status()); return
        if sub in {'排行','排名','ranking'}:
            raw=self.db.get_global_boss_ranking(20); lines=['🏆【大世界 Boss 贡献榜】']
            if not raw: lines.append('暂无贡献记录。')
            for i,r in enumerate(raw,1): lines.append(f"{i}. {r['name']}｜{fmt_num(int(r['damage']))} 伤害｜{int(r['attacks'])} 次")
            yield event.plain_result('\n'.join(lines)); return
        skill='普攻'
        if sub in {'技能','skill'}: skill=args[1] if len(args)>=2 else '普攻'
        elif sub not in {'攻击','attack'}: skill=sub
        result=self.engine.global_boss_attack(self._user(event),self._name(event),skill)
        yield event.plain_result(result.text)
        b=self.db.get_global_boss()
        if int(b['active']) and int(b['hp'])<=0:
            finish=self.engine.finish_global_boss(f"{self._name(event)} 完成最后一击")
            await self._broadcast_all_groups(finish)

    # ------------------------- Boss & social -------------------------

    @filter.command("Boss", alias={"boss", "世界Boss"})
    async def boss(self, event: AstrMessageEvent):
        """查看世界 Boss。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        yield event.plain_result(self.engine.boss_status(self._group(event)))

    @filter.command("攻击")
    async def attack(self, event: AstrMessageEvent):
        """攻击世界 Boss。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        result = self.engine.attack_boss(self._group(event), self._user(event), self._name(event))
        yield event.plain_result(result.text)

    @filter.command("转账")
    async def transfer(self, event: AstrMessageEvent):
        """转账给群聊世界中的其他玩家。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        args = self._args(event)
        target_ref = self._target_id(event, args)
        if not target_ref:
            yield event.plain_result("用法：/转账 @用户 金额，或 /转账 玩家UID 金额")
            return
        target = target_ref
        if target_ref.upper().startswith("GW-"):
            matched = self.db.find_player_by_uid(target_ref.upper())
            if not matched:
                yield event.plain_result("❌ 找不到这个玩家 UID，请先确认 UID 是否正确。")
                return
            target = str(matched["user_id"])
        numeric_args = [a for a in args if a.isdigit() and a != target_ref]
        amount_arg = numeric_args[-1] if numeric_args else None
        if not amount_arg:
            yield event.plain_result("请输入转账金额。")
            return
        yield event.plain_result(
            self.engine.transfer(self._group(event), self._user(event), target, int(amount_arg), self._name(event))
        )

    # ------------------------- admin -------------------------

    @filter.command("世界管理", alias={"世界管理员", "worldadmin"})
    async def admin(self, event: AstrMessageEvent):
        """群聊世界管理员控制台。"""
        group = self._group(event)
        if not group:
            yield event.plain_result("🌎 管理命令只能在群聊使用。")
            return
        if not self._is_operator(event):
            yield event.plain_result("🚫 你没有群聊世界管理权限。\n\n本插件把权限分为：世界超级管理员 ＞ 会话管理员 ＞ 普通玩家。\n会话管理员默认只有查看权限，世界超级管理员需要在插件配置中设置。")
            return
        args = self._args(event)
        if not args:
            yield event.plain_result(
                "🛠️ 【世界管理】\n\n"
                "/世界管理 开启\n/世界管理 关闭\n"
                "/世界管理 发金币 用户ID 数量\n/世界管理 扣金币 用户ID 数量\n"
                "/世界管理 发钻石 用户ID 数量\n"
                "/世界管理 封禁 用户ID\n/世界管理 解封 用户ID\n"
                "/世界管理 重置 用户ID\n/世界管理 触发事件\n"
                "/世界管理 开启Boss\n/世界管理 结束Boss\n/世界管理 状态"
            )
            return
        cmd = args[0]
        if cmd == "开启":
            if not self._has_admin_action(event, "开启"):
                yield event.plain_result("🚫 只有世界超级管理员或被授权的会话管理员才能执行此操作。")
                return
            self.engine.db.upsert_group(group, event.unified_msg_origin)
            self.engine.db.update_group(group, enabled=1)
            self.engine.db.add_admin_log(group, self._user(event), "enable", None, "开启群聊世界")
            yield event.plain_result("✅ 群聊世界已开启。")
            return
        if cmd == "关闭":
            if not self._has_admin_action(event, "关闭"):
                yield event.plain_result("🚫 只有世界超级管理员或被授权的会话管理员才能执行此操作。")
                return
            self.engine.db.update_group(group, enabled=0)
            self.engine.db.add_admin_log(group, self._user(event), "disable", None, "关闭群聊世界")
            yield event.plain_result("✅ 群聊世界已关闭。")
            return
        if cmd in {"发金币", "扣金币", "发钻石"}:
            if not self._has_admin_action(event, cmd):
                yield event.plain_result("🚫 经济操作需要世界超级管理员权限。")
                return
            if len(args) < 3 or not args[1].isdigit() or not args[2].isdigit():
                yield event.plain_result(f"用法：/世界管理 {cmd} 用户ID 数量")
                return
            target = args[1]
            amount = int(args[2])
            player = self.engine.db.get_player(group, target)
            if not player:
                yield event.plain_result("❌ 找不到该玩家。")
                return
            if cmd == "发金币":
                delta_coins, delta_gems = amount, 0
            elif cmd == "扣金币":
                delta_coins, delta_gems = -amount, 0
            else:
                delta_coins, delta_gems = 0, amount
            row = self.engine.db.wallet_change(group, target, delta_coins, delta_gems, kind="admin", note=f"管理员 {self._user(event)} 操作")
            if not row:
                yield event.plain_result("❌ 操作失败：余额不能为负。")
                return
            self.engine.db.add_admin_log(group, self._user(event), cmd, target, f"数量={amount}")
            yield event.plain_result(f"✅ 操作成功。\n目标：{target}\n💰 {row['coins']}｜💎 {row['gems']}")
            return
        if cmd in {"封禁", "解封"}:
            if not self._has_admin_action(event, cmd):
                yield event.plain_result("🚫 用户管理操作需要世界超级管理员权限。")
                return
            if len(args) < 2:
                yield event.plain_result(f"用法：/世界管理 {cmd} 用户ID")
                return
            target = args[1]
            if not self.engine.db.get_player(group, target):
                yield event.plain_result("❌ 找不到该玩家。")
                return
            self.engine.db.set_player_field(group, target, "banned", 1 if cmd == "封禁" else 0)
            self.engine.db.add_admin_log(group, self._user(event), cmd, target, "")
            yield event.plain_result(f"✅ {cmd}成功：{target}")
            return
        if cmd == "重置":
            if not self._has_admin_action(event, "重置"):
                yield event.plain_result("🚫 重置玩家需要世界超级管理员权限。")
                return
            if len(args) < 2:
                yield event.plain_result("用法：/世界管理 重置 用户ID")
                return
            target = args[1]
            self.engine.db.execute("DELETE FROM inventory WHERE group_id=? AND user_id=?", (group, target))
            self.engine.db.execute("DELETE FROM pets WHERE group_id=? AND user_id=?", (group, target))
            self.engine.db.execute("DELETE FROM equipment WHERE group_id=? AND user_id=?", (group, target))
            self.engine.db.execute("DELETE FROM achievements WHERE group_id=? AND user_id=?", (group, target))
            self.engine.db.execute(
                "UPDATE players SET level=1,exp=0,coins=?,gems=?,stamina=?,max_stamina=?,profession='无职业',title='初出茅庐',streak=0,total_checkin=0,last_checkin=NULL,explore_count=0,explore_day='',active_pet_id=NULL WHERE group_id=? AND user_id=?",
                (
                    int(self.engine.cfg("default_new_player_coins", group, 1000)),
                    int(self.engine.cfg("default_new_player_gems", group, 3)),
                    int(self.engine.cfg("max_stamina", group, 100)),
                    int(self.engine.cfg("max_stamina", group, 100)),
                    group,
                    target,
                ),
            )
            self.engine.db.add_admin_log(group, self._user(event), "reset", target, "重置玩家")
            yield event.plain_result(f"✅ 已重置玩家 {target} 的群聊世界数据。")
            return
        if cmd == "触发事件":
            if not self._has_admin_action(event, "触发事件"):
                yield event.plain_result("🚫 只有高权限管理员可以触发世界事件。")
                return
            text = self.engine.random_world_event(group)
            self.engine.db.add_admin_log(group, self._user(event), "event", None, text.replace("\n", " "))
            yield event.plain_result(text)
            return
        if cmd == "开启Boss":
            if not self._has_admin_action(event, "开启Boss"):
                yield event.plain_result("🚫 只有高权限管理员可以操作 Boss。")
                return
            text = self.engine.spawn_boss(group)
            self.engine.db.add_admin_log(group, self._user(event), "spawn_boss", None, text)
            yield event.plain_result(text)
            return
        if cmd == "结束Boss":
            if not self._has_admin_action(event, "结束Boss"):
                yield event.plain_result("🚫 只有高权限管理员可以操作 Boss。")
                return
            row = self.engine.db.get_group(group)
            if not row or not row["boss_active"]:
                yield event.plain_result("当前没有 Boss。")
                return
            text = self.engine.finish_boss(group)
            self.engine.db.add_admin_log(group, self._user(event), "finish_boss", None, text)
            yield event.plain_result(text)
            return
        if cmd == "状态":
            stats = self.engine.db.get_group_stats(group)
            row = self.engine.db.get_group(group)
            yield event.plain_result(
                f"🛠️ 群状态\n启用：{'是' if row and row['enabled'] else '否'}\n"
                f"玩家：{stats['players']}\n累计消息：{stats['messages']}\n"
                f"今日消息：{stats['today_messages']}\n今日活跃：{stats['today_active_users']}\n"
                f"Boss：{'进行中' if row and row['boss_active'] else '无'}\n"
                f"你的权限：{'世界超级管理员' if self._admin_level(event)>=3 else '会话管理员'}"
            )
            return
        yield event.plain_result("❌ 未知管理命令。发送 `/世界管理` 查看。")

    # ------------------------- plugin Pages -------------------------

    def _web_username(self) -> str:
        try:
            return str(request.username or "") if request else ""
        except Exception:
            return ""

    def _web_admin_usernames(self) -> set[str]:
        return self._csv_set("web_admin_usernames")

    def _web_token(self) -> str | None:
        try:
            return request.query.get("token") if request else None
        except Exception:
            return None

    def _web_authorized(self, write: bool = False, token: str | None = None) -> bool:
        username = self._web_username()
        admins = self._web_admin_usernames()
        if username and username in admins:
            return True
        # Password-login sessions are also recognized by bridge.upload(), which has
        # no place to carry our custom token in multipart form data.
        session_key = self._web_session_key()
        expires = self._web_write_sessions.get(session_key, 0)
        if expires > int(time.time()):
            return True
        if session_key in self._web_write_sessions:
            self._web_write_sessions.pop(session_key, None)
        token = token or self._web_token()
        if token and token in self._web_tokens:
            owner, expires = self._web_tokens[token]
            if expires > int(time.time()):
                return True
            self._web_tokens.pop(token, None)
        return False

    def _web_session_key(self) -> str:
        try:
            username = str(request.username or "").strip() if request else ""
            if username:
                return f"user:{username}"
            host = str(request.client_host or "unknown") if request else "unknown"
            return f"host:{host}"
        except Exception:
            return "unknown"

    def _issue_web_token(self) -> str:
        # Short-lived in-memory token; it never lands in SQLite/config.
        token = secrets.token_urlsafe(32)
        owner = self._web_username() or self._web_session_key()
        expires = int(time.time()) + 6 * 3600
        self._web_tokens[token] = (owner, expires)
        # bridge.upload() intentionally only carries the file, not arbitrary JSON/query
        # values. Record the successful password-login session so multipart uploads
        # from the same Dashboard session can be authorized without weakening the
        # normal plugin-page access checks.
        self._web_write_sessions[self._web_session_key()] = expires
        return token

    def _web_action_allowed_once(self, group_id: str, action: str, window_seconds: int = 4) -> bool:
        """Prevent duplicate browser handlers/retries from repeating destructive actions."""
        now = time.monotonic()
        key = (str(group_id), str(action))
        previous = self._web_action_guard.get(key, 0.0)
        if now - previous < max(1, int(window_seconds)):
            return False
        self._web_action_guard[key] = now
        # Small cleanup to avoid unbounded growth on busy installations.
        if len(self._web_action_guard) > 512:
            cutoff = now - 120
            self._web_action_guard = {k: v for k, v in self._web_action_guard.items() if v >= cutoff}
        return True

    def _require_web(self, write: bool = False, token: str | None = None):
        if self._web_authorized(write=write, token=token):
            return None
        if error_response:
            return error_response("无权访问此管理接口，请使用插件超级管理员账号或管理口令解锁。", status_code=403)
        return {"error": "forbidden"}

    async def page_bootstrap(self):
        password_configured = bool(str(self.config.get("web_admin_password", "")))
        username = self._web_username()
        authed = self._web_authorized(False)
        return json_response({
            "author": "ysgl",
            "plugin": PLUGIN_NAME,
            "version": "1.13.5",
            "username": username,
            "authenticated": authed,
            "password_configured": password_configured,
            "web_admin_usernames": sorted(self._web_admin_usernames()),
            "capabilities": {"read": authed, "write": authed},
        })

    async def page_login(self):
        payload = await request.json(default={}) if request else {}
        password = str(payload.get("password", ""))[:256]
        configured = str(self.config.get("web_admin_password", ""))
        if not configured:
            return error_response("尚未配置 Web 管理口令。请先在 AstrBot 插件配置中设置“Web 管理口令”。", status_code=400)
        if not secrets.compare_digest(password, configured):
            return error_response("管理口令错误。", status_code=401)
        token = self._issue_web_token()
        return json_response({"authenticated": True, "token": token, "expires_in": 21600})

    def _safe_config(self) -> dict[str, Any]:
        out = {}
        for key, spec in self._schema.items():
            value = self.config.get(key, spec.get("default"))
            if spec.get("secret") and value:
                out[key] = "********"
            else:
                out[key] = value
        return out

    async def page_overview(self):
        denied = self._require_web(False)
        if denied: return denied
        summary = self.db.get_dashboard_summary()
        groups = [dict(r) for r in self.db.get_group_details(30)]
        include_worldplus = False
        refresh_worldplus = False
        try:
            include_worldplus = bool(request and str(request.query.get("worldplus") or "0") == "1")
            refresh_worldplus = bool(request and str(request.query.get("refresh") or "0") == "1")
        except Exception:
            pass
        payload = {"summary": summary, "groups": groups}
        if include_worldplus:
            if refresh_worldplus and bool(self.config.get("cloud_enabled", False)) and str(self.config.get("cloud_api_key") or "").strip():
                try:
                    await self._cloud_sync_if_due(force=True)
                except Exception as exc:
                    logger.warning("[群聊世界] 刷新悬赏资产目录时云端同步失败：%s", exc)
            self.db.expire_bounties()
            bounties = []
            for row in self.db.get_bounties(None, 100):
                item = dict(row)
                try:
                    item["reward_items"] = json.loads(str(item.get("reward_items_json") or "[]"))
                except Exception:
                    item["reward_items"] = []
                bounties.append(item)
            payload["worldplus"] = {
                "ok": True,
                "bounties": bounties,
                "global_boss": dict(self.db.get_global_boss()),
                "global_boss_ranking": [dict(x) for x in self.db.get_global_boss_ranking(20)],
                "catalog": self._build_bounty_reward_catalog(),
                "config": {k: self.config.get(k) for k in (
                    'bounty_enabled','bounty_default_duration_hours','bounty_challenge_timeout_seconds','bounty_player_daily_limit',
                    'bounty_player_max_open','bounty_min_coins','bounty_max_coins','bounty_publish_fee_coins','bounty_same_target_cooldown_seconds',
                    'world_map_enabled','world_travel_enabled','world_travel_base_cost','world_travel_cooldown_seconds','world_reputation_enabled',
                    'global_boss_enabled','global_boss_auto_spawn','global_boss_interval_hours','global_boss_name','global_boss_description',
                    'global_boss_max_hp','global_boss_attack','global_boss_defense','global_boss_skill_chance_percent','global_boss_duration_hours',
                    'global_boss_attack_cooldown_seconds','global_boss_stamina_cost','global_boss_participation_reward',
                    'global_boss_reward_pool_coins','global_boss_reward_pool_gems','global_boss_exp_per_1000_damage',
                    'global_boss_enrage_threshold_percent','global_boss_enrage_multiplier','global_boss_profile_json'
                )},
                "refreshed": refresh_worldplus,
            }
        return json_response(payload)

    async def page_groups(self):
        denied = self._require_web(False)
        if denied: return denied
        return json_response({"groups": [dict(r) for r in self.db.get_group_details(300)]})

    async def page_players(self):
        denied = self._require_web(False)
        if denied: return denied
        search = str(request.query.get("search") or "") if request else ""
        rows = self.engine.db.get_global_players(1000, search[:80]) if hasattr(self.engine.db, "get_global_players") else self.db.get_player_dashboard("__GLOBAL_USER__",1000,search[:80])
        out=[]
        for row in rows:
            item=dict(row)
            item["scope"]="global"
            item["groups"]=[r[0] for r in self.db.fetchall("SELECT group_id FROM group_members WHERE user_id=? ORDER BY group_id", (row["user_id"],))]
            out.append(item)
        return json_response({"players": out})

    async def page_transactions(self):
        denied = self._require_web(False)
        if denied: return denied
        user_id = str(request.query.get("user_id") or "") if request else ""
        rows = self.db.get_transactions("__GLOBAL_USER__", 500, user_id or None)
        return json_response({"transactions": [dict(r) for r in rows]})

    async def page_events(self):
        denied = self._require_web(False)
        if denied: return denied
        group_id = str(request.query.get("group_id") or "") if request else ""
        if group_id:
            rows = self.db.get_recent_events(group_id, 300)
        else:
            rows = self.db.fetchall("SELECT * FROM world_event_logs ORDER BY created_at DESC LIMIT 500")
        return json_response({"events": [dict(r) for r in rows]})

    async def page_logs(self):
        denied = self._require_web(False)
        if denied: return denied
        group_id = str(request.query.get("group_id") or "") if request else ""
        if group_id:
            admin_rows = self.db.get_recent_logs(group_id, 200)
            action_rows = self.db.get_group_action_logs(group_id, 300)
        else:
            admin_rows = self.db.fetchall("SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT 500")
            action_rows = self.db.fetchall("SELECT * FROM action_logs ORDER BY created_at DESC LIMIT 500")
        return json_response({
            "admin_logs": [dict(r) for r in admin_rows],
            "action_logs": [dict(r) for r in action_rows],
        })

    async def page_tasks(self):
        denied = self._require_web(False)
        if denied: return denied
        date_key = str(request.query.get("date") or self.db.get_today_key()) if request else self.db.get_today_key()
        group_id = str(request.query.get("group_id") or "") if request else ""
        rows = self.db.get_dashboard_tasks(date_key, group_id or None, 1500)
        summary = {
            "rows": len(rows),
            "completed": sum(1 for r in rows if int(r["completed"] or 0)),
            "players": len({str(r["user_id"]) for r in rows}),
        }
        return json_response({"date": date_key, "tasks": [dict(r) for r in rows], "summary": summary})

    async def page_tutorials(self):
        denied = self._require_web(False)
        if denied: return denied
        rows = self.db.get_dashboard_tutorials(1000)
        counts = {"pending": 0, "completed": 0, "skipped": 0}
        for r in rows:
            key = str(r["tutorial_status"] or "pending")
            counts[key] = counts.get(key, 0) + 1
        return json_response({"tutorials": [dict(r) for r in rows], "counts": counts})

    async def page_system(self):
        """System operations aggregate endpoint.

        Older AstrBot builds / plugin-page bridges occasionally report a missing
        route when several sibling APIs are requested during the first render.
        This endpoint intentionally returns the complete system view in one
        response, while the legacy sibling routes remain available below.
        """
        denied = self._require_web(False)
        if denied: return denied
        metrics = self._system_metrics()
        groups_by_id = {str(r["group_id"]): dict(r) for r in self.db.get_group_details(500)}
        broadcast_rows = {str(r["group_id"]): dict(r) for r in self.db.get_group_broadcasts(500)}
        default_message = str(self.config.get("group_broadcast_default_message", "📢 群聊世界活动提醒：输入 /群聊世界状态 查看世界状态。") or "")
        default_interval = int(self.config.get("group_broadcast_default_interval_minutes", 120) or 120)
        broadcasts = []
        for gid, g in groups_by_id.items():
            row = broadcast_rows.get(gid, {})
            broadcasts.append({
                "group_id": gid, "session_origin": str(g.get("session_origin") or ""),
                "enabled": bool(row.get("enabled", 0)),
                "message": str(row.get("message") or default_message),
                "interval_minutes": int(row.get("interval_minutes") or default_interval),
                "next_send_at": int(row.get("next_send_at") or 0),
                "last_sent_at": int(row.get("last_sent_at") or 0),
                "total_sent": int(row.get("total_sent") or 0),
                "world_enabled": bool(g.get("enabled", 0)),
                "player_count": int(g.get("player_count") or 0),
            })
        campaigns=[]
        for row in self.db.get_broadcast_campaigns(100):
            item=dict(row); item["enabled"]=bool(item.get("enabled"))
            item["targets"]=[dict(x) for x in self.db.get_broadcast_campaign_targets(int(row["id"]))]
            campaigns.append(item)
        return json_response({
            "ok": True,
            "route": "system/overview",
            "metrics": metrics,
            "groups": broadcasts,
            "config": {
                "maintenance_auto_cleanup": bool(self.config.get("maintenance_auto_cleanup", True)),
                "maintenance_cleanup_time": str(self.config.get("maintenance_cleanup_time", "04:30") or "04:30"),
                "maintenance_cache_retention_days": int(self.config.get("maintenance_cache_retention_days", 7) or 7),
                "group_broadcast_default_message": default_message,
                "group_broadcast_default_interval_minutes": default_interval,
            },
            "cleanup": {"auto_date": self._last_auto_cleanup_date, "cache_dir": str(self.cache_dir)},
            "broadcasts": broadcasts,
            "campaigns": campaigns,
            "running": [cid for cid, task in self._broadcast_campaign_tasks.items() if task and not task.done()],
        })

    async def page_menu_status(self):
        self._refresh_help_menu_config_from_disk()
        denied = self._require_web(False)
        if denied:
            return denied
        include_image = str(request.query.get("include_image") or "0") in {"1", "true", "yes"} if request else False
        return json_response({"ok": True, **self._menu_status(include_image=include_image)})

    async def page_menu_settings_save(self):
        """只保存帮助菜单，不依赖高级配置总保存权限。

        能够读取群聊世界后台的管理员同样可以修改这个专属菜单分区。
        这样不会再因为总配置接口的写权限判断导致“禁止修改”。
        """
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(False, token)
        if denied:
            return denied
        changes = payload.get("changes") or {}
        if not isinstance(changes, dict):
            return error_response("changes 必须是对象。", status_code=400)
        allowed = {"image_enabled", "text_enabled", "image_first"}
        unknown = [k for k in changes if k not in allowed]
        if unknown:
            return error_response(f"不允许修改配置项：{unknown[0]}", status_code=400)
        normalized_changes = {k: self._config_bool(changes[k], True) for k in changes}
        self._set_help_menu_config(**normalized_changes)
        if not self._save_config_object(self.config):
            return error_response("帮助菜单设置写入失败，请检查 AstrBot 配置文件权限。", status_code=500)
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "help_menu_save", None, json.dumps(changes, ensure_ascii=False))
        return json_response({"ok": True, "message": "帮助菜单设置已保存。", **self._menu_status(False)})

    @staticmethod
    def _decode_uploaded_base64(raw: str) -> bytes:
        """兼容 data URL、换行、URL-safe Base64 以及缺失 padding。"""
        raw = str(raw or "").strip()
        if raw.startswith("data:"):
            head, sep, body = raw.partition(",")
            if not sep or ";base64" not in head.lower():
                raise ValueError("图片数据格式无效。")
            raw = body
        raw = re.sub(r"\s+", "", raw)
        if not raw:
            raise ValueError("图片 Base64 数据为空。")
        # Accept both standard and URL-safe Base64.
        raw = raw.replace("-", "+").replace("_", "/")
        raw += "=" * ((4 - len(raw) % 4) % 4)
        return base64.b64decode(raw, validate=True)

    async def page_menu_upload(self):
        """接受 AstrBot 原生 multipart 上传，同时兼容旧版 Base64 JSON 上传。"""
        token = None
        try:
            # bridge.upload() uses multipart/form-data with field name `file`.
            files = await request.files() if request else {}
            upload = files.get("file") if files else None
        except Exception:
            upload = None

        if upload is not None:
            denied = self._require_web(True)
            if denied:
                return denied
            tmp = self.data_dir / ".menu_upload.tmp"
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                save_method = getattr(upload, "save", None)
                if not callable(save_method):
                    return error_response("上传文件对象不支持保存，请刷新 AstrBot 管理面板后重试。", status_code=400)
                await save_method(tmp)
                decoded = tmp.read_bytes()
                filename = str(getattr(upload, "filename", "menu_image") or "menu_image")
                content_type = str(getattr(upload, "content_type", "") or "")
            except Exception as exc:
                logger.exception("[群聊世界] 读取菜单图片上传失败：%s", exc)
                return error_response(f"读取上传图片失败：{exc}", status_code=400)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            payload = await request.json(default={}) if request else {}
            token = str(payload.get("token") or "")
            denied = self._require_web(True, token)
            if denied:
                return denied
            raw = str(payload.get("image_base64") or "").strip()
            if not raw:
                return error_response("请先选择菜单图片。", status_code=400)
            try:
                decoded = self._decode_uploaded_base64(raw)
            except Exception as exc:
                # Backward compatibility: tolerate harmless transport quotes/whitespace
                # before giving a hard Base64 error.
                cleaned = raw.strip().strip('"').strip("'").replace('\n', '')
                try:
                    decoded = base64.b64decode(cleaned, altchars=b"-_", validate=False)
                except Exception:
                    return error_response(f"图片 Base64 数据无效：{exc}", status_code=400)
            filename = "menu_image"
            content_type = ""

        if not decoded:
            return error_response("图片数据为空。", status_code=400)
        if len(decoded) > 5 * 1024 * 1024:
            return error_response("菜单图片必须小于等于 5 MB。", status_code=400)

        signatures = (
            (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
            (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
            (b"RIFF", "image/webp", ".webp"),
        )
        matched = next(((mime, suffix) for sig, mime, suffix in signatures if decoded.startswith(sig)), None)
        if matched is None:
            return error_response("仅支持 JPG、PNG、WEBP 菜单图片。请确认选择的是实际图片文件。", status_code=400)
        actual, suffix = matched
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for p in self._menu_custom_paths:
                if p.exists():
                    p.unlink()
            target = self.data_dir / f"menu_image{suffix}"
            target.write_bytes(decoded)
            self._set_help_menu_config(image_enabled=True)
            if not self._save_config_object(self.config):
                return error_response("图片已保存，但 AstrBot 配置保存失败，请检查配置文件权限。", status_code=500)
        except Exception as exc:
            logger.exception("[群聊世界] 保存菜单图片失败：%s", exc)
            return error_response("保存菜单图片失败，请检查插件数据目录权限。", status_code=500)
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "menu_image_upload", None, f"mime={actual};bytes={len(decoded)};name={filename}")
        return json_response({"ok": True, "message": "菜单图片已更新并启用。", **self._menu_status(False)})

    async def page_menu_reset(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied:
            return denied
        try:
            for p in self._menu_custom_paths:
                if p.exists():
                    p.unlink()
            self._set_help_menu_config(image_enabled=True, image_first=True, text_enabled=True)
            if not self._save_config_object(self.config):
                return error_response("默认菜单已恢复，但配置保存失败，请检查权限。", status_code=500)
            self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "menu_image_reset", None, "恢复默认菜单图片")
            return json_response({"ok": True, "message": "已恢复默认菜单图片和帮助菜单设置。", **self._menu_status(False)})
        except Exception as exc:
            return error_response(f"恢复默认菜单图片失败：{exc}", status_code=500)

    async def page_system_cleanup(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        purge_all = bool(payload.get("purge_all", False))
        days = int(payload.get("retention_days", self.config.get("maintenance_cache_retention_days", 7)) or 7)
        result = self._cleanup_cache(0 if purge_all else days)
        result["purge_all"] = purge_all
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "cleanup_cache", None, json.dumps(result, ensure_ascii=False))
        return json_response({"ok": True, **result, "message": f"已清理全部可重建缓存。" if purge_all else f"已按保留 {days} 天规则清理缓存。", "metrics": self._system_metrics()})

    async def page_broadcasts(self):
        denied = self._require_web(False)
        if denied: return denied
        default_message = str(self.config.get("group_broadcast_default_message", "") or "")
        default_interval = int(self.config.get("group_broadcast_default_interval_minutes", 120) or 120)
        rows = {str(r["group_id"]): dict(r) for r in self.db.get_group_broadcasts(500)}
        groups = []
        for g in self.db.get_group_details(500):
            gid = str(g["group_id"])
            row = rows.get(gid, {})
            groups.append({
                "group_id": gid,
                "session_origin": str(g["session_origin"] or ""),
                "enabled": bool(row.get("enabled", 0)),
                "message": str(row.get("message") or default_message),
                "interval_minutes": int(row.get("interval_minutes") or default_interval),
                "next_send_at": int(row.get("next_send_at") or 0),
                "last_sent_at": int(row.get("last_sent_at") or 0),
                "total_sent": int(row.get("total_sent") or 0),
                "world_enabled": bool(g["enabled"]),
                "player_count": int(g["player_count"] or 0),
            })
        return json_response({
            "groups": groups,
            "default_message": default_message,
            "default_interval_minutes": default_interval,
        })

    def _broadcast_group_ids_from_payload(self, payload: dict[str, Any]) -> list[str]:
        raw = payload.get("group_ids")
        if not isinstance(raw, list):
            return []
        out = []
        for value in raw[:500]:
            gid = str(value or "").strip()
            if gid and gid not in out:
                out.append(gid)
        return out

    async def page_broadcast_batch_save(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        group_ids = self._broadcast_group_ids_from_payload(payload)
        if not group_ids: return error_response("至少选择一个目标群。", status_code=400)
        message = str(payload.get("message") or "").strip()[:3000]
        if not message: return error_response("群发消息不能为空。", status_code=400)
        interval = max(1, min(10080, int(payload.get("interval_minutes", self.config.get("group_broadcast_default_interval_minutes", 120)) or 120)))
        enabled = bool(payload.get("enabled", True))
        now = int(time.time())
        saved, missing = [], []
        for gid in group_ids:
            group = self.db.get_group(gid)
            if not group or not group["session_origin"]:
                missing.append(gid)
                continue
            next_at = now + interval * 60 if enabled else 0
            self.db.upsert_group_broadcast(gid, enabled, message, interval, next_at)
            self.db.add_admin_log(gid, self._web_username() or "web", "broadcast_batch_save", None, f"enabled={enabled};interval={interval};message={message[:200]}")
            saved.append(gid)
        return json_response({"ok": True, "message": f"已保存 {len(saved)} 个群的群发配置。", "saved": saved, "missing": missing})

    async def page_broadcast_batch_send_now(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        group_ids = self._broadcast_group_ids_from_payload(payload)
        if not group_ids: return error_response("至少选择一个目标群。", status_code=400)
        message = str(payload.get("message") or "").strip()[:3000]
        if not message: return error_response("群发消息不能为空。", status_code=400)
        interval = max(1, min(86400, int(payload.get("group_interval_seconds", 1) or 1)))
        task = asyncio.create_task(self._run_broadcast_oneoff(group_ids, message, interval, self._web_username() or "web"))
        self._broadcast_oneoff_tasks.add(task)
        task.add_done_callback(self._broadcast_oneoff_tasks.discard)
        return json_response({
            "ok": True,
            "message": f"立即群发已启动：{len(group_ids)} 个群，群间隔 {interval} 秒。",
            "group_count": len(group_ids), "group_interval_seconds": interval,
            "asynchronous": True,
        })

    async def page_broadcast_campaigns(self):
        denied = self._require_web(False)
        if denied: return denied
        campaigns = []
        for row in self.db.get_broadcast_campaigns(100):
            item = dict(row)
            item["enabled"] = bool(item.get("enabled"))
            item["targets"] = [dict(x) for x in self.db.get_broadcast_campaign_targets(int(row["id"]))]
            campaigns.append(item)
        return json_response({"campaigns": campaigns, "running": [cid for cid, task in self._broadcast_campaign_tasks.items() if task and not task.done()]})

    async def page_broadcast_campaign_save(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        group_ids = self._broadcast_group_ids_from_payload(payload)
        if not group_ids: return error_response("至少选择一个循环群发目标群。", status_code=400)
        message = str(payload.get("message") or "").strip()[:3000]
        if not message: return error_response("循环群发消息不能为空。", status_code=400)
        name = str(payload.get("name") or "循环群发").strip()[:80] or "循环群发"
        gap = max(1, min(86400, int(payload.get("group_interval_seconds", 1) or 1)))
        loop_minutes = max(1, min(525600, int(payload.get("loop_interval_minutes", 1) or 1)))
        campaign_id = int(payload.get("campaign_id") or 0)
        enabled = bool(payload.get("enabled", True))
        now = int(time.time())
        if campaign_id:
            ok = self.db.replace_broadcast_campaign(campaign_id, name, message, gap, loop_minutes, group_ids, enabled=enabled, next_run_at=now if enabled else 0)
            if not ok: return error_response("循环群发任务不存在。", status_code=404)
            old_task = self._broadcast_campaign_tasks.pop(campaign_id, None)
            if old_task and not old_task.done():
                old_task.cancel()
                try:
                    await old_task
                except asyncio.CancelledError:
                    pass
        else:
            campaign_id = self.db.create_broadcast_campaign(name, message, gap, loop_minutes, group_ids, next_run_at=now if enabled else 0)
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "broadcast_campaign_save", campaign_id, f"groups={','.join(group_ids)};gap={gap};loop={loop_minutes};enabled={enabled}")
        return json_response({"ok": True, "campaign_id": campaign_id, "message": "循环群发任务已保存，并将立即开始第一轮。" if enabled else "循环群发任务已保存但未启动。"})

    async def page_broadcast_campaign_toggle(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        cid = int(payload.get("campaign_id") or 0)
        enabled = bool(payload.get("enabled"))
        if cid <= 0: return error_response("campaign_id 无效。", status_code=400)
        next_at = int(time.time()) if enabled else 0
        if not self.db.set_broadcast_campaign_enabled(cid, enabled, next_at): return error_response("循环群发任务不存在。", status_code=404)
        if not enabled:
            task = self._broadcast_campaign_tasks.pop(cid, None)
            if task and not task.done(): task.cancel()
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "broadcast_campaign_toggle", cid, f"enabled={enabled}")
        return json_response({"ok": True, "message": "循环群发已启动。" if enabled else "循环群发已停止。"})

    async def page_broadcast_campaign_delete(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        cid = int(payload.get("campaign_id") or 0)
        if cid <= 0: return error_response("campaign_id 无效。", status_code=400)
        task = self._broadcast_campaign_tasks.pop(cid, None)
        if task and not task.done(): task.cancel()
        if not self.db.delete_broadcast_campaign(cid): return error_response("循环群发任务不存在。", status_code=404)
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "broadcast_campaign_delete", cid, "")
        return json_response({"ok": True, "message": "循环群发任务已删除。"})

    async def page_broadcast_save(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        gid = str(payload.get("group_id") or "").strip()
        if not gid: return error_response("group_id required", status_code=400)
        group = self.db.get_group(gid)
        if not group: return error_response("群组不存在，请先让该群发送一次消息让插件记录。", status_code=404)
        message = str(payload.get("message") if payload.get("message") is not None else self.config.get("group_broadcast_default_message", ""))[:3000]
        enabled = bool(payload.get("enabled", False))
        interval = max(1, min(10080, int(payload.get("interval_minutes", self.config.get("group_broadcast_default_interval_minutes", 120)) or 120)))
        next_at = int(time.time()) + interval * 60 if enabled else 0
        self.db.upsert_group_broadcast(gid, enabled, message, interval, next_at)
        self.db.add_admin_log(gid, self._web_username() or "web", "broadcast_save", None, f"enabled={enabled};interval={interval}")
        return json_response({"ok": True, "message": "群发配置已保存。"})

    async def page_broadcast_send_now(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        gid = str(payload.get("group_id") or "").strip()
        group = self.db.get_group(gid)
        if not group or not group["session_origin"]: return error_response("找不到群组会话来源。", status_code=404)
        row = self.db.get_group_broadcast(gid)
        raw_message = payload.get("message") if payload.get("message") is not None else (row["message"] if row else self.config.get("group_broadcast_default_message", ""))
        message = str(raw_message or "").strip()[:3000]
        if not message: return error_response("群发消息不能为空。", status_code=400)
        ok = await self._broadcast(str(group["session_origin"]), message, proactive=False)
        if not ok: return error_response("消息发送失败，请检查该群当前是否可发送消息。", status_code=502)
        interval = max(1, int(row["interval_minutes"] or 120)) if row else int(self.config.get("group_broadcast_default_interval_minutes", 120) or 120)
        self.db.mark_group_broadcast_sent(gid, int(time.time()) + interval * 60) if row else None
        self.db.add_admin_log(gid, self._web_username() or "web", "broadcast_send_now", None, message[:500])
        return json_response({"ok": True, "message": "已立即发送群发消息。"})

    async def page_settings(self):
        denied = self._require_web(False)
        if denied: return denied
        return json_response({"schema": self._schema, "config": self._safe_config()})

    async def page_save_settings(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            return error_response("changes 必须是对象。", status_code=400)
        allowed = set(self._schema)
        for key, value in changes.items():
            if key not in allowed:
                return error_response(f"不允许修改配置项：{key}", status_code=400)
            spec = self._schema[key]
            typ = spec.get("type")
            if spec.get("secret") and isinstance(value, str) and value == "********":
                continue
            if typ == "bool" and not isinstance(value, bool):
                return error_response(f"{key} 必须是布尔值", status_code=400)
            if typ == "int" and (not isinstance(value, int) or isinstance(value, bool)):
                return error_response(f"{key} 必须是整数", status_code=400)
            if typ == "float" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
                return error_response(f"{key} 必须是数字", status_code=400)
            if typ in {"string", "text"} and not isinstance(value, str):
                return error_response(f"{key} 必须是字符串", status_code=400)
            if typ == "list" and not isinstance(value, list):
                return error_response(f"{key} 必须是数组", status_code=400)
            if typ == "object" and not isinstance(value, dict):
                return error_response(f"{key} 必须是对象", status_code=400)
            if key == "cloud_selected_package_nos":
                if len(value) > 50 or any(not isinstance(x, str) or not x.strip() or len(x) > 64 for x in value):
                    return error_response("cloud_selected_package_nos 最多 50 个，每个编号最长 64 字符", status_code=400)
            slider = spec.get("slider")
            if slider and isinstance(value, (int, float)):
                if value < slider["min"] or value > slider["max"]:
                    return error_response(f"{key} 超出范围", status_code=400)
            if key in {"group_overrides_json", "shop_catalog_json", "event_catalog_json", "tutorial_pages_json", "boss_catalog_json"}:
                try:
                    json.loads(value or "{}")
                except Exception:
                    return error_response(f"{key} 不是有效 JSON", status_code=400)
        for key, value in changes.items():
            if self._schema[key].get("secret") and value == "********":
                continue
            self.config[key] = value
        menu_changes = {}
        if "help_menu_image_enabled" in changes:
            menu_changes["image_enabled"] = self._config_bool(changes["help_menu_image_enabled"], True)
        if "help_menu_image_first" in changes:
            menu_changes["image_first"] = self._config_bool(changes["help_menu_image_first"], True)
        if "help_menu_text_enabled" in changes:
            menu_changes["text_enabled"] = self._config_bool(changes["help_menu_text_enabled"], True)
        if "help_menu_settings" in changes and isinstance(changes["help_menu_settings"], dict):
            section = changes["help_menu_settings"]
            menu_changes.update({
                "image_enabled": self._config_bool(section.get("image_enabled", True), True),
                "text_enabled": self._config_bool(section.get("text_enabled", True), True),
                "image_first": self._config_bool(section.get("image_first", True), True),
            })
        if menu_changes:
            self._set_help_menu_config(**menu_changes)
        try:
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as exc:
            logger.warning("[群聊世界] 配置保存失败：%s", exc)
            return error_response("配置已写入内存，但保存到文件失败，请检查 AstrBot 权限。", status_code=500)
        self.db.add_admin_log("__SYSTEM__", self._web_username() or "web", "save_settings", None, f"keys={','.join(changes)}")
        return json_response({"saved": True, "config": self._safe_config()})

    async def page_group_action(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        group_id = str(payload.get("group_id") or "")
        action = str(payload.get("action") or "")
        if not group_id:
            return error_response("group_id required", status_code=400)
        row = self.db.get_group(group_id)
        if not row:
            self.db.upsert_group(group_id)
            row = self.db.get_group(group_id)
        if action in {"event", "boss_start", "boss_end", "spawn_npc", "end_event", "clear_npc", "toggle"} and not self._web_action_allowed_once(group_id, action):
            return json_response({"ok": False, "message": "操作正在处理中，请勿重复点击；如果仍需操作，请等待几秒后再试。", "duplicate": True})
        if action == "toggle":
            enabled = 0 if row["enabled"] else 1
            self.db.update_group(group_id, enabled=enabled)
            self.db.add_admin_log(group_id, self._web_username() or "web", "toggle", None, f"enabled={enabled}")
            return json_response({"ok": True, "message": "已更新群聊世界开关"})
        if action == "event":
            if not bool(self.engine.cfg("enable_auto_world_events", group_id, True)) or ("world_event_enabled" in row.keys() and not bool(row["world_event_enabled"])):
                return json_response({"ok": False, "message": "本群/全局已关闭世界事件，请先开启世界事件系统。"})
            text = self.engine.random_world_event(group_id)
            await self._broadcast(row["session_origin"] or "", "📢【管理员触发世界事件】\n" + text, proactive=False)
            self.db.add_admin_log(group_id, self._web_username() or "web", "event", None, text[:500])
            return json_response({"ok": True, "message": text, "broadcast": True})
        if action == "boss_start":
            text = self.engine.spawn_boss(group_id)
            await self._broadcast(row["session_origin"] or "", text, proactive=False)
            self.db.add_admin_log(group_id, self._web_username() or "web", "spawn_boss", None, text[:500])
            return json_response({"ok": True, "message": text, "broadcast": True})
        if action == "save_settings":
            raw_payload=payload.get("settings") if isinstance(payload.get("settings"),dict) else {}
            allowed={"world_weather","world_location","world_region_id","world_faction","world_event_enabled","explore_enabled","monster_enabled","monster_chance_percent","monster_max_count","monster_multi_chance_percent","npc_enabled","npc_chance_percent","npc_interval_minutes"}
            clean={}
            for k,v in raw_payload.items():
                if k not in allowed: continue
                if k in {"world_weather","world_location","world_region_id","world_faction"}: clean[k]=str(v)[:80]
                else:
                    try: clean[k]=int(v)
                    except Exception: return error_response(f"{k} 必须是整数",status_code=400)
            if "world_region_id" in clean and clean["world_region_id"] not in WORLD_REGIONS:
                return error_response("world_region_id 无效，请使用 /地图 中的地区。",status_code=400)
            if "world_faction" in clean and clean["world_faction"] not in {"王国","联盟","深渊","自然","中立"}:
                return error_response("world_faction 无效。",status_code=400)
            if "monster_chance_percent" in clean: clean["monster_chance_percent"]=max(0,min(100,clean["monster_chance_percent"]))
            if "monster_multi_chance_percent" in clean: clean["monster_multi_chance_percent"]=max(0,min(100,clean["monster_multi_chance_percent"]))
            if "monster_max_count" in clean: clean["monster_max_count"]=max(1,min(3,clean["monster_max_count"]))
            if "npc_chance_percent" in clean: clean["npc_chance_percent"]=max(0,min(100,clean["npc_chance_percent"]))
            if "npc_interval_minutes" in clean: clean["npc_interval_minutes"]=max(5,min(1440,clean["npc_interval_minutes"]))
            if clean:
                self.db.update_group(group_id,**clean)
            self.db.add_admin_log(group_id,self._web_username() or "web","group_settings",None,json.dumps(clean,ensure_ascii=False))
            return json_response({"ok":True,"message":"本群世界设置已保存。","group":dict(self.db.get_group(group_id))})
        if action == "spawn_npc":
            text=self.engine.spawn_npc(group_id)
            await self._broadcast(row["session_origin"] or "", text, proactive=False)
            self.db.add_admin_log(group_id,self._web_username() or "web","spawn_npc",None,text[:500])
            return json_response({"ok":True,"message":text,"broadcast":True})
        if action == "end_event":
            self.db.update_group(group_id,current_event_key=None,current_event_expires_at=0,current_event_effects_json="{}")
            self.db.add_admin_log(group_id,self._web_username() or "web","end_event",None,"clear current event")
            return json_response({"ok":True,"message":"当前世界事件已结束。"})
        if action == "clear_npc":
            self.db.clear_current_npc(group_id)
            self.db.add_admin_log(group_id,self._web_username() or "web","clear_npc",None,"clear current npc")
            return json_response({"ok":True,"message":"当前 NPC 已离开。"})

        if action == "boss_end":
            if not row["boss_active"]:
                return json_response({"ok": False, "message": "当前没有 Boss"})
            text = self.engine.finish_boss(group_id)
            self.db.add_admin_log(group_id, self._web_username() or "web", "finish_boss", None, text[:500])
            return json_response({"ok": True, "message": text})
        return error_response("未知群操作", status_code=400)

    async def page_player_action(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied:
            return denied
        user_id = str(payload.get("user_id") or "").strip()
        action = str(payload.get("action") or "")
        amount = int(payload.get("amount") or 0)
        if not user_id:
            return error_response("user_id required", status_code=400)
        player = self.db.get_player("__GLOBAL_USER__", user_id)
        if not player:
            return error_response("玩家不存在", status_code=404)
        admin = self._web_username() or "web"
        updated = None
        if action in {"grant_coins", "take_coins", "grant_gems"}:
            if amount <= 0:
                return error_response("amount 必须大于 0", status_code=400)
            coins = amount if action == "grant_coins" else (-amount if action == "take_coins" else 0)
            gems = amount if action == "grant_gems" else 0
            updated = self.db.wallet_change("__GLOBAL_USER__", user_id, coins, gems, kind="web_admin", note=f"Web管理员 {admin}")
            if not updated:
                return error_response("操作失败，余额不能为负", status_code=400)
        elif action in {"set_coins", "set_gems"}:
            if amount < 0:
                return error_response("数值不能小于 0", status_code=400)
            if action == "set_coins":
                updated = self.db.wallet_change("__GLOBAL_USER__", user_id, coins_delta=amount-int(player["coins"]), kind="web_admin_set", note=f"Web管理员 {admin} 设置金币")
            else:
                updated = self.db.wallet_change("__GLOBAL_USER__", user_id, gems_delta=amount-int(player["gems"]), kind="web_admin_set", note=f"Web管理员 {admin} 设置钻石")
            if not updated:
                return error_response("操作失败", status_code=400)
        elif action in {"ban", "unban"}:
            updated = self.db.set_global_player_fields(user_id, {"banned": 1 if action == "ban" else 0})
        elif action == "tutorial_reset":
            updated = self.db.set_global_player_fields(user_id, {"tutorial_status": "pending", "tutorial_step": 1})
        elif action == "tutorial_complete":
            updated = self.db.set_global_player_fields(user_id, {"tutorial_status": "completed", "tutorial_step": 10})
        elif action == "tutorial_skip":
            updated = self.db.set_global_player_fields(user_id, {"tutorial_status": "skipped", "tutorial_step": 0})
        elif action == "reset":
            with self.db.transaction() as conn:
                for table in ("inventory", "pets", "equipment", "achievements", "daily_tasks", "cooldowns", "player_skills", "skill_cooldowns"):
                    if table == "player_skills" or table == "skill_cooldowns":
                        conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
                    else:
                        conn.execute(f"DELETE FROM {table} WHERE group_id=? AND user_id=?", ("__GLOBAL_USER__", user_id))
                conn.execute(
                    "UPDATE players SET level=1,exp=0,coins=?,gems=?,stamina=?,max_stamina=?,profession='无职业',title='初出茅庐',streak=0,total_checkin=0,last_checkin=NULL,explore_count=0,explore_day='',active_pet_id=NULL,tutorial_status='pending',tutorial_step=1,total_explores=0,total_games=0,total_work=0,total_boss_damage=0,total_earned_coins=0,total_spent_coins=0,last_action_at=NULL,battle_attack=50,battle_defense=5,battle_crit_rate=8,battle_dodge_rate=3,battle_speed=100,battle_wins=0,battle_losses=0,battle_draws=0,battle_kills=0,battle_deaths=0,pvp_rating=1000,pvp_streak=0,death_state=0,respawn_at=0,revive_count=0,last_combat_group_id=NULL,last_combat_at=NULL,banned=0,updated_at=? WHERE group_id=? AND user_id=?",
                    (int(self.config.get("default_new_player_coins", 1000)), int(self.config.get("default_new_player_gems", 3)), int(self.config.get("max_stamina", 100)), int(self.config.get("max_stamina", 100)), datetime.now(timezone.utc).isoformat(timespec="seconds"), "__GLOBAL_USER__", user_id),
                )
            updated = self.db.get_player("__GLOBAL_USER__", user_id)
        elif action == "edit":
            fields = payload.get("fields")
            if not isinstance(fields, dict):
                return error_response("fields 必须是对象", status_code=400)
            clean = {}
            int_fields = {"level","exp","stamina","max_stamina","luck","renown","streak","total_checkin","tutorial_step","explore_count","total_explores","total_games","total_work","total_boss_damage","total_earned_coins","total_spent_coins","active_pet_id","hp","max_hp","battle_attack","battle_defense","battle_speed","battle_wins","battle_losses","battle_draws","battle_kills","battle_deaths","pvp_rating","pvp_streak","death_state","respawn_at","revive_count"}
            text_fields = {"name","profession","title","tutorial_status","last_checkin","protected_until","explore_day","last_action_at","last_combat_group_id","last_combat_at"}
            for key, value in fields.items():
                if key in int_fields:
                    if key == "active_pet_id" and (value is None or str(value).strip() == ""):
                        clean[key] = None
                    else:
                        try:
                            clean[key] = int(value)
                        except Exception:
                            return error_response(f"{key} 必须是整数", status_code=400)
                elif key in {"battle_crit_rate","battle_dodge_rate"}:
                    try: clean[key]=float(value)
                    except Exception: return error_response(f"{key} 必须是数字",status_code=400)
                elif key in text_fields:
                    clean[key] = str(value)[:80]
            if "tutorial_status" in clean and clean["tutorial_status"] not in {"pending","completed","skipped"}:
                return error_response("tutorial_status 无效", status_code=400)
            if not clean:
                return error_response("没有可修改字段", status_code=400)
            # coins/gems use wallet_change so balances remain auditable.
            if "coins" in fields:
                target_coins=max(0,int(fields["coins"]))
                updated=self.db.wallet_change("__GLOBAL_USER__", user_id, coins_delta=target_coins-int(player["coins"]), kind="web_admin_set", note=f"Web管理员 {admin} 设置金币")
                if not updated: return error_response("金币设置失败", status_code=400)
                player=updated
            if "gems" in fields:
                target_gems=max(0,int(fields["gems"]))
                updated=self.db.wallet_change("__GLOBAL_USER__", user_id, gems_delta=target_gems-int(player["gems"]), kind="web_admin_set", note=f"Web管理员 {admin} 设置钻石")
                if not updated: return error_response("钻石设置失败", status_code=400)
            if clean:
                updated=self.db.set_global_player_fields(user_id, clean)
            else:
                updated=self.db.get_player("__GLOBAL_USER__", user_id)
        elif action == "group_remove":
            group_id=str(payload.get("group_id") or "").strip()
            if not group_id:
                return error_response("group_id required", status_code=400)
            self.db.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?", (group_id,user_id))
            updated=self.db.get_player("__GLOBAL_USER__",user_id)
        else:
            return error_response("未知玩家操作", status_code=400)
        self.db.add_admin_log("__GLOBAL_USER__", admin, action, user_id, f"amount={amount}")
        return json_response({"ok": True, "message": "玩家操作成功，已按全局角色数据更新。", "player": dict(updated) if updated else None})

    def _build_data_snapshot(self, group_id: str = "") -> dict[str, Any]:
        """Build a portable, versioned SQLite snapshot for backup/export/import."""
        tables = {
            "groups": "groups", "players": "players", "inventory": "inventory", "pets": "pets",
            "equipment": "equipment", "transactions": "transactions", "daily_tasks": "daily_tasks",
            "achievements": "achievements", "cooldowns": "cooldowns", "game_sessions": "game_sessions",
            "boss_damage": "boss_damage", "admin_logs": "admin_logs", "message_stats": "message_stats",
            "group_members": "group_members", "invite_codes": "invite_codes", "invite_records": "invite_records",
            "auto_battles": "auto_battles", "world_event_logs": "world_event_logs",
            "monster_encounters": "monster_encounters", "group_broadcasts": "group_broadcasts", "player_skills": "player_skills",
            "skill_cooldowns": "skill_cooldowns", "npc_interactions": "npc_interactions", "action_logs": "action_logs",
        }
        snapshot: dict[str, Any] = {
            "format": "astrbot_plugin_group_world_snapshot",
            "snapshot_version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "author": "ysgl",
            "plugin": PLUGIN_NAME,
            "config": self._safe_config(),
            "summary": self.db.get_dashboard_summary(group_id or None),
            "tables": {},
        }
        for label, table in tables.items():
            if label == "groups":
                rows = self.db.get_group_details(500) if not group_id else [self.db.get_group(group_id)]
                rows = [r for r in rows if r]
            elif group_id and table in {"world_event_logs", "admin_logs", "message_stats", "group_members", "monster_encounters", "npc_interactions", "action_logs", "boss_damage", "game_sessions", "auto_battles", "group_broadcasts"}:
                rows = self.db.fetchall(f"SELECT * FROM {table} WHERE group_id=? ORDER BY 1 DESC LIMIT 5000", (group_id,))
            elif table in {"players", "inventory", "pets", "equipment", "transactions", "daily_tasks", "achievements", "cooldowns", "player_skills", "skill_cooldowns", "invite_codes", "invite_records"}:
                # Player-owned tables are global in current versions.
                if table == "invite_codes":
                    rows = self.db.fetchall("SELECT * FROM invite_codes ORDER BY created_at DESC LIMIT 5000")
                elif table == "invite_records":
                    rows = self.db.fetchall("SELECT * FROM invite_records ORDER BY created_at DESC LIMIT 5000")
                elif table in {"player_skills", "skill_cooldowns"}:
                    rows = self.db.fetchall(f"SELECT * FROM {table} LIMIT 5000")
                else:
                    rows = self.db.fetchall(f"SELECT * FROM {table} WHERE group_id='__GLOBAL_USER__' LIMIT 10000")
            else:
                rows = self.db.fetchall(f"SELECT * FROM {table} LIMIT 10000")
            snapshot["tables"][label] = [dict(r) for r in rows]
        # Retain the older top-level keys for backwards compatibility with V1.5 exports.
        snapshot["groups"] = snapshot["tables"].get("groups", [])
        snapshot["players"] = snapshot["tables"].get("players", [])
        snapshot["transactions"] = snapshot["tables"].get("transactions", [])
        snapshot["events"] = snapshot["tables"].get("world_event_logs", [])
        snapshot["admin_logs"] = snapshot["tables"].get("admin_logs", [])
        return snapshot

    async def page_data_export(self):
        denied = self._require_web(False)
        if denied: return denied
        group_id = str(request.query.get("group_id") or "") if request else ""
        snapshot = self._build_data_snapshot(group_id)
        target = self.data_dir / f"world-export-{int(time.time())}.json"
        target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        if file_response:
            return file_response(target, filename=target.name, content_type="application/json")
        return json_response(snapshot)

    def _validate_import_config(self, imported: Any) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(imported, dict):
            return {}, ["快照中的 config 不是对象，已跳过配置导入。"]
        changes: dict[str, Any] = {}
        warnings: list[str] = []
        for key, value in imported.items():
            spec = self._schema.get(key)
            if not spec:
                continue
            if spec.get("secret") and value == "********":
                continue
            typ = spec.get("type")
            valid = True
            if typ == "bool": valid = isinstance(value, bool)
            elif typ == "int": valid = isinstance(value, int) and not isinstance(value, bool)
            elif typ == "float": valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            elif typ in {"string", "text"}: valid = isinstance(value, str)
            elif typ == "list": valid = isinstance(value, list)
            if not valid:
                warnings.append(f"{key} 类型不匹配，已跳过")
                continue
            slider = spec.get("slider")
            if slider and isinstance(value, (int, float)) and not (slider["min"] <= value <= slider["max"]):
                warnings.append(f"{key} 超出范围，已跳过")
                continue
            if key in {"group_overrides_json", "shop_catalog_json", "event_catalog_json", "tutorial_pages_json", "boss_catalog_json", "tip_catalog_json"}:
                try:
                    json.loads(value or "{}")
                except Exception:
                    warnings.append(f"{key} JSON 无效，已跳过")
                    continue
            changes[key] = value
        return changes, warnings

    async def page_data_import(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        raw = payload.get("snapshot", payload.get("data"))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception as exc:
                return error_response(f"导入文件不是有效 JSON：{exc}", status_code=400)
        if not isinstance(raw, dict):
            return error_response("snapshot 必须是 JSON 对象。", status_code=400)
        if raw.get("format") and raw.get("format") != "astrbot_plugin_group_world_snapshot":
            return error_response("不是群聊世界数据快照，已拒绝导入。", status_code=400)
        tables = dict(raw.get("tables")) if isinstance(raw.get("tables"), dict) else {}
        # Accept legacy V1.5 top-level exports too.
        legacy_map = {"groups":"groups", "players":"players", "transactions":"transactions", "events":"world_event_logs", "admin_logs":"admin_logs"}
        for old_key, table_name in legacy_map.items():
            if table_name not in tables and isinstance(raw.get(old_key), list):
                tables[table_name] = raw[old_key]
        allowed_tables = {
            "groups","players","inventory","pets","equipment","transactions","daily_tasks","achievements","cooldowns",
            "game_sessions","boss_damage","admin_logs","message_stats","group_members","invite_codes","invite_records",
            "auto_battles","world_event_logs","monster_encounters","group_broadcasts","player_skills","skill_cooldowns","npc_interactions","action_logs",
        }
        selected = [(t, rows) for t, rows in tables.items() if t in allowed_tables and isinstance(rows, list)]
        import_config = bool(payload.get("import_config", True))
        config_changes, config_warnings = self._validate_import_config(raw.get("config")) if import_config else ({}, [])
        if not selected and not config_changes:
            return error_response("没有找到可导入的数据表或有效配置。", status_code=400)
        # Create a safety backup before changing any rows or config.
        backup = self._build_data_snapshot("")
        backup_path = self.data_dir / f"pre-import-backup-{int(time.time())}.json"
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        imported = 0
        skipped = 0
        for table, rows in selected:
            actual_cols = [r[1] for r in self.db.conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if not actual_cols:
                skipped += len(rows)
                continue
            with self.db.transaction() as conn:
                for row in rows[:20000]:
                    if not isinstance(row, dict):
                        skipped += 1
                        continue
                    payload_cols = [c for c in row.keys() if c in actual_cols]
                    if not payload_cols:
                        skipped += 1
                        continue
                    placeholders = ",".join("?" for _ in payload_cols)
                    cols_sql = ",".join(payload_cols)
                    values = [row[c] for c in payload_cols]
                    try:
                        conn.execute(f"INSERT OR REPLACE INTO {table} ({cols_sql}) VALUES ({placeholders})", values)
                        imported += 1
                    except Exception:
                        skipped += 1
        if config_changes:
            self.config.update(config_changes)
            try:
                save = getattr(self.config, "save_config", None)
                if callable(save):
                    save()
            except Exception as exc:
                logger.warning("[群聊世界] 导入配置保存失败：%s", exc)
                config_warnings.append("数据库已导入，但配置保存到文件失败，请检查 AstrBot 权限。")
        self.db.add_admin_log(
            "__SYSTEM__", self._web_username() or "web", "data_import", None,
            f"imported={imported},skipped={skipped},config={len(config_changes)},backup={backup_path.name}",
        )
        warning_text = ("；".join(config_warnings)) if config_warnings else ""
        msg = f"导入完成：{imported} 行，跳过 {skipped} 行。"
        if config_changes:
            msg += f" 已恢复 {len(config_changes)} 项配置。"
        if warning_text:
            msg += f" 提示：{warning_text}"
        return json_response({
            "ok": True, "message": msg, "imported": imported, "skipped": skipped,
            "config_imported": len(config_changes), "warnings": config_warnings, "backup": backup_path.name,
        })

    async def page_cloud_status(self):
        denied = self._require_web(False)
        if denied:
            return denied
        enabled = bool(self.config.get("cloud_enabled", False))
        has_key = bool(str(self.config.get("cloud_api_key") or "").strip())
        configured = has_key
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        remote = {
            "configured": configured,
            "enabled": enabled,
            "reachable": False,
            "message": "未配置云端 API Key" if not has_key else ("已配置 API Key，但云端玩法未启用" if not enabled else "正在连接云端"),
        }
        if configured:
            self._cloud_client = CloudClient(
                str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
                str(self.config.get("cloud_api_key") or ""),
                self._cloud_cache_path,
                int(self.config.get("cloud_timeout_seconds", 12) or 12),
            )
            remote = await self._cloud_client.status()
            if remote.get("reachable") and enabled:
                data = await self._cloud_sync_if_due(force=False)
        return self._cloud_status_payload(remote, data)

    def _cloud_status_payload(self, remote: dict[str, Any], data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data if isinstance(data, dict) else (self._cloud_data if isinstance(self._cloud_data, dict) else {})
        return {
            **remote,
            "configured": bool(str(self.config.get("cloud_api_key") or "").strip()),
            "enabled": bool(self.config.get("cloud_enabled", False)),
            "base_url": self._cloud_client.base_url if getattr(self, "_cloud_client", None) else str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            "last_sync_at": data.get("synced_at", 0),
            "counts": {
                "bosses": len(data.get("bosses", []) if isinstance(data.get("bosses"), list) else []),
                "products": len(data.get("products", []) if isinstance(data.get("products"), list) else []),
                "tutorials": len(data.get("tutorials", {}) if isinstance(data.get("tutorials"), dict) else {}),
                "monsters": len(data.get("monsters", []) if isinstance(data.get("monsters"), list) else []),
                "npcs": len(data.get("npcs", []) if isinstance(data.get("npcs"), list) else []),
                "crafting_recipes": len(data.get("crafting_recipes", {}) if isinstance(data.get("crafting_recipes"), dict) else {}),
                "bounty_templates": len(data.get("bounty_templates", []) if isinstance(data.get("bounty_templates"), list) else []),
                "global_boss": 1 if isinstance(data.get("global_boss_profile"), dict) and data.get("global_boss_profile") else 0,
                "community_packages": len(data.get("community_packages", []) if isinstance(data.get("community_packages"), list) else []),
            },
            "active_merge_counts": self._cloud_active_merge_counts(),
            "community_enabled": bool(self.config.get("cloud_use_community_content", True)),
            "package_sync_enabled": bool(self.config.get("cloud_sync_packages", True)),
            "cache_file": self._cloud_cache_path.name,
            "community_packages": data.get("community_packages", []) if isinstance(data.get("community_packages"), list) else [],
            "selected_package_nos": self._cloud_selected_package_nos(),
            "active_packages": data.get("selected_packages", []) if isinstance(data.get("selected_packages"), list) else [],
            "products_catalog": data.get("products", []) if isinstance(data.get("products"), list) else [],
        }

    async def page_cloud_portal(self):
        denied = self._require_web(False)
        if denied:
            return denied
        has_key = bool(str(self.config.get("cloud_api_key") or "").strip())
        enabled = bool(self.config.get("cloud_enabled", False))
        self._cloud_client = CloudClient(
            str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            str(self.config.get("cloud_api_key") or ""),
            self._cloud_cache_path,
            int(self.config.get("cloud_timeout_seconds", 12) or 12),
        )
        if not has_key:
            return json_response({
                "ok": True, "configured": False, "enabled": enabled, "reachable": False,
                "message": "请先在下面填写 ysgl 网站生成的完整 API Key。",
                "base_url": self._cloud_client.base_url,
                "site": {}, "announcements": [],
                **self._cloud_status_payload({"configured": False, "reachable": False, "message": "未配置云端 API Key"}),
            })
        remote = await self._cloud_client.status()
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        if remote.get("reachable") and enabled:
            data = await self._cloud_sync_if_due(force=False)
        site = {}
        announcements = []
        if remote.get("reachable"):
            try:
                site_data = await self._cloud_client.site_info()
                site = site_data.get("site") if isinstance(site_data.get("site"), dict) else {}
            except Exception as exc:
                logger.debug("[群聊世界] 云端站点信息读取失败：%s", exc)
            try:
                announcements = await self._cloud_client.announcements()
            except Exception as exc:
                logger.debug("[群聊世界] 云端公告读取失败：%s", exc)
        payload = self._cloud_status_payload(remote, data)
        payload.update({"ok": True, "site": site, "announcements": announcements})
        return json_response(payload)

    async def page_cloud_announcements(self):
        denied = self._require_web(False)
        if denied:
            return denied
        self._cloud_client = CloudClient(
            str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            str(self.config.get("cloud_api_key") or ""),
            self._cloud_cache_path,
            int(self.config.get("cloud_timeout_seconds", 12) or 12),
        )
        rows = await self._cloud_client.announcements()
        return json_response({"ok": True, "announcements": rows})

    async def page_cloud_sync(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        data = await self._cloud_sync_if_due(force=True)
        return json_response({"ok": True, "message": "云端同步完成。", "data": data, "selected_package_nos": self._cloud_selected_package_nos(), "counts": {k: len(data.get(k, [] if k not in ("tutorials","crafting_recipes") else {})) for k in ("bosses","products","tutorials","monsters","npcs","crafting_recipes")}})

    async def page_cloud_package_preview(self):
        payload = await request.json(default={}) if request else {}
        package_id = int(payload.get("package_id") or 0)
        package_no = str(payload.get("package_no") or "").strip()
        denied = self._require_web(False)
        if denied: return denied
        if package_id <= 0 and not package_no:
            return json_response({"ok": False, "message": "缺少 JSON 数据包编号"}, status_code=400)
        self._cloud_client = CloudClient(str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL), str(self.config.get("cloud_api_key") or ""), self._cloud_cache_path, int(self.config.get("cloud_timeout_seconds", 12) or 12))
        package = await self._cloud_client.package(package_id, package_no)
        if not package:
            return json_response({"ok": False, "message": "JSON 数据包不存在或尚未审核通过"}, status_code=404)
        # Preview never imports or activates the package.
        return json_response({"ok": True, "package": package})

    async def page_cloud_import_package(self):
        payload = await request.json(default={}) if request else {}
        token = str(payload.get("token") or "")
        denied = self._require_web(True, token)
        if denied: return denied
        package_id = int(payload.get("package_id") or 0)
        if package_id <= 0:
            return json_response({"ok": False, "message": "缺少 JSON 数据包 ID"}, status_code=400)
        self._cloud_client = CloudClient(str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL), str(self.config.get("cloud_api_key") or ""), self._cloud_cache_path, int(self.config.get("cloud_timeout_seconds", 12) or 12))
        package = await self._cloud_client.package(package_id)
        if not package:
            return json_response({"ok": False, "message": "JSON 数据包不存在或当前 Key 无权获取"}, status_code=404)
        counts = self._merge_cloud_package_into_cache(package)
        self._cloud_data["synced_at"] = int(time.time())
        self._cloud_cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cloud_cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._cloud_data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._cloud_cache_path)
        self._apply_cloud_data_to_engine()
        return json_response({"ok": True, "message": "已获取并加入云端缓存。未知 JSON 字段不会自动执行。", "package": package, "merged": counts})

    async def page_cloud_test(self):
        denied = self._require_web(False)
        if denied: return denied
        self._cloud_client = CloudClient(str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL), str(self.config.get("cloud_api_key") or ""), self._cloud_cache_path, int(self.config.get("cloud_timeout_seconds", 12) or 12))
        result = await self._cloud_client.status()
        return json_response(result)

    async def page_cloud_local_export(self):
        denied = self._require_web(False)
        if denied: return denied
        def parse_cfg(key):
            try:
                value = self.config.get(key, "{}")
                return json.loads(value or "{}")
            except Exception:
                return {}
        export = {
            "format": "astrbot_group_world_custom_package",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "astrbot_local_config",
            "base_url": str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            "products": [{"item_no": str(k), "name": v[0], "price": int(v[1]), "description": v[2], "effect": {}} for k,v in parse_cfg("shop_catalog_json").items() if isinstance(v,list) and len(v)>=3],
            "bosses": [{"id": str(k), **v} for k,v in parse_cfg("boss_catalog_json").items() if isinstance(v,dict) and v.get("name")],
            "tutorials": parse_cfg("tutorial_pages_json"),
            "monsters": [{"id": str(k), **v} for k,v in parse_cfg("monster_catalog_json").items() if isinstance(v,dict) and v.get("name")],
            "packages": [],
            "selected_package_nos": self._cloud_selected_package_nos(),
        }
        target = self.data_dir / f"local-custom-package-{int(time.time())}.json"
        target.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        if file_response:
            return file_response(target, filename=target.name, content_type="application/json")
        return json_response(export)

    async def page_cloud_export(self):
        denied = self._require_web(False)
        if denied: return denied
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        export = {
            "format": "astrbot_group_world_cloud_package",
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "cloud-cache",
            "base_url": str(self.config.get("cloud_base_url") or CLOUD_DEFAULT_URL),
            "bosses": data.get("bosses", []),
            "products": data.get("products", []),
            "tutorials": data.get("tutorials", {}),
            "monsters": data.get("monsters", []),
            "npcs": data.get("npcs", []),
            "bounty_templates": data.get("bounty_templates", []),
            "global_boss": data.get("global_boss_profile", {}),
            "community_packages": data.get("community_packages", []),
            "custom_json_packages": data.get("custom_json_packages", []),
        }
        target = self.data_dir / f"cloud-package-{int(time.time())}.json"
        target.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        if file_response:
            return file_response(target, filename=target.name, content_type="application/json")
        return json_response(export)

    async def page_world_settings(self):
        denied=self._require_web(False)
        if denied:return denied
        group_id=str(request.query.get("group_id") or "").strip() if request else ""
        if not group_id:return error_response("缺少 group_id。",status_code=400)
        group=self.db.get_group(group_id)
        if not group:
            group=self.db.upsert_group(group_id)
        regions=[{"id":rid,**dict(info)} for rid,info in WORLD_REGIONS.items()]
        factions=["王国","联盟","深渊","自然","中立"]
        return json_response({"ok":True,"group":dict(group),"regions":regions,"factions":factions})

    async def page_world_settings_save(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        group_id=str(payload.get("group_id") or "").strip()
        if not group_id:return error_response("缺少 group_id。",status_code=400)
        group=self.db.get_group(group_id) or self.db.upsert_group(group_id)
        settings=payload.get("settings") or {}
        if not isinstance(settings,dict):return error_response("settings 必须是对象。",status_code=400)
        rid=str(settings.get("world_region_id") or settings.get("region_id") or "").strip()
        if rid not in WORLD_REGIONS: return error_response("世界地区不存在，请从地区列表中选择。",status_code=400)
        faction=str(settings.get("world_faction") or "中立").strip()
        if faction not in {"王国","联盟","深渊","自然","中立"}: return error_response("阵营无效。",status_code=400)
        weather=str(settings.get("world_weather") or "晴朗").strip()[:80]
        location=str(settings.get("world_location") or WORLD_REGIONS[rid]["name"]).strip()[:120]
        try:
            fields={
                "world_region_id":rid,"world_faction":faction,"world_weather":weather,"world_location":location,
                "world_event_enabled":1 if bool(settings.get("world_event_enabled",1)) else 0,
                "explore_enabled":1 if bool(settings.get("explore_enabled",1)) else 0,
                "monster_enabled":1 if bool(settings.get("monster_enabled",1)) else 0,
                "monster_chance_percent":max(0,min(35,int(settings.get("monster_chance_percent",16)))),
                "monster_max_count":max(1,min(2,int(settings.get("monster_max_count",2)))),
                "monster_multi_chance_percent":max(0,min(35,int(settings.get("monster_multi_chance_percent",18)))),
                "npc_enabled":1 if bool(settings.get("npc_enabled",1)) else 0,
                "npc_chance_percent":max(0,min(35,int(settings.get("npc_chance_percent",10)))),
                "npc_interval_minutes":max(5,min(1440,int(settings.get("npc_interval_minutes",120)))),
            }
        except Exception:return error_response("世界设置包含无效数字。",status_code=400)
        self.db.update_group(group_id,**fields)
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','world_settings_save',str(group_id),json.dumps(fields,ensure_ascii=False,separators=(',',':')))
        return json_response({"ok":True,"message":"世界设置已保存。","group":dict(self.db.get_group(group_id))})

    def _build_bounty_reward_catalog(self) -> list[dict[str, Any]]:
        """Build a safe bounty reward catalog from every usable in-plugin/cloud asset.

        Sources include:
        - built-in ITEM_INFO items;
        - actual monster drop materials from built-in/local/cloud monster catalogs;
        - built-in/local/cloud shop products;
        - equipment blueprints from built-in/local/cloud crafting recipes.
        Cloud entries are descriptive data only and never execute code.
        """
        from .core.engine import ITEM_INFO, SHOP, MONSTER_TEMPLATES, CRAFTING_RECIPES

        catalog: dict[str, dict[str, Any]] = {}

        def add_item(iid: str, name: str, desc: str, source: str, category: str = "材料/道具",
                     reward_type: str = "item", **extra: Any) -> None:
            iid = str(iid or "").strip()[:96]
            if not iid:
                return
            row = catalog.get(iid)
            priority = 0 if source.startswith("插件内置") else (1 if source.startswith("本地") else 2)
            if row and int(row.get("_priority", 0)) < priority:
                return
            value = {
                "item_id": iid,
                "name": str(name or iid)[:120],
                "description": str(desc or "可作为悬赏奖励。")[:300],
                "source": str(source)[:80],
                "category": str(category)[:60],
                "reward_type": reward_type,
                "_priority": priority,
            }
            value.update(extra)
            catalog[iid] = value

        # 1) Built-in backpack assets.
        for iid, (name, desc) in ITEM_INFO.items():
            add_item(iid, name, desc, "插件内置 · 道具", "道具")

        # 2) Built-in shop assets.
        for iid, val in SHOP.items():
            if isinstance(val, (tuple, list)) and len(val) >= 3:
                add_item(iid, val[0], val[2], "插件内置 · 商店", "商店商品")

        def parse_catalog(raw: Any) -> dict[str, Any]:
            try:
                if isinstance(raw, str):
                    value = json.loads(raw or "{}")
                else:
                    value = raw
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}

        # 3) Monster drop materials. This is intentionally separate from the monster
        # itself: the reward picker should show the actual drops users recognize.
        def ingest_monsters(monsters: Any, source: str) -> None:
            if isinstance(monsters, list):
                monsters = {str(x.get("id") or x.get("code") or x.get("name") or f"monster_{i}"): x for i, x in enumerate(monsters) if isinstance(x, dict)}
            if not isinstance(monsters, dict):
                return
            for mid, monster in list(monsters.items())[:1000]:
                if not isinstance(monster, dict):
                    continue
                mname = str(monster.get("name") or mid)[:80]
                drops = monster.get("items")
                if not drops:
                    drops = monster.get("drops") or monster.get("drop_items") or monster.get("loot")
                entries = []
                if isinstance(drops, dict):
                    entries = list(drops.items())
                elif isinstance(drops, list):
                    for drop in drops[:50]:
                        if isinstance(drop, str):
                            entries.append((drop, 1))
                        elif isinstance(drop, dict):
                            did = drop.get("item_id") or drop.get("id") or drop.get("code") or drop.get("item_no")
                            if did:
                                entries.append((did, drop.get("qty") or drop.get("quantity") or 1))
                for drop_id, qty in entries[:50]:
                    drop_id = str(drop_id).strip()
                    if not drop_id:
                        continue
                    fallback = ITEM_INFO.get(drop_id, (drop_id, "怪物掉落材料"))
                    add_item(drop_id, fallback[0], fallback[1], f"{source} · 怪物：{mname}", "怪物材料")

        ingest_monsters(dict(MONSTER_TEMPLATES), "插件内置")
        ingest_monsters(parse_catalog(self.config.get("monster_catalog_json", "{}")), "本地自定义")
        ingest_monsters(parse_catalog(self.config.get("cloud_monster_catalog_json", "{}")), "云端同步")

        # 4) Local/cloud shop product catalogs.
        for cfg_key, label in (("shop_catalog_json", "本地自定义 · 商店"), ("cloud_shop_catalog_json", "云端同步 · 商店")):
            for iid, val in parse_catalog(self.config.get(cfg_key, "{}")).items():
                if isinstance(val, (tuple, list)) and len(val) >= 3:
                    add_item(iid, val[0], val[2], label, "商店商品")

        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        products = data.get("products", [])
        if isinstance(products, dict):
            products = [{"id": k, **(v if isinstance(v, dict) else {"name": v})} for k, v in products.items()]
        elif not isinstance(products, list):
            products = []
        for item in products[:1500]:
            if not isinstance(item, dict):
                continue
            iid = str(item.get("item_no") or item.get("item_id") or item.get("id") or item.get("code") or "").strip()
            if not iid:
                continue
            add_item(iid, item.get("name") or iid, item.get("description") or item.get("desc") or "云端自定义商品，可作为悬赏物品。",
                     "YSGL 云端 · 商店", "云端商品")
        cloud_items = data.get("items") or data.get("reward_items") or data.get("materials")
        if isinstance(cloud_items, dict):
            for iid, item in list(cloud_items.items())[:1500]:
                if isinstance(item, dict):
                    add_item(iid, item.get("name") or iid, item.get("description") or item.get("desc") or "云端同步材料/道具。", "YSGL 云端 · 材料", str(item.get("category") or "云端材料"))
                else:
                    add_item(iid, str(item), "云端同步材料/道具。", "YSGL 云端 · 材料", "云端材料")

        # 5) Equipment blueprints. The reward becomes a real equipment record instead
        # of a fake inventory item, so players can equip it immediately after winning.
        def ingest_recipes(recipes: dict[str, Any], source: str) -> None:
            for rid, recipe in list(recipes.items())[:500]:
                if not isinstance(recipe, dict) or not recipe.get("name"):
                    continue
                rid = str(rid).strip()
                if not rid:
                    continue
                equip_id = f"equipment:{rid}"
                add_item(
                    equip_id, recipe.get("name"), recipe.get("desc") or "可直接作为装备悬赏奖励。", source, "装备",
                    reward_type="equipment", equipment_recipe_id=rid, slot=str(recipe.get("slot") or "主手"),
                    rarity=str(recipe.get("rarity") or "普通"), level=max(1, min(100, int(recipe.get("level", 1) or 1))),
                    attack=max(0, min(300, int(recipe.get("attack", 0) or 0))),
                    defense=max(0, min(200, int(recipe.get("defense", 0) or 0))),
                    explore_bonus=max(0, min(100, int(recipe.get("explore_bonus", 0) or 0))),
                    revive_chance=max(0, min(100, int(recipe.get("revive_chance", 0) or 0))),
                )

        ingest_recipes(dict(CRAFTING_RECIPES), "插件内置 · 装备配方")
        ingest_recipes(parse_catalog(self.config.get("crafting_recipe_json", "{}")), "本地自定义 · 装备配方")
        ingest_recipes(parse_catalog(self.config.get("cloud_crafting_recipe_json", "{}")), "云端同步 · 装备配方")
        cached_recipes = data.get("crafting_recipes")
        if isinstance(cached_recipes, dict):
            ingest_recipes(cached_recipes, "YSGL 云端 · 装备配方")
        for equipment_key in ("equipment", "equipments", "equipment_catalog", "equipments_catalog"):
            cached_equipment = data.get(equipment_key)
            if isinstance(cached_equipment, list):
                cached_equipment = {str(x.get("id") or x.get("code") or x.get("item_id") or i): x for i, x in enumerate(cached_equipment) if isinstance(x, dict)}
            if isinstance(cached_equipment, dict):
                ingest_recipes(cached_equipment, f"YSGL 云端 · {equipment_key}")

        out = []
        for row in catalog.values():
            row.pop("_priority", None)
            out.append(row)
        return sorted(out, key=lambda x: (str(x.get("category", "")), str(x.get("source", "")), str(x.get("name", ""))))

    async def page_worldplus_bounty_catalog(self):
        denied=self._require_web(False)
        if denied:return denied
        refresh=str(request.query.get("refresh") or "0") == "1" if request else False
        sync_message=""
        if refresh and bool(self.config.get("cloud_enabled",False)) and str(self.config.get("cloud_api_key") or "").strip():
            try:
                result=await self._cloud_sync_if_due(force=True)
                sync_message="云端目录已刷新。" if result.get("ok",True) else "云端刷新返回异常，使用本地缓存目录。"
            except Exception as exc:
                sync_message=f"云端刷新失败，已改用本地缓存：{str(exc)[:120]}"
        catalog=self._build_bounty_reward_catalog()
        return json_response({"ok":True,"catalog":catalog,"updated_at":int(time.time()),"refresh":refresh,"message":sync_message or "奖励目录已读取。"})

    async def page_worldplus(self):
        """Single Page bootstrap endpoint for World+; avoids split-route failures on older Dashboard builds."""
        denied = self._require_web(False)
        if denied:
            return denied
        self.db.expire_bounties()
        bounties = []
        for row in self.db.get_bounties(None, 100):
            item = dict(row)
            try:
                item["reward_items"] = json.loads(str(item.get("reward_items_json") or "[]"))
            except Exception:
                item["reward_items"] = []
            bounties.append(item)
        boss = dict(self.db.get_global_boss())
        ranking = [dict(x) for x in self.db.get_global_boss_ranking(20)]
        catalog = self._build_bounty_reward_catalog()
        return json_response({
            "ok": True,
            "bounties": bounties,
            "global_boss": boss,
            "global_boss_ranking": ranking,
            "catalog": catalog,
            "config": {k: self.config.get(k) for k in (
                'bounty_enabled','bounty_default_duration_hours','bounty_challenge_timeout_seconds','bounty_player_daily_limit',
                'bounty_player_max_open','bounty_min_coins','bounty_max_coins','bounty_publish_fee_coins','bounty_same_target_cooldown_seconds',
                'world_map_enabled','world_travel_enabled','world_travel_base_cost','world_travel_cooldown_seconds','world_reputation_enabled',
                'global_boss_enabled','global_boss_auto_spawn','global_boss_interval_hours','global_boss_name','global_boss_description',
                'global_boss_max_hp','global_boss_attack','global_boss_defense','global_boss_skill_chance_percent','global_boss_duration_hours',
                'global_boss_attack_cooldown_seconds','global_boss_stamina_cost','global_boss_participation_reward',
                'global_boss_reward_pool_coins','global_boss_reward_pool_gems','global_boss_exp_per_1000_damage',
                'global_boss_enrage_threshold_percent','global_boss_enrage_multiplier','global_boss_profile_json'
            )}
        })

    async def page_worldplus_overview(self):
        denied=self._require_web(False)
        if denied:return denied
        self.db.expire_bounties()
        bounties=[]
        for row in self.db.get_bounties(None,100):
            item=dict(row)
            try:item["reward_items"]=json.loads(str(item.get("reward_items_json") or "[]"))
            except Exception:item["reward_items"]=[]
            bounties.append(item)
        boss=dict(self.db.get_global_boss())
        ranking=[dict(x) for x in self.db.get_global_boss_ranking(20)]
        return json_response({'ok':True,'bounties':bounties,'global_boss':boss,'global_boss_ranking':ranking,'config':{k:self.config.get(k) for k in ('bounty_enabled','bounty_default_duration_hours','bounty_challenge_timeout_seconds','bounty_player_daily_limit','bounty_player_max_open','bounty_min_coins','bounty_max_coins','bounty_publish_fee_coins','bounty_same_target_cooldown_seconds','world_map_enabled','world_travel_enabled','world_travel_base_cost','world_travel_cooldown_seconds','world_reputation_enabled','global_boss_enabled','global_boss_auto_spawn','global_boss_interval_hours','global_boss_name','global_boss_description','global_boss_max_hp','global_boss_attack','global_boss_defense','global_boss_skill_chance_percent','global_boss_duration_hours','global_boss_attack_cooldown_seconds','global_boss_stamina_cost','global_boss_participation_reward','global_boss_reward_pool_coins','global_boss_reward_pool_gems','global_boss_exp_per_1000_damage','global_boss_enrage_threshold_percent','global_boss_enrage_multiplier','global_boss_profile_json')}})

    async def page_worldplus_bounty_create(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        target_ref=str(payload.get('target_user') or '').strip()
        if not target_ref:return error_response('请填写目标玩家 UID / 用户 ID。',status_code=400)
        target=self.db.find_player_by_uid(target_ref.upper())
        if not target: target=self.db.get_player('__GLOBAL_USER__',target_ref)
        if not target:return error_response('找不到目标玩家，请输入正确的玩家 UID 或平台用户 ID。',status_code=404)
        if int(target['banned'] or 0):return error_response('目标玩家已被封禁，不能作为悬赏目标。',status_code=400)
        title=str(payload.get('title') or '与指定冒险者进行正式决斗并取胜').strip()[:120]
        desc=str(payload.get('description') or '').strip()[:1000]
        try: hours=max(1,min(720,int(payload.get('duration_hours') or self.config.get('bounty_default_duration_hours',24) or 24)))
        except Exception:return error_response('有效小时必须是整数。',status_code=400)
        rewards=payload.get('rewards')
        if isinstance(rewards,list) and len(rewards)>6:
            return error_response('一个悬赏最多设置 6 项奖励。',status_code=400)
        if not isinstance(rewards,list):
            # Backward-compatible legacy fields.
            rewards=[]
            for typ,key in (("coins","reward_coins"),("gems","reward_gems"),("exp","reward_exp")):
                try: amount=int(payload.get(key) or 0)
                except Exception: amount=0
                if amount>0: rewards.append({"type":typ,"amount":amount})
        clean=[]; coins=gems=exp=total_items=0; seen_items=set()
        catalog={x['item_id']:x for x in self._build_bounty_reward_catalog()}
        if not rewards:return error_response('至少添加一种悬赏奖励。',status_code=400)
        for raw_reward in rewards[:6]:
            if not isinstance(raw_reward,dict):continue
            typ=str(raw_reward.get('type') or '').strip().lower()
            try: amount=int(raw_reward.get('amount') or raw_reward.get('qty') or 0)
            except Exception: amount=0
            if amount<=0:continue
            if typ=='coins':
                coins=min(200000,coins+amount); continue
            if typ=='gems':
                gems=min(1000,gems+amount); continue
            if typ in {'exp','experience'}:
                exp=min(200000,exp+amount); continue
            if typ in {'item','equipment'}:
                iid=str(raw_reward.get('item_id') or '').strip()
                asset=catalog.get(iid)
                if not asset:return error_response(f'悬赏资产「{iid or "空"}」不在当前可用目录，请点击“刷新物品”后重新选择。',status_code=400)
                actual_type=str(asset.get('reward_type') or 'item')
                if typ != actual_type and not (typ=='item' and actual_type=='item'):
                    return error_response('所选悬赏资产类型与目录不匹配，请重新选择。',status_code=400)
                if iid in seen_items:return error_response(f'资产「{asset["name"]}」不能重复添加，请直接修改数量。',status_code=400)
                amount=min(100,amount)
                if total_items+amount>200:return error_response('单个悬赏的物品/装备总数量不能超过 200。',status_code=400)
                seen_items.add(iid); total_items+=amount
                record={'item_id':iid,'item_name':asset['name'],'qty':amount,'source':asset.get('source',''), 'type':actual_type}
                if actual_type=='equipment':
                    for key in ('equipment_recipe_id','slot','rarity','level','attack','defense','explore_bonus','revive_chance'):
                        if key in asset: record[key]=asset[key]
                clean.append(record)
                continue
            return error_response(f'不支持的奖励类型：{typ or "空"}。',status_code=400)
        if not (coins or gems or exp or clean):return error_response('奖励不能全部为 0。',status_code=400)
        # Admin/system-funded bounties use fixed safety ceilings to protect the world economy.
        if coins>200000 or gems>1000 or exp>200000:return error_response('悬赏奖励超过系统安全上限。',status_code=400)
        bid=self.db.create_bounty(bounty_type='duel',title=title,description=desc,target_user_id=str(target['user_id']),target_name=str(target['name'] or '冒险者'),reward_coins=coins,reward_gems=gems,reward_exp=exp,reward_items=clean,expires_at=int(time.time())+hours*3600,created_by=self._web_username() or 'web',target_player_uid=str(target['player_uid'] or '').strip())
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','bounty_create',str(target['user_id']),f'id={bid};title={title};coins={coins};gems={gems};exp={exp};items={json.dumps(clean,ensure_ascii=False)};hours={hours}')
        return json_response({'ok':True,'message':f'悬赏 #{bid} 已发布。','bounty_id':bid,'rewards':{'coins':coins,'gems':gems,'exp':exp,'items':clean}})

    async def page_worldplus_bounty_cancel(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        bid=int(payload.get('bounty_id') or 0)
        if bid<=0:return error_response('bounty_id 无效。',status_code=400)
        result=self.db.cancel_bounty(bid)
        if not result.get('ok'): return error_response(result.get('message','该悬赏不存在或当前状态不能取消。'),status_code=409)
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','bounty_cancel',str(bid),'')
        return json_response({'ok':True,'message':f'悬赏 #{bid} 已取消。'})

    async def page_worldplus_boss_save(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        changes=payload.get('changes') or {}
        if not isinstance(changes,dict):return error_response('changes 必须是对象。',status_code=400)
        allow={'global_boss_enabled','global_boss_auto_spawn','global_boss_interval_hours','global_boss_name','global_boss_description','global_boss_max_hp','global_boss_attack','global_boss_defense','global_boss_skill_chance_percent','global_boss_duration_hours','global_boss_attack_cooldown_seconds','global_boss_stamina_cost','global_boss_participation_reward','global_boss_reward_pool_coins','global_boss_reward_pool_gems','global_boss_exp_per_1000_damage','global_boss_enrage_threshold_percent','global_boss_enrage_multiplier','global_boss_profile_json'}
        schema=self._schema
        for k,v in changes.items():
            if k not in allow:return error_response(f'不允许修改配置项：{k}',status_code=400)
            spec=schema.get(k,{})
            if spec.get('type')=='bool' and not isinstance(v,bool):return error_response(f'{k} 必须是布尔值',status_code=400)
            if spec.get('type')=='int' and (not isinstance(v,int) or isinstance(v,bool)):return error_response(f'{k} 必须是整数',status_code=400)
            if spec.get('type')=='float' and (not isinstance(v,(int,float)) or isinstance(v,bool)):return error_response(f'{k} 必须是数字',status_code=400)
            if spec.get('type') in {'string','text'} and not isinstance(v,str):return error_response(f'{k} 必须是字符串',status_code=400)
            sl=spec.get('slider');
            if sl and isinstance(v,(int,float)) and not (sl['min']<=v<=sl['max']):return error_response(f'{k} 超出范围',status_code=400)
            if k.endswith('_profile_json') or k=='global_boss_top_rewards_json':
                try:json.loads(v or '{}')
                except Exception:return error_response(f'{k} 不是有效 JSON',status_code=400)
        for k,v in changes.items():self.config[k]=v
        if not self._save_config_object(self.config):return error_response('Boss 配置保存失败，请检查 AstrBot 配置权限。',status_code=500)
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','global_boss_save',None,','.join(changes))
        return json_response({'ok':True,'message':'大世界 Boss 配置已保存。','config':self._safe_config()})

    async def page_worldplus_boss_spawn(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        text=self.engine.spawn_global_boss(bool(payload.get('force',False)))
        if text.startswith('🌍🐉【大世界 Boss 降临】'):
            await self._broadcast_all_groups(text)
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','global_boss_spawn',None,text[:500])
        return json_response({'ok':True,'message':text,'boss':dict(self.db.get_global_boss())})

    async def page_worldplus_boss_finish(self):
        payload=await request.json(default={}) if request else {}
        token=str(payload.get('token') or ''); denied=self._require_web(True,token)
        if denied:return denied
        text=self.engine.finish_global_boss('管理员手动结束')
        if text.startswith('🏆【大世界 Boss 结束】'):
            await self._broadcast_all_groups(text)
        self.db.add_admin_log('__SYSTEM__',self._web_username() or 'web','global_boss_finish',None,text[:500])
        return json_response({'ok':True,'message':text,'boss':dict(self.db.get_global_boss())})

    def _register_web_api(self) -> None:
        if not hasattr(self.context, "register_web_api") or not json_response:
            return
        routes = [
            ("bootstrap", self.page_bootstrap, ["GET"], "群聊世界后台初始化"),
            ("login", self.page_login, ["POST"], "群聊世界后台登录"),
            ("overview", self.page_overview, ["GET"], "群聊世界仪表盘"),
            ("groups", self.page_groups, ["GET"], "群聊世界群列表"),
            ("players", self.page_players, ["GET"], "群聊世界玩家数据"),
            ("transactions", self.page_transactions, ["GET"], "群聊世界经济流水"),
            ("events", self.page_events, ["GET"], "群聊世界世界事件"),
            ("logs", self.page_logs, ["GET"], "群聊世界操作日志"),
            ("settings", self.page_settings, ["GET"], "群聊世界配置"),
            ("settings/save", self.page_save_settings, ["POST"], "群聊世界保存配置"),
            ("system", self.page_system, ["GET"], "群聊世界系统运维"),
            ("system/overview", self.page_system, ["GET"], "群聊世界系统运维聚合接口"),
            ("ops", self.page_system, ["GET"], "群聊世界系统运维兼容接口"),
            ("system/cleanup", self.page_system_cleanup, ["POST"], "群聊世界手动清理缓存"),
            ("system/cleanup_cache", self.page_system_cleanup, ["POST"], "群聊世界手动清理缓存兼容路由"),
            ("cache/clear", self.page_system_cleanup, ["POST"], "群聊世界清理可重建缓存"),
            ("menu/status", self.page_menu_status, ["GET"], "群聊世界帮助菜单图片状态"),
            ("menu/settings/save", self.page_menu_settings_save, ["POST"], "群聊世界保存帮助菜单设置"),
            ("menu/upload", self.page_menu_upload, ["POST"], "群聊世界上传帮助菜单图片"),
            ("menu/reset", self.page_menu_reset, ["POST"], "群聊世界恢复默认帮助菜单图片"),
            ("world/settings", self.page_world_settings, ["GET"], "群聊世界世界设置读取"),
            ("world/settings/get", self.page_world_settings, ["GET"], "群聊世界世界设置读取兼容路由"),
            ("world_settings", self.page_world_settings, ["GET"], "群聊世界世界设置读取兼容路由"),
            ("world/settings/save", self.page_world_settings_save, ["POST"], "群聊世界世界设置保存"),
            ("world_settings/save", self.page_world_settings_save, ["POST"], "群聊世界世界设置保存兼容路由"),
            ("worldplus", self.page_worldplus, ["GET"], "群聊世界+单页聚合接口"),
            ("worldplus/home", self.page_worldplus, ["GET"], "群聊世界+兼容聚合接口"),
            ("worldplus/overview", self.page_worldplus_overview, ["GET"], "群聊世界悬赏与大世界 Boss 总览"),
            ("worldplus/bounty/catalog", self.page_worldplus_bounty_catalog, ["GET"], "群聊世界悬赏奖励物品目录"),
            ("worldplus/bounty/items", self.page_worldplus_bounty_catalog, ["GET"], "群聊世界悬赏奖励物品兼容目录"),
            ("worldplus/bounty/create", self.page_worldplus_bounty_create, ["POST"], "群聊世界后台发布悬赏"),
            ("worldplus/bounty/cancel", self.page_worldplus_bounty_cancel, ["POST"], "群聊世界后台取消悬赏"),
            ("worldplus/boss/save", self.page_worldplus_boss_save, ["POST"], "群聊世界保存大世界 Boss 配置"),
            ("worldplus/boss/spawn", self.page_worldplus_boss_spawn, ["POST"], "群聊世界召唤大世界 Boss"),
            ("worldplus/boss/finish", self.page_worldplus_boss_finish, ["POST"], "群聊世界结算大世界 Boss"),
            ("broadcasts", self.page_broadcasts, ["GET"], "群聊世界群发配置"),
            ("broadcast/campaigns", self.page_broadcast_campaigns, ["GET"], "群聊世界循环群发任务"),
            ("broadcast/campaign/save", self.page_broadcast_campaign_save, ["POST"], "群聊世界保存循环群发任务"),
            ("broadcast/campaign/toggle", self.page_broadcast_campaign_toggle, ["POST"], "群聊世界启停循环群发任务"),
            ("broadcast/campaign/delete", self.page_broadcast_campaign_delete, ["POST"], "群聊世界删除循环群发任务"),
            ("broadcast/save", self.page_broadcast_save, ["POST"], "群聊世界保存群发配置"),
            ("broadcast/batch_save", self.page_broadcast_batch_save, ["POST"], "群聊世界批量保存群发"),
            ("broadcast/send_now", self.page_broadcast_send_now, ["POST"], "群聊世界立即群发"),
            ("broadcast/batch_send_now", self.page_broadcast_batch_send_now, ["POST"], "群聊世界批量立即群发"),
            ("group/action", self.page_group_action, ["POST"], "群聊世界群操作"),
            ("player/action", self.page_player_action, ["POST"], "群聊世界玩家操作"),
            ("data/export", self.page_data_export, ["GET"], "群聊世界数据导出"),
            ("data/import", self.page_data_import, ["POST"], "群聊世界数据导入"),
            ("tasks", self.page_tasks, ["GET"], "群聊世界任务数据"),
            ("tutorials", self.page_tutorials, ["GET"], "群聊世界教程数据"),
            ("cloud/portal", self.page_cloud_portal, ["GET"], "群聊世界云端总览"),
            ("cloud/announcements", self.page_cloud_announcements, ["GET"], "群聊世界云端公告"),
            ("cloud/status", self.page_cloud_status, ["GET"], "群聊世界云端状态"),
            ("cloud/test", self.page_cloud_test, ["GET"], "群聊世界云端连接测试"),
            ("cloud/sync", self.page_cloud_sync, ["POST"], "群聊世界云端数据同步"),
            ("cloud/package-preview", self.page_cloud_package_preview, ["POST"], "预览公开 JSON 数据包"),
            ("cloud/import-package", self.page_cloud_import_package, ["POST"], "获取并导入社区 JSON 数据包"),
            ("cloud/export", self.page_cloud_export, ["GET"], "群聊世界云端数据导出"),
            ("cloud/local-export", self.page_cloud_local_export, ["GET"], "群聊世界本地自定义数据导出"),
        ]
        for path, handler, methods, desc in routes:
            try:
                self.context.register_web_api(f"/{PLUGIN_NAME}/{path}", handler, methods, desc)
            except Exception as exc:
                logger.warning("[群聊世界] Web API 注册失败 %s：%s", path, exc)

    async def terminate(self):
        self._closed = True
        for task in list(self._broadcast_campaign_tasks.values()) + list(self._broadcast_oneoff_tasks):
            if task and not task.done():
                task.cancel()
        if self._broadcast_campaign_tasks or self._broadcast_oneoff_tasks:
            await asyncio.gather(*[t for t in list(self._broadcast_campaign_tasks.values()) + list(self._broadcast_oneoff_tasks) if t and not t.done()], return_exceptions=True)
        self._broadcast_campaign_tasks.clear()
        self._broadcast_oneoff_tasks.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        try:
            self.db.close()
        except Exception:
            pass
        logger.info("[群聊世界] 插件已停止")

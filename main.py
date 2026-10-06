from __future__ import annotations

import asyncio
import json
import re
import time
import secrets
from urllib.parse import quote
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

try:
    from astrbot.api.web import error_response, file_response, json_response, request
except Exception:  # pragma: no cover - compatibility fallback
    error_response = file_response = json_response = request = None

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover - compatibility fallback
    get_astrbot_data_path = None

try:
    from .core.database import Database
    from .core.globaldb import GlobalPlayerDB
    from .core.engine import ACHIEVEMENT_INFO, PROFESSIONS, WorldEngine
    from .cloud_client import CloudClient
except ImportError:  # AstrBot loader compatibility when main.py is imported as a standalone module
    from core.database import Database
    from core.globaldb import GlobalPlayerDB
    from core.engine import ACHIEVEMENT_INFO, PROFESSIONS, WorldEngine
    from cloud_client import CloudClient

PLUGIN_NAME = "astrbot_plugin_group_world"
CLOUD_DEFAULT_URL = "https://ysgl.bot.cd/astrbot"


class Main(Star):
    """群聊世界 V1.9.2.

    The plugin deliberately relies on AstrBot's unified event/message layer.
    This keeps the game logic independent from QQ's transport while declaring
    support for the QQ official adapters in metadata.yaml.
    """

    def __init__(self, context: Context, config: Any):
        super().__init__(context)
        self.config = config if hasattr(config, "save_config") else dict(config or {})
        self._closed = False
        self._task: asyncio.Task | None = None
        self._web_tokens: dict[str, tuple[str, int]] = {}
        self._last_proactive_broadcast_at = 0.0
        self._web_action_guard: dict[tuple[str, str], float] = {}
        self._duel_lock = asyncio.Lock()
        self._cloud_data: dict[str, Any] = {}
        self._cloud_last_sync_at = 0.0
        self._cloud_sync_lock = asyncio.Lock()
        self._schema = {}
        try:
            self._schema = json.loads((Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        except Exception:
            self._schema = {}

        if get_astrbot_data_path:
            base = Path(get_astrbot_data_path())
        else:
            base = Path("data")
        self.data_dir = base / "plugin_data" / PLUGIN_NAME
        self.data_dir.mkdir(parents=True, exist_ok=True)
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
        counts = {"bosses": 0, "products": 0, "monsters": 0, "npcs": 0, "tutorials": 0}
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
        pages = payload.get("tutorials")
        if isinstance(pages, dict):
            tutorials = self._cloud_data.setdefault("tutorials", {})
            for k, v in list(pages.items())[:100]:
                if str(k).strip() and str(k) not in tutorials and isinstance(v, str):
                    tutorials[str(k)] = v
                    counts["tutorials"] += 1
        return counts

    def _cloud_active_merge_counts(self) -> dict[str, int]:
        data = self._cloud_data if isinstance(self._cloud_data, dict) else {}
        counts = {"products": 0, "bosses": 0, "monsters": 0, "npcs": 0, "tutorials": 0}
        selected = data.get("selected_packages", [])
        if not isinstance(selected, list):
            return counts
        for pkg in selected:
            payload = pkg.get("payload") if isinstance(pkg, dict) and isinstance(pkg.get("payload"), dict) else {}
            for key in ("products", "bosses", "monsters", "npcs"):
                values = payload.get(key)
                if isinstance(values, list):
                    counts[key] += sum(1 for x in values[:500] if isinstance(x, dict))
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

        self.config["cloud_shop_catalog_json"] = json.dumps({
            str(code): [str(item.get("name") or "云端商品"), max(0, int(item.get("price", 0) or 0)), str(item.get("description") or item.get("intro") or "云端自定义商品")]
            for code, item in products.items() if isinstance(item, dict)
        }, ensure_ascii=False)
        self.config["cloud_shop_effects_json"] = json.dumps(effects, ensure_ascii=False)
        self.config["cloud_monster_catalog_json"] = json.dumps(monsters, ensure_ascii=False)
        self.config["cloud_tutorial_pages_json"] = json.dumps(tutorials, ensure_ascii=False)
        self.config["cloud_boss_catalog_json"] = json.dumps(bosses, ensure_ascii=False)
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

    async def _broadcast(self, origin: str, text: str, *, proactive: bool = False) -> bool:
        if not origin:
            return False
        payload = text
        if proactive:
            payload = payload.rstrip() + self._proactive_footer()
        try:
            chain = MessageChain().message(payload)
            await self.context.send_message(origin, chain)
            return True
        except Exception as exc:
            logger.warning("[群聊世界] 主动消息发送失败: %s", exc)
            return False

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

        # Deliver cross-group duel messages to the opponent's original session.
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
            self._ensure_scheduler()
            self.db.upsert_group(group_id, event.unified_msg_origin)
            self.db.touch_message(group_id, self._user(event), event.unified_msg_origin)
        except Exception as exc:
            logger.warning("[群聊世界] 统计记录失败：%s", exc)

    # ------------------------- player commands -------------------------

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

    @filter.command("帮助", alias={"世界帮助", "worldhelp"})
    async def help(self, event: AstrMessageEvent):
        """查看群聊世界完整命令。"""
        blocked = self._disabled_or_private(event)
        if blocked:
            yield event.plain_result(blocked)
            return
        text = (
            "📖 【群聊世界命令】\n\n"
            "👤 玩家：/注册 [邀请码] /我的 /签到 /邀请码 /教程 /继续教程 /跳过教程 /地图\n"
            "🗺️ 冒险：/探索 /探索 深度 /探索 危险 /钓鱼 /挖矿 /打工 /怪物\n"
            "✨ 战斗：/技能 /技能学习 /技能装备 /技能卸下 /攻击怪物 /技能使用 /自动战斗 /逃跑 /决斗匹配 /决斗状态 /决斗攻击 /决斗技能 /战后留言 /复活状态\n"
            "🎒 物品：/背包 /商店 /购买 ID 数量 /使用 ID 数量 /装备 /穿戴 ID /强化 ID\n"
            "🐾 宠物：/宠物 /抽宠物 /出战宠物 ID\n"
            "⭐ 成长：/职业 /转职 职业 /任务 /任务领取 /成就\n"
            "🏆 排行：/排行榜\n"
            "🎮 游戏：/游戏 /猜数字 /猜 N /猜拳 石头 /骰子 /炸弹 /抽炸弹\n"
            "🐉 Boss：/Boss /攻击\n"
            "💸 社交：/转账 用户ID 金额\n\n"
            "管理员：/世界管理"
        )
        yield event.plain_result(text)

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
        if not args:
            yield event.plain_result("✨ 用法：`/技能使用 技能名`。\n先输入 `/技能` 查看并配置技能栏。")
            return
        group=self._group(event); uid=self._user(event); name=self._name(event)
        monster=self.db.get_active_monster(group,uid)
        if monster:
            result=self.engine._monster_hit(group,uid,name,args[0])
        else:
            result=self.engine.boss_use_skill(group,uid,name,args[0])
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
            async with self._duel_lock:
                text=self.engine.duel_join(g,u,n,origin)
                b=self.db.get_active_duel_for_user(u)
            yield event.plain_result(text)
            if b:
                other=b["p2_origin"] if b["p1_user_id"]==u else b["p1_origin"]
                other_name=b["p2_name"] if b["p1_user_id"]==u else b["p1_name"]
                if other and other!=origin:
                    await self._broadcast(other,f"⚔️【跨群决斗 #{b['id']}】你已匹配到 {n}！现在开始战斗。输入 `/决斗状态` 查看，轮到你时输入 `/决斗攻击`。",proactive=False)
            return
        yield event.plain_result("用法：/决斗 匹配｜/决斗 状态｜/决斗 取消")

    @filter.command("决斗攻击", alias={"pvp攻击"})
    async def duel_attack(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        text,b=self.engine.duel_action(self._user(event),"attack")
        yield event.plain_result(text)
        if b:
            other=b["p2_origin"] if b["p1_user_id"]==self._user(event) else b["p1_origin"]
            origin=getattr(event,"unified_msg_origin","")
            if other and other!=origin:
                await self._broadcast(other,text,proactive=False)

    @filter.command("决斗技能", alias={"pvp技能"})
    async def duel_skill(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("用法：/决斗技能 技能名"); return
        text,b=self.engine.duel_action(self._user(event),"skill",args[0]); yield event.plain_result(text)
        if b:
            other=b["p2_origin"] if b["p1_user_id"]==self._user(event) else b["p1_origin"]
            origin=getattr(event,"unified_msg_origin","")
            if other and other!=origin:
                await self._broadcast(other,text,proactive=False)

    @filter.command("决斗防御", alias={"pvp防御"})
    async def duel_guard(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        text,b=self.engine.duel_action(self._user(event),"guard"); yield event.plain_result(text)
        if b:
            other=b["p2_origin"] if b["p1_user_id"]==self._user(event) else b["p1_origin"]
            origin=getattr(event,"unified_msg_origin","")
            if other and other!=origin:
                await self._broadcast(other,text,proactive=False)

    @filter.command("战后留言", alias={"决斗留言"})
    async def duel_message(self,event:AstrMessageEvent):
        blocked=self._disabled_or_private(event)
        if blocked: yield event.plain_result(blocked); return
        args=self._args(event)
        if not args: yield event.plain_result("用法：/战后留言 内容"); return
        text,b,msg_id=self.engine.duel_message(self._user(event)," ".join(args)); yield event.plain_result(text)
        if b:
            other=b["p2_origin"] if b["p1_user_id"]==self._user(event) else b["p1_origin"]
            sender=b["p1_name"] if b["p1_user_id"]==self._user(event) else b["p2_name"]
            origin=getattr(event,"unified_msg_origin","")
            delivered=False if not other or other==origin else await self._broadcast(other,f"💬【决斗战后留言】{sender}：{' '.join(args)}",proactive=False)
            if delivered and msg_id:
                self.db.mark_duel_messages_delivered(self._user(event),[int(msg_id)])

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
            lines.append(f"#{row['id']} {row['name']} [{row['rarity']}] Lv.{row['level']}｜⚔️{row['attack']} 🛡️{row['defense']} {flag}")
        lines.append("\n穿戴：/穿戴 装备ID")
        yield event.plain_result("\n".join(lines))

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
        token = token or self._web_token()
        if token and token in self._web_tokens:
            owner, expires = self._web_tokens[token]
            if expires > int(time.time()):
                return True
            self._web_tokens.pop(token, None)
        return False

    def _issue_web_token(self) -> str:
        # Short-lived in-memory token; it never lands in SQLite/config.
        token = secrets.token_urlsafe(32)
        self._web_tokens[token] = (self._web_username() or "password-login", int(time.time()) + 6 * 3600)
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
            "version": "1.9.0",
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
        return json_response({"summary": summary, "groups": groups})

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
            allowed={"world_weather","world_location","world_event_enabled","explore_enabled","monster_enabled","monster_chance_percent","monster_max_count","monster_multi_chance_percent","npc_enabled","npc_chance_percent","npc_interval_minutes"}
            clean={}
            for k,v in raw_payload.items():
                if k not in allowed: continue
                if k in {"world_weather","world_location"}: clean[k]=str(v)[:80]
                else:
                    try: clean[k]=int(v)
                    except Exception: return error_response(f"{k} 必须是整数",status_code=400)
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
            "monster_encounters": "monster_encounters", "player_skills": "player_skills",
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
            elif group_id and table in {"world_event_logs", "admin_logs", "message_stats", "group_members", "monster_encounters", "npc_interactions", "action_logs", "boss_damage", "game_sessions", "auto_battles"}:
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
            "auto_battles","world_event_logs","monster_encounters","player_skills","skill_cooldowns","npc_interactions","action_logs",
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
        return json_response({"ok": True, "message": "云端同步完成。", "data": data, "selected_package_nos": self._cloud_selected_package_nos(), "counts": {k: len(data.get(k, [] if k != "tutorials" else {})) for k in ("bosses","products","tutorials","monsters","npcs")}})

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
            "community_packages": data.get("community_packages", []),
            "custom_json_packages": data.get("custom_json_packages", []),
        }
        target = self.data_dir / f"cloud-package-{int(time.time())}.json"
        target.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
        if file_response:
            return file_response(target, filename=target.name, content_type="application/json")
        return json_response(export)

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

from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .database import Database

PROFESSIONS = {
    "无职业": "无职业",
    "战士": "战斗伤害 +20%",
    "法师": "随机事件奖励 +15%",
    "刺客": "探索稀有物品概率提高",
    "农夫": "签到额外金币 +30%",
    "探险家": "探索体力消耗 -2",
    "商人": "商店价格 -10%",
    "幸运师": "幸运事件概率提高",
    "猎人": "危险事件额外收益 +20%",
    "收藏家": "获得材料时数量 +20%",
}

RARITY_ORDER = ["普通", "优秀", "稀有", "史诗", "传说", "神话"]
PET_RARITIES = {
    "普通": (52, 3, 2, 1, 1),
    "优秀": (28, 6, 4, 2, 2),
    "稀有": (13, 10, 7, 4, 4),
    "史诗": (5, 16, 12, 7, 7),
    "传说": (1.8, 25, 18, 11, 12),
    "神话": (0.2, 40, 28, 20, 20),
}
PET_SPECIES = ["小火龙", "月兔", "机械狐", "森之鹿", "星尘鸟", "深海章鱼", "黑曜狼", "史莱姆王"]
PERSONALITIES = ["勇敢", "胆小", "贪财", "幸运", "懒惰", "忠诚", "暴躁", "聪明"]

SKILLS = {
    "普攻": {"name":"普通攻击","type":"damage","power":1.0,"cost":0,"cooldown":0,"desc":"稳定的基础攻击。"},
    "重击": {"name":"重击","type":"damage","power":1.65,"cost":8,"cooldown":6,"desc":"高伤害，但消耗体力。"},
    "连斩": {"name":"连斩","type":"damage","power":1.25,"cost":10,"cooldown":9,"hits":2,"desc":"连续攻击两次。"},
    "护盾": {"name":"守护屏障","type":"guard","power":0.35,"cost":6,"cooldown":12,"desc":"下一次受到怪物攻击时减伤。"},
    "吸血": {"name":"猩红打击","type":"lifesteal","power":1.15,"cost":12,"cooldown":14,"heal":0.28,"desc":"造成伤害并恢复部分体力。"},
    "火球": {"name":"火球术","type":"damage","power":1.8,"cost":14,"cooldown":10,"element":"火","desc":"火属性攻击。"},
    "冰枪": {"name":"冰霜长枪","type":"damage","power":1.45,"cost":12,"cooldown":8,"element":"冰","slow":2,"desc":"冰属性攻击，有机会降低怪物攻击。"},
    "毒刃": {"name":"淬毒之刃","type":"damage","power":1.35,"cost":9,"cooldown":7,"element":"毒","dot":18,"desc":"附加持续伤害。"},
    "雷击": {"name":"落雷","type":"damage","power":2.05,"cost":18,"cooldown":16,"element":"雷","desc":"高爆发雷属性技能。"},
    "火焰风暴": {"name":"火焰风暴","type":"aoe","power":1.55,"cost":22,"cooldown":18,"element":"火","aoe_ratio":0.85,"desc":"范围攻击，对群体怪物特别有效。"},
    "雷霆震爆": {"name":"雷霆震爆","type":"aoe","power":1.7,"cost":26,"cooldown":22,"element":"雷","aoe_ratio":0.9,"desc":"范围雷击，拥有小概率震慑敌人。"},
}

MONSTER_TEMPLATES = {
    "slime": {"name":"软泥怪","hp":220,"attack":28,"defense":4,"coins":180,"exp":90,"items":{"slime_core":1},"skills":[{"name":"黏液喷射","multiplier":1.20,"chance":25,"text":"减速黏液溅射，造成额外伤害！","aoe":False}]},
    "wolf": {"name":"森林野狼","hp":360,"attack":42,"defense":8,"coins":280,"exp":150,"items":{"wolf_fang":1},"skills":[{"name":"群狼扑袭","multiplier":1.28,"chance":30,"text":"狼群协同扑袭，波及附近冒险者！","aoe":True}]},
    "goblin": {"name":"贪财哥布林","hp":500,"attack":55,"defense":12,"coins":520,"exp":240,"items":{"goblin_ear":1,"ore":1},"skills":[{"name":"零钱炸弹","multiplier":1.32,"chance":26,"text":"把金币做成爆弹砸向你！","aoe":False}]},
    "skeleton": {"name":"荒漠骷髅","hp":620,"attack":68,"defense":18,"coins":700,"exp":330,"items":{"bone":2,"ore":1},"skills":[{"name":"骨矛齐射","multiplier":1.38,"chance":28,"text":"骨矛从四面八方射来！","aoe":True}]},
    "ice_beast": {"name":"冰原兽","hp":900,"attack":92,"defense":26,"coins":1100,"exp":520,"items":{"ice_core":1,"crystal":1},"skills":[{"name":"冰川震击","multiplier":1.45,"chance":32,"text":"冰霜震波席卷战场！","aoe":True}]},
    "lava_hound": {"name":"熔岩猎犬","hp":1250,"attack":120,"defense":34,"coins":1600,"exp":760,"items":{"lava_core":1,"ore":3},"skills":[{"name":"熔岩喷吐","multiplier":1.50,"chance":34,"text":"灼热熔岩喷向多人！","aoe":True}]},
    "void_watcher": {"name":"虚空凝视者","hp":1800,"attack":155,"defense":45,"coins":2400,"exp":1200,"items":{"void_fragment":1,"crystal":2},"skills":[{"name":"虚空撕裂","multiplier":1.55,"chance":28,"text":"扭曲空间造成额外范围伤害！","aoe":True}]},
    "flame_imp": {"name":"炎魔小鬼","hp":760,"attack":105,"defense":20,"coins":980,"exp":430,"items":{"lava_core":1},"skills":[{"name":"火焰喷射","multiplier":1.45,"chance":35,"text":"喷出火焰，造成额外伤害！","aoe":False}]},
    "thunder_hawk": {"name":"雷翼鹰","hp":1100,"attack":132,"defense":29,"coins":1450,"exp":690,"items":{"crystal":1,"feather":2},"skills":[{"name":"雷羽风暴","multiplier":1.6,"chance":30,"text":"召来雷电进行范围打击！","aoe":True}]},
}
LOCATIONS = [
    ("新手村", 1, "安全、稳定，适合新人"),
    ("黑森林", 2, "木材与宠物素材丰富"),
    ("废弃矿洞", 3, "矿石和强化材料"),
    ("黄沙荒漠", 3, "隐藏宝箱较多"),
    ("冰封山脉", 4, "高品质装备"),
    ("恶魔城", 5, "高风险高收益"),
    ("熔岩火山", 6, "火系材料与传说事件"),
    ("虚空领域", 7, "终局资源，死亡概率更高"),
]

ITEM_INFO = {
    "potion": ("体力药水", "使用后恢复 25 点体力。"),
    "super_potion": ("超级体力药水", "使用后恢复 100 点体力。"),
    "energy_drink": ("能量饮料", "恢复 50 体力，并获得 100 EXP。"),
    "food": ("冒险便当", "恢复 10 体力，并获得 40 EXP。"),
    "ore": ("强化矿石", "装备强化材料，强化装备时消耗。"),
    "crystal": ("强化水晶", "稀有强化材料，可用于高品质装备。"),
    "key": ("神秘钥匙", "探索遗迹时有机会让宝箱奖励提升。"),
    "wood": ("冒险素材", "基础探索材料，可用于后续制作系统。"),
    "slime_core": ("史莱姆核心", "怪物材料，可用于后续制作与任务。"),
    "wolf_fang": ("狼牙", "森林怪物材料，可用于制作武器。"),
    "goblin_ear": ("哥布林耳朵", "怪物材料，可用于悬赏任务。"),
    "bone": ("完整骨片", "荒漠怪物材料，可用于制作。"),
    "ice_core": ("冰霜核心", "冰属性怪物稀有材料。"),
    "lava_core": ("熔岩核心", "熔岩怪物稀有材料。"),
    "void_fragment": ("虚空碎片", "终局区域的稀有材料。"),
}

SHOP = {
    "potion": ("体力药水", 300, "恢复 25 体力"),
    "super_potion": ("超级体力药水", 900, "恢复 100 体力"),
    "ore": ("强化矿石", 450, "装备强化材料"),
    "crystal": ("强化水晶", 1500, "稀有装备强化材料"),
    "food": ("冒险便当", 220, "恢复 10 体力并获得 40 经验"),
    "key": ("神秘钥匙", 1200, "探索遗迹时提高宝箱收益"),
    "energy_drink": ("能量饮料", 650, "恢复 50 体力并获得 100 经验"),
}

ACHIEVEMENT_INFO = {
    "first_explore": ("初次冒险", "第一次探索"),
    "first_checkin": ("每日打卡", "完成第一次签到"),
    "rich": ("小富翁", "金币达到 10000"),
    "boss_hunter": ("Boss猎人", "参与击败一次世界 Boss"),
    "collector": ("收藏家", "拥有 3 只宠物"),
    "level_10": ("冒险者", "达到 10 级"),
    "daily_master": ("任务达人", "完成一整套每日任务"),
    "first_fishing": ("钓鱼佬", "第一次成功钓鱼"),
    "first_mining": ("矿工学徒", "第一次挖矿"),
    "worker": ("打工皇帝", "完成 10 次打工"),
    "tutorial_complete": ("世界学者", "完成新手教程"),
}


def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def fmt_num(value: int) -> str:
    return f"{value:,}"

def utc_ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Result:
    text: str
    level_ups: int = 0
    achievement: str | None = None


class WorldEngine:
    def __init__(self, db: Database, config: dict[str, Any]):
        self.db = db
        self.config = config

    def cfg(self, key: str, group_id: str | None = None, default: Any = None) -> Any:
        value = self.config.get(key, default)
        overrides = self.config.get("group_overrides_json", "{}")
        if group_id:
            try:
                data = json.loads(overrides or "{}")
                if isinstance(data, dict) and isinstance(data.get(group_id), dict):
                    value = data[group_id].get(key, value)
            except Exception:
                pass
        return value

    def json_cfg(self, key: str, group_id: str | None = None, default: Any = None) -> dict[str, Any]:
        raw = self.cfg(key, group_id, "{}")
        try:
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else (default or {})
        except Exception:
            return default or {}

    def _active_world_event(self, group_id: str) -> dict[str, Any] | None:
        try:
            return self.db.raw.get_active_world_event(group_id) if hasattr(self.db, "raw") else self.db.get_active_world_event(group_id)
        except Exception:
            return None

    def _event_effect(self, group_id: str, key: str, default: Any = 0) -> Any:
        event = self._active_world_event(group_id)
        if not event:
            return default
        return event.get("effects", {}).get(key, default)

    def group_enabled(self, group_id: str) -> bool:
        disabled = {x.strip() for x in str(self.config.get("disabled_group_ids", "")).split(",") if x.strip()}
        if group_id in disabled:
            return False
        row = self.db.raw.get_group(group_id) if hasattr(self.db, "raw") else self.db.get_group(group_id)
        if row and not bool(row["enabled"]):
            return False
        return bool(self.cfg("enabled", group_id, True))

    def group_feature_enabled(self, group_id: str, field: str, config_key: str, default: bool = True) -> bool:
        row = self.db.raw.get_group(group_id) if hasattr(self.db, "raw") else self.db.get_group(group_id)
        if row is not None and field in row.keys() and row[field] is not None:
            return bool(row[field])
        return bool(self.cfg(config_key, group_id, default))

    def ensure_player(self, group_id: str, user_id: str, name: str):
        # Player-owned data is global across groups. The group only records membership/world state.
        legacy = self.db.get_player(group_id, user_id) if group_id and group_id != "__GLOBAL_USER__" else None
        player = self.db.get_player("__GLOBAL_USER__", user_id)
        created = False
        if not player:
            if legacy:
                player, created = self.db.ensure_player("__GLOBAL_USER__", user_id, name or legacy["name"], int(legacy["coins"]), int(legacy["gems"]), int(legacy["max_stamina"]), 0)
                fields={k: legacy[k] for k in ("level","exp","stamina","max_stamina","hp","max_hp","luck","renown","profession","title","streak","total_checkin","last_checkin","explore_count","explore_day","active_pet_id","tutorial_status","tutorial_step","total_explores","total_games","total_work","total_boss_damage","total_earned_coins","total_spent_coins") if k in legacy.keys()}
                self.db.set_global_player_fields(user_id, fields)
                # One-time migration of owned collections.
                raw=self.db.raw if hasattr(self.db,'raw') else self.db
                for table in ('inventory','pets','equipment','achievements'):
                    try:
                        raw.execute(f"UPDATE {table} SET group_id=? WHERE group_id=? AND user_id=?", ("__GLOBAL_USER__", group_id, user_id))
                    except Exception:
                        pass
                player=self.db.get_player("__GLOBAL_USER__", user_id)
            else:
                player, created = self.db.ensure_player("__GLOBAL_USER__", user_id, name or "冒险者", int(self.cfg("default_new_player_coins", group_id, 1000)), int(self.cfg("default_new_player_gems", group_id, 3)), int(self.cfg("max_stamina", group_id, 100)), int(self.cfg("new_player_protection_hours", group_id, 24)))
        else:
            if name and name != player["name"]:
                self.db.execute("UPDATE players SET name=?,updated_at=?,last_seen_at=? WHERE group_id=? AND user_id=?", (name, utc_ts_iso(), utc_ts_iso(), "__GLOBAL_USER__", user_id))
                player=self.db.get_player("__GLOBAL_USER__", user_id)
        player=self.db.refresh_stamina(player, int(self.cfg("stamina_regen_minutes", group_id, 10)), int(self.cfg("stamina_regen_amount", group_id, 1)))
        if group_id and group_id != "__GLOBAL_USER__":
            try:
                self.db.raw.upsert_group(group_id)
                self.db.raw.register_group_member(group_id,user_id,name or player["name"])
            except Exception:
                pass
        return player, created

    def invite_info(self, user_id: str, name: str="冒险者") -> str:
        self.ensure_player("",user_id,name)
        code=self.db.raw.ensure_invite_code(user_id,int(self.cfg("invite_code_length",None,6)))
        used=self.db.raw.count_invites(user_id)
        limit=int(self.cfg("invite_max_uses_per_user",None,20))
        return f"🎁【邀请中心】\n\n你的邀请码：`{code}`\n已成功邀请：{used}/{limit if limit>0 else '不限'} 人\n\n邀请方式：让新玩家输入 `/注册 {code}`。\n成功后双方会按后台配置获得金币、钻石和经验奖励。\n\n📌 新手没有邀请码也完全可以直接注册。"

    def apply_invite(self, invite_code: str, invitee_id: str) -> tuple[bool,str]:
        if not bool(self.cfg("invite_enabled",None,True)): return False,"邀请系统当前关闭。"
        code=(invite_code or "").strip().upper()
        if not code: return False,""
        row=self.db.raw.get_inviter_by_code(code)
        if not row: return False,"邀请码不存在或已失效。"
        inviter=str(row["inviter_user_id"])
        if inviter==str(invitee_id): return False,"不能使用自己的邀请码。"
        if self.db.raw.invite_used_by(invitee_id): return False,"你已经使用过邀请码，不能重复领取。"
        limit=int(self.cfg("invite_max_uses_per_user",None,20))
        if limit>0 and self.db.raw.count_invites(inviter)>=limit: return False,"该邀请码已达到使用上限。"
        rewards={"inviter_coins":int(self.cfg("invite_inviter_coins",None,500)),"inviter_gems":int(self.cfg("invite_inviter_gems",None,1)),"invitee_coins":int(self.cfg("invite_invitee_coins",None,500)),"invitee_gems":int(self.cfg("invite_invitee_gems",None,1))}
        if not self.db.raw.record_invite(inviter,invitee_id,code,rewards): return False,"邀请码领取失败，请稍后再试。"
        self.db.wallet_change("__GLOBAL_USER__",inviter,coins_delta=rewards["inviter_coins"],gems_delta=rewards["inviter_gems"],kind="invite_reward",note=f"邀请{invitee_id}")
        self.db.wallet_change("__GLOBAL_USER__",invitee_id,coins_delta=rewards["invitee_coins"],gems_delta=rewards["invitee_gems"],kind="invite_reward",note=f"使用邀请码{code}")
        exp=int(self.cfg("invite_invitee_exp",None,200))
        if exp>0: self.db.change_exp("__GLOBAL_USER__",invitee_id,exp)
        inviter_exp=int(self.cfg("invite_inviter_exp",None,100))
        if inviter_exp>0: self.db.change_exp("__GLOBAL_USER__",inviter,inviter_exp)
        return True,f"🎁 邀请奖励成功！\n你获得：💰+{rewards['invitee_coins']} 💎+{rewards['invitee_gems']} ⭐+{exp} EXP\n邀请人也已获得对应奖励。"


    def tutorial_state(self, group_id: str, user_id: str, name: str = "冒险者") -> dict[str, Any]:
        player, _ = self.ensure_player(group_id, user_id, name)
        return {"status": player["tutorial_status"], "step": int(player["tutorial_step"])}

    def tutorial_page(self, step: int, group_id: str | None = None) -> str:
        custom = self.json_cfg("tutorial_pages_json", group_id)
        if str(step) in custom and isinstance(custom[str(step)], str):
            return str(custom[str(step)])
        pages={
            1:"👋【新手教程 1/10｜欢迎来到群聊世界】\n\n🎁 先说一个重要福利：本世界支持邀请奖励。你的邀请码可以用 `/邀请码` 查看；新玩家可以在注册时输入 `/注册 邀请码`。没有邀请码可以直接 `/注册`，教程也可以随时跳过。\n\n双方奖励由管理员后台配置，成功邀请后会立即到账。\n\n你的核心成长路线：签到 → 任务 → 探索 → 战斗 → 收集 → 强化 → 升级 → Boss。\n\n输入 `/继续教程` 下一页；不想参加邀请可直接 `/继续教程` 或 `/跳过教程`。",
            2:"💰【2/10｜你的角色与货币】\n\n你的角色数据是跨群保存的：换到另一个启用了本插件的群，等级、金币、钻石、背包、宠物、装备仍然是你的。\n\n💰金币：日常消费。\n💎钻石：稀有奖励。\n⭐经验：升级。\n❤️体力：限制高频玩法。\n\n提示：不要把所有金币都花掉，探索和战斗都需要资源。",
            3:"📅【3/10｜每天先做什么】\n\n推荐每日顺序：\n① `/签到`\n② `/任务`\n③ `/探索`\n④ `/游戏`\n⑤ 看 `/排行榜` 或参与 Boss。\n\n连续签到会增加奖励；任务完成后记得 `/任务领取`。",
            4:"🗺️【4/10｜探索与随机事件】\n\n`/探索`：稳定成长。\n`/探索 深度`：消耗更多体力，收益更高。\n`/探索 危险`：高风险高收益。\n\n探索可能发现装备、材料、钻石，也可能遇到危险或怪物。\n\n⚠️ 探索遇到怪物后不要再次 `/探索` 硬顶，先输入 `/怪物` 查看战斗状态。",
            5:"👹【5/10｜怪物战斗】\n\n怪物是持久化遭遇：哪怕你上一条消息结束了，下一条仍能继续。怪物也会反击，你有独立的战斗生命。\n\n`/怪物` 查看当前敌人。\n`/攻击怪物` 普攻。\n`/技能使用 火球` 使用技能。\n`/自动战斗 开启` 自动轮换技能。\n`/逃跑` 主动脱离怪物。\n\n击败怪物可以获得金币、EXP 和材料；战斗生命耗尽时先使用恢复品。",
            6:"✨【6/10｜技能系统】\n\n你会逐步获得技能点，可以学习更多技能。\n\n`/技能`：查看完整技能清单。\n`/技能学习 雷击`：学习。\n`/技能装备 雷击`：放入技能栏。\n`/技能卸下 雷击`：移出技能栏。\n\n技能有体力消耗和冷却，不要只堆一个技能。",
            7:"🔗【7/10｜技能羁绊】\n\n部分技能组合会触发额外效果：\n🔥 火球 + 重击：火焰羁绊\n❄️ 冰枪 + 雷击：冰雷羁绊\n☠️ 毒刃 + 连斩：连毒羁绊\n🩸 吸血 + 护盾：守护羁绊\n⚡ 火球 + 雷击：超载羁绊\n\n建议根据怪物属性调整技能栏，而不是固定一套。",
            8:"🎒【8/10｜背包、商城与装备】\n\n`/商店`：查看商品和作用。\n`/购买 potion 2`：购买。\n`/背包`：查看所有物品和作用。\n`/使用 potion 1`：使用物品。\n`/装备`：查看装备。\n`/穿戴 装备ID`：穿戴。\n`/强化 装备ID`：强化。\n\n材料不要随便丢，后续制作、公会和高级任务都会使用。",
            9:"🐾【9/10｜宠物、Boss与长期成长】\n\n`/抽宠物` 获得宠物。\n`/宠物` 查看。\n`/出战宠物 ID` 出战。\n\n世界 Boss 出现时会在对应群主动公告。输入 `/Boss` 查看，`/攻击` 参与。\n\nBoss、成就、等级和排行榜会构成长期目标。",
            10:"🏆【10/10｜成为长期玩家】\n\n你已经掌握核心循环。\n\n建议：每天签到、做任务、探索、养宠物、强化装备、收集材料、调整技能、参加世界事件和 Boss。\n\n世界事件会随机在群里主动出现；管理员也可以人工触发事件。\n\n🎓 输入 `/继续教程` 完成教程并获得【世界学者】成就。\n\n如果忘记指令：`/帮助`。如果不知道下一步：`/世界`。",
        }
        return pages.get(step,pages[10])

    def tutorial_begin(self, group_id: str, user_id: str, name: str) -> str:
        self.db.set_tutorial(group_id, user_id, "pending", 1)
        return self.tutorial_page(1, group_id)

    def tutorial_continue(self, group_id: str, user_id: str, name: str) -> Result:
        state = self.tutorial_state(group_id, user_id, name)
        if state["status"] in {"completed", "skipped"}:
            return Result("📖 你的新手教程已经结束了。需要重新查看可以输入 `/教程`。")
        next_step = max(1, state["step"] + 1)
        if next_step >= 11:
            self.db.set_tutorial(group_id, user_id, "completed", 10)
            unlocked = self.db.unlock_achievement(group_id, user_id, "tutorial_complete")
            return Result("🎓【新手教程完成】\n\n你已经掌握群聊世界的核心玩法！\n现在可以自由探索、养宠物、强化装备、参与 Boss 和冲击排行榜。" + ("\n📜 解锁成就【世界学者】" if unlocked else ""))
        self.db.set_tutorial(group_id, user_id, "pending", next_step)
        return Result(self.tutorial_page(next_step, group_id))

    def tutorial_skip(self, group_id: str, user_id: str, name: str) -> str:
        self.ensure_player(group_id, user_id, name)
        self.db.set_tutorial(group_id, user_id, "skipped", 0)
        return "⏭️ 已跳过新手教程。随时可以输入 `/教程` 重新查看。"

    def level_title(self, level: int) -> str:
        if level >= 50:
            return "虚空传说"
        if level >= 30:
            return "世界强者"
        if level >= 20:
            return "资深冒险者"
        if level >= 10:
            return "冒险者"
        if level >= 5:
            return "熟练学徒"
        return "初出茅庐"

    def profile(self, group_id: str, user_id: str, name: str) -> Result:
        player, created = self.ensure_player(group_id, user_id, name)
        pet = self.db.get_active_pet(group_id, user_id)
        equip = self.db.get_equipped_stats(group_id, user_id)
        next_req = self.db.level_requirement(player["level"])
        protection = ""
        if player["protected_until"]:
            try:
                dt = datetime.fromisoformat(player["protected_until"])
                if dt.timestamp() > utc_ts():
                    protection = "\n🛡️ 新人保护：生效中"
            except Exception:
                pass
        pet_line = f"\n🐾 出战宠物：{pet['species']} [{pet['rarity']}] Lv.{pet['level']}" if pet else "\n🐾 出战宠物：无"
        text = (
            f"👤 【玩家档案】\n"
            f"名称：{player['name']}\n"
            f"等级：Lv.{player['level']}（{player['exp']}/{next_req} EXP）\n"
            f"职业：{player['profession']}\n"
            f"称号：{player['title']}\n"
            f"💰 金币：{fmt_num(player['coins'])}\n"
            f"💎 钻石：{fmt_num(player['gems'])}\n"
            f"❤️ 体力：{player['stamina']}/{player['max_stamina']}\n"
            f"🫀 战斗生命：{player['hp']}/{player['max_hp']}\n"
            f"🍀 幸运：{player['luck']}\n"
            f"⭐ 声望：{player['renown']}\n"
            f"⚔️ 装备攻击：{equip['attack']}\n"
            f"🛡️ 装备防御：{equip['defense']}\n"
            f"📅 连续签到：{player['streak']} 天\n"
            f"🗓️ 总签到：{player['total_checkin']} 次"
            f"🔎 累计探索：{player['total_explores']} 次｜🎮 游戏：{player['total_games']} 次"
            f"🎓 教程：{'已完成' if player['tutorial_status']=='completed' else ('已跳过' if player['tutorial_status']=='skipped' else '进行中')}"
            f"{pet_line}{protection}"
        )
        if created:
            text = "🎁 新人礼包已到账！\n\n" + text
        return Result(text)

    def checkin(self, group_id: str, user_id: str, name: str) -> Result:
        self.db.upsert_group(group_id)
        player, _ = self.ensure_player(group_id, user_id, name)
        today = self.db.get_today_key()
        if player["last_checkin"] == today:
            return Result(f"📅 {name} 今天已经签到过啦。\n当前连续签到：{player['streak']} 天\n💰 余额：{fmt_num(player['coins'])}")
        yesterday = None
        try:
            yesterday = (datetime.fromisoformat(today).date()).toordinal() - 1
        except Exception:
            pass
        streak = player["streak"] + 1
        if player["last_checkin"]:
            try:
                prev = datetime.fromisoformat(player["last_checkin"]).date().toordinal()
                streak = player["streak"] + 1 if yesterday == prev else 1
            except Exception:
                streak = 1
        lo = int(self.cfg("checkin_min_coins", group_id, 100))
        hi = max(lo, int(self.cfg("checkin_max_coins", group_id, 500)))
        coins = random.randint(lo, hi)
        if player["profession"] == "农夫":
            coins = int(coins * 1.3)
        streak_bonus = min(int(self.cfg("checkin_streak_bonus_cap", group_id, 1000)), max(0, streak - 1) * int(self.cfg("checkin_streak_bonus_per_day", group_id, 15)))
        coins += streak_bonus
        coins += max(0, int(self._event_effect(group_id, "checkin_bonus_coins", 0) or 0))
        self.db.wallet_change(group_id, user_id, coins_delta=coins, kind="checkin", note=f"连续签到{streak}天")
        self.db.execute(
            "UPDATE players SET streak=?,total_checkin=total_checkin+1,last_checkin=?,title=?,updated_at=? WHERE group_id=? AND user_id=?",
            (streak, today, self.level_title(player["level"]), datetime.now(timezone.utc).isoformat(timespec="seconds"), "__GLOBAL_USER__", user_id),
        )
        self.db.ensure_daily_tasks(group_id, user_id, today)
        self.db.progress_task(group_id, user_id, today, "checkin", 1)
        achievement = None
        if self.db.unlock_achievement(group_id, user_id, "first_checkin"):
            achievement = "📜 解锁成就【每日打卡】"
        return Result(
            f"✅ 签到成功！\n"
            f"🔥 连续签到：{streak} 天\n"
            f"💰 +{fmt_num(coins)} 金币\n"
            f"💰 当前余额：{fmt_num(player['coins'] + coins)}"
            + (f"\n{achievement}" if achievement else "")
        )

    def explore(self, group_id: str, user_id: str, name: str, mode: str = "normal") -> Result:
        player, _ = self.ensure_player(group_id, user_id, name)
        if player["banned"]:
            return Result("🚫 你已被本群群聊世界封禁。")
        active_monster = self.db.raw.get_active_monster(group_id,user_id) if hasattr(self.db,"raw") else self.db.get_active_monster(group_id,user_id)
        if active_monster:
            return Result(f"👹 你正在与【{active_monster['monster_name']}】战斗。\n❤️ HP：{active_monster['hp']}/{active_monster['max_hp']}\n先输入 `/攻击怪物` 或 `/技能使用 技能名`，击败/逃脱后才能继续探索。")
        day = self.db.get_today_key()
        count = player["explore_count"] if player["explore_day"] == day else 0
        limit = int(self.cfg("explore_daily_limit", group_id, 20))
        if count >= limit:
            return Result(f"🗺️ 今日探索次数已达到上限：{limit} 次。")
        if not self.group_feature_enabled(group_id, "explore_enabled", "explore_enabled", True):
            return Result("🗺️ 管理员已关闭本群探索系统。")
        base_cost = int(self.cfg("explore_stamina_cost", group_id, 10))
        cost = base_cost
        if mode == "deep":
            cost = base_cost + int(self.cfg("explore_deep_extra_cost", group_id, 10))
        elif mode == "danger":
            cost = base_cost + int(self.cfg("explore_danger_extra_cost", group_id, 25))
        if player["profession"] == "探险家":
            cost = max(1, cost - 2)
        rain_extra = int(self._event_effect(group_id, "explore_stamina_extra", 0) or 0)
        cost += max(0, rain_extra)
        if player["stamina"] < cost:
            return Result(f"❤️ 体力不足，需要 {cost} 点，当前 {player['stamina']} 点。")
        self.db.change_stamina(group_id, user_id, -cost)
        loc = random.choice(LOCATIONS)
        event_roll = random.random() * 100
        equip = self.db.get_equipped_stats(group_id, user_id)
        rare_boost = 4 if player["profession"] == "刺客" else 0
        fortune_boost = player["luck"] * 0.35
        rare_threshold = max(0.1, float(self.cfg("explore_rare_bonus_percent", group_id, 6)) + rare_boost + fortune_boost + float(self._event_effect(group_id, "explore_gem_bonus_percent", 0) or 0))
        danger_threshold = max(rare_threshold + 1, float(self.cfg("explore_danger_percent", group_id, 10)) + 18)
        lines = [f"🗺️ 你进入了【{loc[0]}】。", f"难度：{'⭐' * loc[1]}"]
        exp_gain = random.randint(70, 160) + loc[1] * 20
        coins_gain = int(random.randint(80, 260) * max(1, loc[1] // 2) * float(self.cfg("explore_reward_multiplier", group_id, 1.0)))
        coins_gain = int(coins_gain * float(self._event_effect(group_id, "explore_coin_multiplier", 1.0) or 1.0))
        if self._event_effect(group_id, "location_reward_location", "") == loc[0]:
            coins_gain = int(coins_gain * float(self._event_effect(group_id, "location_reward_multiplier", 1.0) or 1.0))
        item_text = ""
        if event_roll < rare_threshold:
            gems = random.randint(1, 4 if loc[1] >= 4 else 2)
            self.db.wallet_change(group_id, user_id, gems_delta=gems, kind="explore_gem", note=loc[0])
            item_text = f"\n💎 发现稀有水晶！钻石 +{gems}"
            exp_gain += 150
        elif event_roll < 18 + rare_boost + fortune_boost:
            item_id, item_name, qty = random.choice([
                ("ore", "强化矿石", random.randint(1, 4)),
                ("crystal", "强化水晶", 1),
                ("key", "神秘钥匙", 1),
            ])
            self.db.add_item(group_id, user_id, item_id, item_name, qty)
            item_text = f"\n🎁 获得：{item_name} ×{qty}"
            exp_gain += 80
        elif event_roll < danger_threshold:
            monster_base = float(self.cfg("explore_monster_chance_percent",group_id,16))
            row_group = self.db.raw.get_group(group_id) if hasattr(self.db, "raw") else self.db.get_group(group_id)
            if row_group is not None and row_group["monster_chance_percent"] is not None:
                monster_base = float(row_group["monster_chance_percent"])
            monster_chance = monster_base + loc[1]*1.5 + float(self._event_effect(group_id, "monster_chance_bonus_percent", 0) or 0)
            if random.random()*100 < monster_chance and self.group_feature_enabled(group_id, "monster_enabled", "monster_enabled", True):
                # Monster encounter is persisted, so the next command can continue safely.
                item_text = "\n" + self._spawn_monster(group_id,user_id)
                exp_gain += 30
            else:
                damage = random.randint(5, 18) * loc[1]
                if player["profession"] == "战士":
                    damage = int(damage * 0.8)
                self.db.change_stamina(group_id, user_id, -damage)
                item_text = f"\n🐺 遭遇危险！额外损失体力 {damage}"
                coins_gain = int(coins_gain * 0.7)
        elif event_roll < 31:
            rarity, equip_item = self._random_equipment(loc[1], player["level"], equip["attack"])
            self.db.add_equipment(group_id, user_id, **equip_item, rarity=rarity)
            item_text = f"\n⚔️ 发现装备：【{equip_item['name']}】 [{rarity}]"
            exp_gain += 200
        else:
            self.db.add_item(group_id, user_id, "wood", "冒险素材", random.randint(3, 12))
            item_text = f"\n🌿 收集了冒险素材。"
        self.db.wallet_change(group_id, user_id, coins_delta=coins_gain, kind="explore", note=loc[0])
        self.db.ensure_daily_tasks(group_id, user_id, day)
        player, level_ups = self.db.change_exp(group_id, user_id, exp_gain)
        if player["explore_day"] != day:
            self.db.execute(
                "UPDATE players SET explore_day=?,explore_count=1,updated_at=? WHERE group_id=? AND user_id=?",
                (day, datetime.now(timezone.utc).isoformat(timespec="seconds"), "__GLOBAL_USER__", user_id),
            )
        else:
            self.db.execute(
                "UPDATE players SET explore_count=explore_count+1,updated_at=? WHERE group_id=? AND user_id=?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), "__GLOBAL_USER__", user_id),
            )
        self.db.progress_task(group_id, user_id, day, "explore", 1)
        self.db.update_player_metrics(group_id, user_id, total_explores=1)
        self.db.log_action(group_id, user_id, "explore", f"mode={mode},location={loc[0]}")
        achievement = None
        if self.db.unlock_achievement(group_id, user_id, "first_explore"):
            achievement = "📜 解锁成就【初次冒险】"
        if player["coins"] >= 10000 and self.db.unlock_achievement(group_id, user_id, "rich"):
            achievement = "📜 解锁成就【小富翁】"
        if player["level"] >= 10 and self.db.unlock_achievement(group_id, user_id, "level_10"):
            achievement = "📜 解锁成就【冒险者】"
        level_msg = f"\n🎉 升级！当前 Lv.{player['level']}" if level_ups else ""
        return Result(
            "\n".join(lines)
            + f"\n💰 +{fmt_num(coins_gain)} 金币"
            + f"\n⭐ +{exp_gain} EXP"
            + item_text
            + level_msg
            + (f"\n{achievement}" if achievement else "")
        , level_ups=level_ups, achievement=achievement)

    def skill_list(self, user_id: str, name: str = "冒险者") -> str:
        self.ensure_player("", user_id, name)
        owned = {r["skill_id"]: r for r in self.db.get_skills(user_id)}
        if "普攻" not in owned: self.db.set_skill(user_id, "普攻", 1, 1)
        if "重击" not in owned: self.db.set_skill(user_id, "重击", 1, 0)
        owned = {r["skill_id"]: r for r in self.db.get_skills(user_id)}
        player=self.db.get_player("",user_id)
        points=max(2,int(player["level"])//3+2)
        lines=["✨【技能中心】", f"技能点：{max(0,points-len(owned))}/{points}", "", "📚 技能清单："]
        for sid,info in SKILLS.items():
            if sid in owned:
                mark="⭐已装备" if owned[sid]["equipped"] else "✅已学习"
                cd=self.db.skill_cooldown_remaining(user_id,sid)
                lines.append(f"{mark}｜{sid} Lv.{owned[sid]['level']}｜❤️{info['cost']}｜CD {info['cooldown']}s｜{info['desc']}{'｜冷却 '+str(cd)+'s' if cd else ''}")
            else:
                lines.append(f"🔒未学习｜{sid}｜❤️{info['cost']}｜CD {info['cooldown']}s｜{info['desc']}")
        equipped=[r["skill_id"] for r in owned.values() if r["equipped"]]
        lines += ["", f"🧩 当前技能栏（最多4个）：{'、'.join(equipped) if equipped else '未配置'}", "", "操作：", "`/技能学习 技能名`｜学习", "`/技能装备 技能名`｜装备/卸下", "`/技能使用 技能名`｜对当前怪物使用", "`/攻击怪物`｜使用普通攻击", "", "🔗 羁绊示例：火球+重击、冰枪+雷击、毒刃+连斩。装备组合后可能获得额外效果。"]
        return "\n".join(lines)

    def equip_skill(self, user_id: str, skill_id: str, name: str) -> str:
        self.ensure_player("",user_id,name)
        if skill_id not in SKILLS: return "❌ 没有这个技能。输入 `/技能` 查看技能清单。"
        owned={r["skill_id"] for r in self.db.get_skills(user_id)}
        if skill_id not in owned: return "❌ 你还没有这个技能，请先使用 `/技能学习 技能名`。"
        current=[r for r in self.db.get_skills(user_id) if r["equipped"]]
        if not any(r["skill_id"]==skill_id for r in current) and len(current)>=int(self.cfg("skill_slot_limit", None, 4)):
            return "❌ 技能栏已达到上限。先卸下一个技能：`/技能卸下 技能名`。"
        self.db.set_skill_equipped(user_id,skill_id,0 if any(r["skill_id"]==skill_id for r in current) else 1)
        return self.skill_list(user_id,name)

    def unequip_skill(self,user_id:str,skill_id:str,name:str)->str:
        self.db.set_skill_equipped(user_id,skill_id,0)
        return f"🧩 已卸下【{skill_id}】。\n\n"+self.skill_list(user_id,name)

    def learn_skill(self,user_id:str,skill_id:str,name:str)->str:
        player,_=self.ensure_player("",user_id,name)
        if skill_id not in SKILLS: return "❌ 没有这个技能。"
        if any(r["skill_id"]==skill_id for r in self.db.get_skills(user_id)): return "你已经学会这个技能了。"
        # Skill points = floor(level/5)+1, learned count must stay within points.
        available=max(2,int(player["level"])//3+2)-len(self.db.get_skills(user_id))
        if available<=0: return f"❌ 技能点不足。你当前技能点上限为 {max(2,int(player['level'])//3+2)}，输入 `/我的` 查看等级。"
        self.db.set_skill(user_id,skill_id,1,0)
        return f"📘 学会技能【{skill_id}】！\n{SKILLS[skill_id]['desc']}\n输入 `/技能装备 {skill_id}` 放入技能栏。"

    def _monster_templates(self,group_id:str)->dict:
        data=dict(MONSTER_TEMPLATES)
        custom=self.json_cfg("monster_catalog_json",group_id)
        for mid,val in custom.items():
            if isinstance(val,dict) and val.get("name"):
                data[str(mid)]={**MONSTER_TEMPLATES.get(str(mid),{}),**val,"id":str(mid)}
        return data

    def _spawn_monster(self,group_id:str,user_id:str)->str:
        templates=self._monster_templates(group_id)
        choices=list(templates.items())
        monster_id, monster_base=random.choice(choices)
        monster=dict(monster_base); monster.setdefault("id",monster_id)
        p,_=self.ensure_player(group_id,user_id,"冒险者")
        scale=1+max(0,int(p["level"])-1)*0.025
        row_group = self.db.raw.get_group(group_id) if hasattr(self.db, "raw") else self.db.get_group(group_id)
        max_count = int(row_group["monster_max_count"] if row_group and row_group["monster_max_count"] is not None else self.cfg("explore_monster_max_count", group_id, 3))
        multi_chance = float(row_group["monster_multi_chance_percent"] if row_group and row_group["monster_multi_chance_percent"] is not None else self.cfg("explore_monster_multi_chance_percent", group_id, 28))
        count = 1
        if max_count > 1 and random.random() * 100 < max(0, min(100, multi_chance)):
            count = random.randint(2, max(2, min(max_count, 3)))
        monster["hp"]=max(1,int(monster["hp"]*scale*count)); monster["attack"]=max(1,int(monster["attack"]*scale*(1+0.08*(count-1))))
        monster["coins"]=max(0,int(monster.get("coins",0)*count)); monster["exp"]=max(0,int(monster.get("exp",0)*count))
        if isinstance(monster.get("items"),dict): monster["items"]={k:int(v)*count for k,v in monster["items"].items()}
        if count>1:
            monster["name"]=f"{monster['name']}群 ×{count}"
        monster["enemy_count"]=count
        encounter_id=self.db.raw.create_monster_encounter(group_id,user_id,monster,utc_ts()+int(self.cfg("monster_encounter_minutes",group_id,15))*60) if hasattr(self.db,"raw") else self.db.create_monster_encounter(group_id,user_id,monster,utc_ts()+900)
        skills = monster.get("skills") or []
        skill_note = f"\n✨ 特性：{skills[0].get('name')}（敌人有概率施放）" if skills and isinstance(skills[0], dict) else ""
        return f"👹【遭遇战 #{encounter_id}】\n{monster['name']} 出现了！\n❤️ HP：{monster['hp']}\n⚔️ 攻击：{monster['attack']}｜🛡️ 防御：{monster.get('defense',0)}{skill_note}\n\n输入 `/攻击怪物` 普攻，或 `/技能使用 技能名`。\n15 分钟内不战斗，怪物会逃跑。"

    def monster_flee(self,group_id:str,user_id:str,name:str)->str:
        row=self.db.get_active_monster(group_id,user_id)
        if not row: return "👹 当前没有正在战斗的怪物。"
        self.db.update_monster(row["id"],status="fled")
        self.db.stop_auto_battle(group_id,user_id)
        self.db.change_stamina(group_id,user_id,-5)
        return f"🏃 你逃离了【{row['monster_name']}】。\n消耗 5 点体力作为逃跑代价。\n\n下一步：可以继续 `/探索`，也可以先 `/背包` 使用恢复品。"

    def monster_status(self,group_id:str,user_id:str,name:str)->str:
        row=self.db.raw.get_active_monster(group_id,user_id) if hasattr(self.db,"raw") else self.db.get_active_monster(group_id,user_id)
        if not row: return "👹 你当前没有正在战斗的怪物。探索时有概率遭遇。"
        return f"👹【{row['monster_name']}】\n❤️ HP：{row['hp']}/{row['max_hp']}\n⚔️ 攻击：{row['attack']}｜🛡️ 防御：{row['defense']}\n奖励：💰{row['reward_coins']}｜⭐{row['reward_exp']}\n\n`/攻击怪物` 或 `/技能使用 技能名`"

    def _monster_hit(self,group_id:str,user_id:str,name:str,skill_id:str)->Result:
        row=self.db.raw.get_active_monster(group_id,user_id) if hasattr(self.db,"raw") else self.db.get_active_monster(group_id,user_id)
        if not row: return Result("👹 没有可攻击的怪物。先去 `/探索`。")
        p,_=self.ensure_player(group_id,user_id,name)
        if int(p["hp"])<=0:
            return Result(f"💀 你的战斗生命已经耗尽（0/{p['max_hp']}）。\n请使用 `/使用 potion 1` 或等待生命恢复后再战斗。")
        if skill_id not in SKILLS: return Result("❌ 技能不存在。输入 `/技能` 查看。")
        skill=SKILLS[skill_id]
        if not any(r["skill_id"]==skill_id and r["equipped"] for r in self.db.get_skills(user_id)) and skill_id!="普攻": return Result("❌ 这个技能不在你的技能栏。使用 `/技能装备 技能名`。")
        cd=self.db.skill_cooldown_remaining(user_id,skill_id)
        if cd: return Result(f"⏳【{skill_id}】还在冷却中：{cd} 秒。")
        cost=int(skill.get("cost",0))
        if p["stamina"]<cost: return Result(f"❤️ 体力不足，需要 {cost}。")
        if cost: self.db.change_stamina(group_id,user_id,-cost)
        eq=self.db.get_equipped_stats(group_id,user_id); pet=self.db.get_active_pet(group_id,user_id)
        base=random.randint(45,75)+int(p["level"])*16+eq["attack"]+(int(pet["attack"])*2 if pet else 0)
        multiplier=float(skill.get("power",1.0))
        if skill.get("type") == "aoe":
            multiplier *= float(skill.get("aoe_ratio",0.85) or 0.85) + 0.15
        damage=max(1,int(base*multiplier)-int(row["defense"]))
        if random.random()<0.08+p["luck"]/600: damage*=2; crit=True
        else: crit=False
        # tiny skill bonds: element pairs and profession synergy
        equipped={r["skill_id"] for r in self.db.get_skills(user_id) if r["equipped"]}
        bond=1.0; bond_text=''
        if skill_id=="火球" and "重击" in equipped: bond=1.15; bond_text='🔥重击·火焰羁绊 +15%'
        elif skill_id=="冰枪" and "雷击" in equipped: bond=1.12; bond_text='❄️⚡冰雷羁绊 +12%'
        elif skill_id=="毒刃" and "连斩" in equipped: bond=1.18; bond_text='☠️连毒羁绊 +18%'
        elif skill_id=="吸血" and "护盾" in equipped: bond=1.12; bond_text='🩸守护羁绊 +12%，并获得额外续航'
        elif skill_id=="雷击" and "火球" in equipped: bond=1.10; bond_text='⚡🔥超载羁绊 +10%'
        damage=max(1,int(damage*bond))
        hp=max(0,int(row["hp"])-damage)
        self.db.raw.update_monster(row["id"],hp=hp) if hasattr(self.db,"raw") else self.db.update_monster(row["id"],hp=hp)
        self.db.set_skill_cooldown(user_id,skill_id,int(skill.get("cooldown",0)))
        if hp<=0:
            self.db.raw.update_monster(row["id"],status="defeated",hp=0) if hasattr(self.db,"raw") else self.db.update_monster(row["id"],status="defeated",hp=0)
            coins=int(row["reward_coins"]*float(self.cfg("monster_reward_multiplier",group_id,1.0))); exp=int(row["reward_exp"]*float(self.cfg("monster_reward_multiplier",group_id,1.0))); self.db.wallet_change(group_id,user_id,coins_delta=coins,kind="monster_reward",note=row["monster_name"]); player,ups=self.db.change_exp(group_id,user_id,exp)
            items=json.loads(row["reward_items_json"] or "{}")
            got=[]
            for iid,qty in items.items(): self.db.add_item(group_id,user_id,iid,iid,int(qty)); got.append(f"{iid}×{qty}")
            return Result(f"🏆 击败【{row['monster_name']}】！\n⚔️ {damage} 伤害{'｜暴击' if crit else ''}\n💰 +{coins}｜⭐ +{exp}\n🎒 材料：{'、'.join(got) if got else '无'}"+ (f"\n🎉 升级至 Lv.{player['level']}！" if ups else '') + (f"\n✨ {bond_text}" if bond_text else ''),ups)
        # Monster counterattack, with optional enemy skills stored on the encounter.
        defense=eq["defense"]+(pet["defense"] if pet else 0)
        try:
            enemy_skills=json.loads(row["skills_json"] or "[]") if "skills_json" in row.keys() else []
        except Exception:
            enemy_skills=[]
        enemy_skill = None
        if isinstance(enemy_skills, list):
            available=[x for x in enemy_skills if isinstance(x,dict) and str(x.get("name"))]
            weighted=[x for x in available if random.random()*100 < max(0,min(100,float(x.get("chance",0) or 0)))]
            enemy_skill=random.choice(weighted) if weighted else None
        base_taken=max(1,int(row["attack"]*random.uniform(.75,1.1))-defense//3)
        taken=max(1,int(base_taken*float(enemy_skill.get("multiplier",1.0))) if enemy_skill else base_taken)
        enemy_text=f"✨ 【{enemy_skill['name']}】！{enemy_skill.get('text','')}" if enemy_skill else ""
        group_extra=""
        raw=self.db.raw if hasattr(self.db,"raw") else self.db
        if enemy_skill and bool(enemy_skill.get("aoe",False)):
            members=raw.fetchall("SELECT user_id FROM group_members WHERE group_id=? ORDER BY last_seen_at DESC LIMIT 10", (group_id,))
            targets=[]
            for r in members:
                uid=str(r["user_id"])
                if uid==user_id or random.random()<0.25:
                    targets.append(uid)
            if user_id not in targets: targets.insert(0,user_id)
            targets=targets[:3]
            extra=[]
            for uid in targets:
                if uid==user_id:
                    hit=taken
                else:
                    other_eq=raw.get_equipped_stats(group_id,uid) if hasattr(raw,"get_equipped_stats") else {"defense":0}
                    hit=max(1,taken-int(other_eq.get("defense",0))//5)
                self.db.change_hp(group_id,uid,-hit)
                if uid!=user_id: extra.append(f"• 波及 {uid}：-{hit} HP")
            if extra: group_extra="\n"+"\n".join(extra)
        else:
            self.db.change_hp(group_id,user_id,-taken)
        hp_now=self.db.get_player("__GLOBAL_USER__",user_id)["hp"]
        if hp_now<=0:
            self.db.stop_auto_battle(group_id,user_id)
            return Result(f"💀 你被【{row['monster_name']}】击倒了！\n❤️ 战斗生命：0/{p['max_hp']}\n{enemy_text}\n建议使用 `/使用 potion 1` 恢复，恢复后再继续战斗。")
        return Result(f"⚔️ 你用【{skill_id}】造成 {damage} 伤害{'｜暴击' if crit else ''}。\n👹 {row['monster_name']} 剩余 HP：{hp}\n💥 怪物反击，你损失 {taken} 战斗生命。{group_extra}\n{enemy_text}"+(f"\n✨ {bond_text}" if bond_text else '')+"\n\n下一步：继续 `/攻击怪物` 或 `/技能使用 技能名`；也可以 `/自动战斗 开启`。\n输入 `/怪物` 查看完整状态。")

    def _skill_damage(self, player, eq, pet, skill_id, equipped, target_defense=0):
        skill=SKILLS[skill_id]
        base=random.randint(45,75)+int(player["level"])*16+eq["attack"]+(int(pet["attack"])*2 if pet else 0)
        damage=max(1,int(base*float(skill.get("power",1.0)))-int(target_defense))
        crit=random.random()<0.08+player["luck"]/600
        if crit: damage*=2
        bond=1.0; bond_text=''
        if bool(self.cfg("skill_bond_enabled",None,True)):
            if skill_id=="火球" and "重击" in equipped: bond=1.15; bond_text='🔥火焰羁绊 +15%'
            elif skill_id=="冰枪" and "雷击" in equipped: bond=1.12; bond_text='❄️⚡冰雷羁绊 +12%'
            elif skill_id=="毒刃" and "连斩" in equipped: bond=1.18; bond_text='☠️连毒羁绊 +18%'
            elif skill_id=="吸血" and "护盾" in equipped: bond=1.12; bond_text='🩸守护羁绊 +12%'
            elif skill_id=="雷击" and "火球" in equipped: bond=1.10; bond_text='⚡🔥超载羁绊 +10%'
        return max(1,int(damage*bond)),crit,bond_text

    def boss_use_skill(self, group_id:str, user_id:str, name:str, skill_id:str)->Result:
        group=self.db.get_group(group_id)
        if not group or not group["boss_active"]: return Result("🐉 当前没有世界 Boss。")
        if skill_id not in SKILLS: return Result("❌ 技能不存在。输入 `/技能` 查看技能清单。")
        if skill_id!="普攻" and not any(r["skill_id"]==skill_id and r["equipped"] for r in self.db.get_skills(user_id)): return Result("❌ 这个技能不在你的技能栏。")
        cd=self.db.skill_cooldown_remaining(user_id,skill_id)
        if cd: return Result(f"⏳【{skill_id}】还在冷却中：{cd} 秒。")
        p,_=self.ensure_player(group_id,user_id,name)
        if int(p["hp"])<=0:
            return Result(f"💀 你的战斗生命已经耗尽（0/{p['max_hp']}）。\n请使用 `/使用 potion 1` 或等待生命恢复后再战斗。")
        cost=int(SKILLS[skill_id].get("cost",0))
        if p["stamina"]<cost: return Result(f"❤️ 体力不足，需要 {cost}。可以输入 `/使用 potion 1` 回复体力。")
        if cost:self.db.change_stamina(group_id,user_id,-cost)
        eq=self.db.get_equipped_stats(group_id,user_id); pet=self.db.get_active_pet(group_id,user_id); equipped={r["skill_id"] for r in self.db.get_skills(user_id) if r["equipped"]}
        damage,crit,bond_text=self._skill_damage(p,eq,pet,skill_id,equipped,0)
        hp=max(0,int(group["boss_hp"])-damage); self.db.update_group(group_id,boss_hp=hp); self.db.add_boss_damage(group_id,user_id,damage); self.db.update_player_metrics(group_id,user_id,total_boss_damage=damage); self.db.set_skill_cooldown(user_id,skill_id,int(SKILLS[skill_id].get("cooldown",0)))
        xp=min(1200,120+damage//3); player,ups=self.db.change_exp(group_id,user_id,xp)
        if hp<=0:
            return Result(self.finish_boss(group_id,user_id),ups)
        # Boss retaliates after every successful hit.
        boss_skill_text = ""
        boss_aoe = random.random() * 100 < max(0, min(100, int(self.cfg("boss_skill_chance_percent", group_id, 22) or 22)))
        if boss_aoe:
            boss_skill = random.choice([
                ("灭世震波", 1.55, "范围伤害！"),
                ("寒霜领域", 1.30, "范围减伤压制！"),
                ("地狱烈焰", 1.70, "爆发范围灼烧！"),
            ])
            taken=max(1,int(group["boss_hp"]*0.0005)+random.randint(6,18)-eq["defense"]//4)
            taken=int(taken*boss_skill[1])
            boss_skill_text=f" 🐉 Boss 施放【{boss_skill[0]}】造成{boss_skill[2]}"
            # Hit up to three currently active group members, but always include the acting player.
            raw=self.db.raw if hasattr(self.db, "raw") else self.db
            members=raw.fetchall("SELECT user_id FROM group_members WHERE group_id=? ORDER BY last_seen_at DESC LIMIT 12", (group_id,))
            targets=[]
            for r in members:
                uid=str(r["user_id"])
                if uid == user_id or random.random() < 0.28:
                    targets.append(uid)
            if user_id not in targets: targets.insert(0,user_id)
            targets=targets[:3]
            extra_lines=[]
            for uid in targets:
                if uid==user_id:
                    hit=taken
                else:
                    other_eq=raw.get_equipped_stats(group_id,uid) if hasattr(raw,"get_equipped_stats") else {"defense":0}
                    hit=max(1,taken-int(other_eq.get("defense",0))//5)
                self.db.change_hp(group_id,uid,-hit)
                if uid != user_id: extra_lines.append(f"• 影响了 {uid}：-{hit} HP")
            group_extra = ("\n" + "\n".join(extra_lines)) if extra_lines else ""
        else:
            taken=max(1,int(group["boss_hp"]*0.0005)+random.randint(6,18)-eq["defense"]//4)
            group_extra = ""
            self.db.change_hp(group_id,user_id,-taken)
        hp_now=self.db.get_player("__GLOBAL_USER__",user_id)["hp"]
        if hp_now<=0:
            self.db.stop_auto_battle(group_id,user_id)
            return Result(f"💀 Boss 将你击倒了！\n❤️ 战斗生命：0/{p['max_hp']}\n建议使用 `/使用 potion 1` 恢复生命后再继续。")
        impact = f"\n🐉 {boss_skill_text}" if boss_skill_text else ""
        return Result(f"⚔️ 你使用【{skill_id}】对 Boss 造成 {fmt_num(damage)} 点伤害{'｜暴击' if crit else ''}。\n🐉 Boss HP：{fmt_num(hp)}/{fmt_num(group['boss_max_hp'])}{impact}\n💥 Boss 反击，你损失 {taken} 战斗生命。{group_extra}\n⭐ +{xp} EXP" + (f"\n✨ {bond_text}" if bond_text else '') + "\n\n下一步：可继续 `/技能使用 技能名`、`/攻击`，或开启 `/自动战斗 Boss`。",ups)

    def auto_battle_start(self, group_id:str,user_id:str,name:str,target:str="") -> str:
        if not bool(self.cfg("auto_battle_enabled",group_id,True)):
            return "🤖 管理员已关闭自动战斗系统。"
        target=(target or "").lower()
        if target in {"boss","世界boss","世界"}:
            g=self.db.get_group(group_id)
            if not g or not g["boss_active"]: return "🐉 当前没有 Boss，无法开启 Boss 自动战斗。"
            self.db.start_auto_battle(group_id,user_id,"boss","auto"); return "🤖 已开启 Boss 自动战斗。系统会按间隔自动选择可用技能攻击；想停止请输入 `/自动战斗 关闭`。"
        row=self.db.get_active_monster(group_id,user_id)
        if not row: return "👹 当前没有怪物。先 `/探索` 遇到怪物后，再输入 `/自动战斗 开启`。"
        self.db.start_auto_battle(group_id,user_id,"monster","auto"); return f"🤖 已开启对【{row['monster_name']}】的自动战斗。系统会自动轮换技能；输入 `/自动战斗 关闭` 可随时停止。"

    def auto_battle_stop(self,group_id:str,user_id:str)->str:
        self.db.stop_auto_battle(group_id,user_id); return "🛑 已停止自动战斗。"

    def auto_battle_status(self,group_id:str,user_id:str)->str:
        row=self.db.get_auto_battle(group_id,user_id)
        if not row:return "🤖 当前未开启自动战斗。\n用法：`/自动战斗 开启`（当前怪物）或 `/自动战斗 Boss`。"
        return f"🤖 自动战斗：开启\n目标：{'世界 Boss' if row['target_type']=='boss' else '当前怪物'}\n已执行：{row['turns']} 回合\n停止：`/自动战斗 关闭`"

    def auto_battle_tick(self,group_id:str,user_id:str)->str|None:
        ab=self.db.get_auto_battle(group_id,user_id)
        if not ab:return None
        p=self.db.get_player("__GLOBAL_USER__",user_id)
        if not p or p["banned"]: self.db.stop_auto_battle(group_id,user_id); return None
        if ab["target_type"]=="boss":
            g=self.db.get_group(group_id)
            if not g or not g["boss_active"]: self.db.stop_auto_battle(group_id,user_id); return "🤖 Boss 已结束，自动战斗已停止。"
            skills=[r["skill_id"] for r in self.db.get_skills(user_id) if r["equipped"] and self.db.skill_cooldown_remaining(user_id,r["skill_id"])==0]
            sid=next((x for x in reversed(skills) if x!="普攻"),"普攻")
            result=self.boss_use_skill(group_id,user_id,p["name"],sid)
        else:
            row=self.db.get_active_monster(group_id,user_id)
            if not row: self.db.stop_auto_battle(group_id,user_id); return "🤖 怪物已结束，自动战斗已停止。"
            skills=[r["skill_id"] for r in self.db.get_skills(user_id) if r["equipped"] and self.db.skill_cooldown_remaining(user_id,r["skill_id"])==0]
            sid=next((x for x in reversed(skills) if x!="普攻"),"普攻")
            result=self._monster_hit(group_id,user_id,p["name"],sid)
        self.db.touch_auto_battle(group_id,user_id,int(time.time()))
        text=result.text
        current=self.db.get_auto_battle(group_id,user_id)
        max_turns=int(self.cfg("auto_battle_max_turns",group_id,80))
        done=any(x in text for x in ("击败","已结束","没有 Boss","没有可攻击","体力不足")) or (current and int(current["turns"])>=max_turns)
        if done:
            self.db.stop_auto_battle(group_id,user_id)
            if current and int(current["turns"])>=max_turns and not any(x in text for x in ("击败","已结束")):
                text += f"\n\n⏹️ 自动战斗已达到 {max_turns} 回合上限，已自动停止。"
            return "🤖【自动战斗结束】\n"+text
        every=max(1,int(self.cfg("auto_battle_broadcast_every",group_id,3)))
        turns=int(current["turns"]) if current else 0
        if turns % every != 0:
            return None
        return "🤖【自动战斗进度】\n"+text+"\n\n💡 仍在自动战斗中；输入 `/自动战斗 状态` 查看状态，`/自动战斗 关闭` 可停止。"

    def _random_equipment(self, difficulty: int, level: int, attack: int):
        r = random.random() * 100
        if r < 58:
            rarity = "普通"
        elif r < 82:
            rarity = "优秀"
        elif r < 94:
            rarity = "稀有"
        elif r < 98:
            rarity = "史诗"
        elif r < 99.8:
            rarity = "传说"
        else:
            rarity = "神话"
        mult = RARITY_ORDER.index(rarity) + 1
        names = [
            ("武器", "猎人短刃"),
            ("武器", "旅行长剑"),
            ("护甲", "探险皮甲"),
            ("护甲", "符文护甲"),
            ("鞋子", "轻风战靴"),
            ("饰品", "幸运吊坠"),
        ]
        slot, base_name = random.choice(names)
        item = {
            "name": base_name,
            "slot": slot,
            "level": max(1, level + random.randint(-1, 1)),
            "attack": (4 + difficulty * 2 + attack // 8) * mult if slot == "武器" else 0,
            "defense": (3 + difficulty * 2) * mult if slot in {"护甲", "鞋子"} else 0,
            "explore_bonus": min(12, difficulty + mult) if slot in {"鞋子", "饰品"} else 0,
            "equipped": False,
        }
        return rarity, item

    def map_text(self, group_id: str) -> str:
        group = self.db.get_group(group_id)
        current = group["world_location"] if group else "新手村"
        lines = ["🌎 【群聊世界地图】"]
        for name, difficulty, desc in LOCATIONS:
            mark = "📍" if name == current else "▫️"
            lines.append(f"{mark} {name} {'⭐' * difficulty}｜{desc}")
        return "\n".join(lines) + f"\n\n当前群世界地点：{current}"

    def inventory_text(self, group_id: str, user_id: str, name: str) -> str:
        self.ensure_player(group_id, user_id, name)
        items = self.db.get_inventory(group_id, user_id)
        equips = self.db.get_equipment(group_id, user_id)
        lines = ["🎒 【背包】"]
        if not items:
            lines.append("物品：空")
        else:
            lines.append("物品：")
            for item in items[:30]:
                desc=ITEM_INFO.get(item['item_id'],(item['item_name'],'可用于后续玩法。'))[1]
                lines.append(f"• {item['item_id']}｜{item['item_name']} ×{item['qty']}｜{desc}")
        if equips:
            lines.append("\n⚔️ 装备：")
            for eq in equips[:20]:
                flag = " [已装备]" if eq["equipped"] else ""
                stats = []
                if eq["attack"]:
                    stats.append(f"攻击+{eq['attack']}")
                if eq["defense"]:
                    stats.append(f"防御+{eq['defense']}")
                if eq["explore_bonus"]:
                    stats.append(f"探索+{eq['explore_bonus']}%")
                lines.append(f"• #{eq['id']} {eq['name']} [{eq['rarity']}] Lv.{eq['level']} {' '.join(stats)}{flag}")
        return "\n".join(lines)

    def pets_text(self, group_id: str, user_id: str, name: str) -> str:
        self.ensure_player(group_id, user_id, name)
        pets = self.db.get_pets(group_id, user_id)
        lines = ["🐾 【宠物图鉴】"]
        if not pets:
            return "🐾 你还没有宠物。\n使用 `/抽宠物`，500金币抽取一次。"
        for pet in pets[:30]:
            active = " ⭐出战" if pet["active"] else ""
            lines.append(
                f"#{pet['id']} {pet['species']} [{pet['rarity']}] Lv.{pet['level']} "
                f"⚔️{pet['attack']} 🛡️{pet['defense']} 🍀{pet['luck']}｜{pet['personality']}{active}"
            )
        lines.append("\n指令：`/抽宠物` 或 `/出战宠物 宠物ID`")
        return "\n".join(lines)

    def draw_pet(self, group_id: str, user_id: str, name: str) -> Result:
        if not bool(self.cfg("pet_enabled", group_id, True)):
            return Result("🐾 管理员已关闭宠物系统。")
        player, _ = self.ensure_player(group_id, user_id, name)
        if self.db.cooldown_remaining(group_id, user_id, "pet_draw"):
            return Result("🐾 抽宠物冷却中，请稍后再试。")
        cost = int(self.cfg("pet_draw_cost", group_id, 500))
        if player["coins"] < cost:
            return Result(f"💰 金币不足，需要 {cost} 金币。")
        self.db.wallet_change(group_id, user_id, coins_delta=-cost, kind="pet_draw", note="抽宠物")
        roll = random.random() * 100
        acc = 0.0
        rarity = "普通"
        rarity_cfg = self.json_cfg("pet_rarity_json", group_id)
        rarity_table = []
        if rarity_cfg:
            for key in PET_RARITIES:
                try:
                    rarity_table.append((key, float(rarity_cfg.get(key, PET_RARITIES[key][0]))))
                except Exception:
                    rarity_table.append((key, float(PET_RARITIES[key][0])))
        else:
            rarity_table = [(k, float(v[0])) for k, v in PET_RARITIES.items()]
        total_weight = sum(max(0, x[1]) for x in rarity_table) or 100.0
        roll = roll / 100.0 * total_weight
        acc = 0.0
        for r, weight in rarity_table:
            acc += max(0, weight)
            if roll <= acc:
                rarity = r
                break
        info = PET_RARITIES[rarity]
        species = random.choice(PET_SPECIES)
        personality = random.choice(PERSONALITIES)
        pet_id = self.db.create_pet(
            group_id, user_id, species, rarity, 1,
            int(info[1] + random.randint(0, 3)),
            int(info[2] + random.randint(0, 3)),
            int(info[3] + random.randint(0, 4)),
            personality,
        )
        self.db.set_cooldown(group_id, user_id, "pet_draw", int(self.cfg("pet_draw_cooldown_seconds", group_id, 3)))
        pets = self.db.get_pets(group_id, user_id)
        achievement = ""
        if len(pets) >= 3 and self.db.unlock_achievement(group_id, user_id, "collector"):
            achievement = "\n📜 解锁成就【收藏家】"
        return Result(f"🎉 抽宠物成功！\n🐾 {species}\n稀有度：{rarity}\n性格：{personality}\n编号：#{pet_id}{achievement}")

    def activate_pet(self, group_id: str, user_id: str, pet_id: int, name: str) -> str:
        self.ensure_player(group_id, user_id, name)
        pet = self.db.activate_pet(group_id, user_id, pet_id)
        if not pet:
            return "❌ 找不到这个宠物。"
        return f"🐾 已让【{pet['species']}】出战。\n⚔️ 攻击：{pet['attack']}\n🛡️ 防御：{pet['defense']}\n🍀 幸运：{pet['luck']}"

    def _shop_catalog(self, group_id: str) -> dict[str, tuple[str, int, str]]:
        catalog = dict(SHOP)
        custom = self.json_cfg("shop_catalog_json", group_id)
        for item_id, value in custom.items():
            if isinstance(value, list) and len(value) >= 3:
                try:
                    catalog[str(item_id)] = (str(value[0]), int(value[1]), str(value[2]))
                except Exception:
                    pass
        return catalog

    def _shop_unit_price(self, group_id: str, player, base_price: int) -> tuple[int, dict[str, int]]:
        profession_discount = 10 if str(player["profession"] or "") == "商人" else 0
        global_discount = max(0, min(90, int(self.cfg("shop_discount_percent", group_id, 0) or 0)))
        event_discount = max(0, min(90, int(self._event_effect(group_id, "shop_discount_percent", 0) or 0)))
        # Apply independent discounts sequentially; every place that displays or
        # charges a price calls this function so event prices cannot drift.
        multiplier = (1 - profession_discount / 100) * (1 - global_discount / 100) * (1 - event_discount / 100)
        price = max(1, int(int(base_price) * multiplier))
        return price, {"profession": profession_discount, "global": global_discount, "event": event_discount}

    def shop_text(self, group_id: str) -> str:
        if not bool(self.cfg("shop_enabled", group_id, True)):
            return "🏪 管理员已关闭世界商店。"
        catalog = self._shop_catalog(group_id)
        # The world shop is public; profession/event discounts are calculated
        # against the viewer's role where possible. Default display uses no profession discount.
        lines = ["🏪 【世界商店】", ""]
        group_event_discount = max(0, min(90, int(self._event_effect(group_id, "shop_discount_percent", 0) or 0)))
        global_discount = max(0, min(90, int(self.cfg("shop_discount_percent", group_id, 0) or 0)))
        for item_id, (name, base_price, desc) in catalog.items():
            multiplier = (1 - global_discount / 100) * (1 - group_event_discount / 100)
            price = max(1, int(int(base_price) * multiplier))
            discount_note = []
            if global_discount: discount_note.append(f"全局-{global_discount}%")
            if group_event_discount: discount_note.append(f"事件-{group_event_discount}%")
            note = f"（{'；'.join(discount_note)}）" if discount_note else ""
            lines.append(f"{item_id}｜{name}｜💰 {price}｜{desc}{note}")
        lines.append("\n购买：`/购买 物品ID 数量`；职业‘商人’购买时再额外享受 10% 折扣。")
        return "\n".join(lines)

    def buy(self, group_id: str, user_id: str, name: str, item_id: str, qty: int) -> str:
        if not bool(self.cfg("shop_enabled", group_id, True)):
            return "🏪 管理员已关闭世界商店。"
        player, _ = self.ensure_player(group_id, user_id, name)
        catalog = self._shop_catalog(group_id)
        if item_id not in catalog:
            return "❌ 商店里没有这个物品。发送 `/商店` 查看。"
        qty = max(1, min(99, int(qty)))
        item_name, base_price, desc = catalog[item_id]
        unit_price, discounts = self._shop_unit_price(group_id, player, base_price)
        price = unit_price * qty
        if player["coins"] < price:
            return f"💰 金币不足，需要 {fmt_num(price)}，当前 {fmt_num(player['coins'])}。"
        self.db.wallet_change(group_id, user_id, coins_delta=-price, kind="shop", note=f"购买{item_name}×{qty}" )
        if item_id == "potion":
            self.db.change_stamina(group_id, user_id, 25 * qty)
        elif item_id == "super_potion":
            self.db.change_stamina(group_id, user_id, 100 * qty)
        elif item_id == "food":
            self.db.change_stamina(group_id, user_id, 10 * qty); self.db.change_hp(group_id,user_id,10*qty)
            self.db.change_exp(group_id, user_id, 40 * qty)
        elif item_id == "energy_drink":
            self.db.change_stamina(group_id, user_id, 50 * qty); self.db.change_exp(group_id, user_id, 100 * qty)
        else:
            self.db.add_item(group_id, user_id, item_id, item_name, qty)
        discount_note = []
        if discounts["profession"]: discount_note.append("商人 -10%")
        if discounts["global"]: discount_note.append(f"全局 -{discounts['global']}%")
        if discounts["event"]: discount_note.append(f"事件 -{discounts['event']}%")
        extra = f"\n🏷️ {' + '.join(discount_note)}" if discount_note else ""
        return f"✅ 购买成功：{item_name} ×{qty}\n💰 消耗：{fmt_num(price)}{extra}\n📦 {desc}"

    def equip(self, group_id: str, user_id: str, equip_id: int, name: str) -> str:
        self.ensure_player(group_id, user_id, name)
        row = self.db.fetchone("SELECT * FROM equipment WHERE id=? AND group_id=? AND user_id=?", (equip_id, "__GLOBAL_USER__", user_id))
        if not row:
            return "❌ 找不到这件装备。"
        with self.db.transaction() as conn:
            conn.execute("UPDATE equipment SET equipped=0 WHERE group_id=? AND user_id=? AND slot=?", (group_id, user_id, row["slot"]))
            conn.execute("UPDATE equipment SET equipped=1 WHERE id=?", (equip_id,))
        return f"✅ 已装备【{row['name']}】[{row['rarity']}]。"

    def tasks_text(self, group_id: str, user_id: str, name: str) -> str:
        if not bool(self.cfg("daily_tasks_enabled", group_id, True)):
            return "📋 管理员已关闭每日任务。"
        self.ensure_player(group_id, user_id, name)
        day = self.db.get_today_key()
        rows = self.db.ensure_daily_tasks(group_id, user_id, day)
        names = {"checkin": "完成签到", "explore": "完成探索", "game": "完成小游戏"}
        lines = ["📋 【今日任务】"]
        for row in rows:
            flag = "✅" if row["completed"] else "⬜"
            lines.append(f"{flag} {names.get(row['task_id'], row['task_id'])} {row['progress']}/{row['target']} ｜ +{row['reward_coins']}💰 +{row['reward_exp']}⭐")
        return "\n".join(lines)

    def claim_completed_tasks(self, group_id: str, user_id: str) -> str:
        if not bool(self.cfg("daily_tasks_enabled", group_id, True)):
            return "📋 管理员已关闭每日任务。"
        day = self.db.get_today_key()
        rows = self.db.get_task_rows(group_id, user_id, day)
        if not rows:
            return "没有可领取的任务。先发送 `/任务` 查看。"
        reward_coin = reward_exp = 0
        claimed = 0
        for row in rows:
            if row["completed"]:
                key = f"task_claim_{day}_{row['task_id']}"
                if self.db.cooldown_remaining(group_id, user_id, key) == 0:
                    reward_coin += row["reward_coins"]
                    reward_exp += row["reward_exp"]
                    claimed += 1
                    self.db.set_cooldown(group_id, user_id, key, 86400)
        if claimed == 0:
            return "没有新的已完成任务可领取。"
        self.db.wallet_change(group_id, user_id, coins_delta=reward_coin, kind="daily_task", note=f"完成{claimed}项任务")
        self.db.change_exp(group_id, user_id, reward_exp)
        all_done = all(r["completed"] for r in rows)
        achievement = ""
        if all_done and self.db.unlock_achievement(group_id, user_id, "daily_master"):
            achievement = "\n📜 解锁成就【任务达人】"
        return f"🎁 领取成功！\n💰 +{reward_coin}\n⭐ +{reward_exp}" + achievement

    def achievements_text(self, group_id: str, user_id: str, name: str) -> str:
        if not bool(self.cfg("achievements_enabled", group_id, True)):
            return "🏆 管理员已关闭成就系统。"
        self.ensure_player(group_id, user_id, name)
        unlocked = {r["key"] for r in self.db.get_achievements(group_id, user_id)}
        lines = ["🏆 【成就】"]
        for key, (title, desc) in ACHIEVEMENT_INFO.items():
            flag = "✅" if key in unlocked else "⬜"
            lines.append(f"{flag} {title}｜{desc}")
        return "\n".join(lines)

    def rankings(self, group_id: str) -> str:
        labels = [("level", "⭐ 等级榜"), ("coins", "💰 财富榜"), ("renown", "🌟 声望榜"), ("luck", "🍀 幸运榜")]
        lines = ["🏆 【群聊世界排行榜】"]
        for order, title in labels:
            lines.append(title)
            rows = self.db.get_top_players(group_id, order, 5)
            if not rows:
                lines.append("暂无玩家")
            else:
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                for i, row in enumerate(rows):
                    value = row[order]
                    if order == "level":
                        value = f"Lv.{row['level']} / {row['exp']}EXP"
                    else:
                        value = fmt_num(int(value))
                    lines.append(f"{medals[i]} {row['name']}｜{value}")
        return "\n".join(lines)

    def boss_status(self, group_id: str) -> str:
        boss = self.db.get_group(group_id)
        if not boss or not boss["boss_active"]:
            return "🐉 当前没有活动中的世界 Boss。"
        hp = max(0, int(boss["boss_hp"]))
        max_hp = max(1, int(boss["boss_max_hp"]))
        ratio = hp / max_hp
        blocks = int(ratio * 10)
        bar = "🟩" * blocks + "⬜" * (10 - blocks)
        lines = [
            f"🐉 【世界 Boss】 {boss['boss_name']}",
            f"HP：{fmt_num(hp)} / {fmt_num(max_hp)}",
            f"{bar} {ratio:.0%}",
            "",
            "使用 `/攻击` 参与战斗。",
        ]
        ranking = self.db.get_boss_ranking(group_id, 5)
        if ranking:
            lines.append("\n⚔️ 当前贡献榜：")
            for i, row in enumerate(ranking, 1):
                lines.append(f"{i}. {row['name']}｜{fmt_num(row['damage'])} 伤害")
        return "\n".join(lines)

    def spawn_boss(self, group_id: str, boss_name: str | None = None) -> str:
        if not bool(self.cfg("boss_enabled", group_id, True)):
            return "🐉 管理员已关闭世界 Boss。"
        group = self.db.get_group(group_id)
        if group and group["boss_active"]:
            return "🐉 当前已经有 Boss，先把它击败或结束。"
        names = ["远古黑龙", "深渊巨兽", "熔岩领主", "虚空魔神", "冰霜女王"]
        name = boss_name or random.choice(names)
        hp = int(self.cfg("boss_max_hp", group_id, 100000))
        now = datetime.now(timezone.utc)
        self.db.reset_boss_damage(group_id)
        self.db.update_group(
            group_id,
            boss_active=1,
            boss_name=name,
            boss_hp=hp,
            boss_max_hp=hp,
            boss_started_at=now.isoformat(timespec="seconds"),
            boss_ends_at=(now.timestamp() + int(self.cfg("boss_duration_hours", group_id, 4)) * 3600),
            last_boss_at=now.isoformat(timespec="seconds"),
        )
        return f"🐉【世界 Boss 降临】\n{ name } 出现在群聊世界！\nHP：{fmt_num(hp)}\n\n快使用 `/攻击` 参与战斗！"

    def attack_boss(self, group_id: str, user_id: str, name: str) -> Result:
        player, _ = self.ensure_player(group_id, user_id, name)
        if int(player["hp"])<=0:
            return Result(f"💀 你的战斗生命已经耗尽（0/{player['max_hp']}）。\n请使用 `/使用 potion 1` 恢复生命后再战斗。")
        group = self.db.get_group(group_id)
        if not group or not group["boss_active"]:
            return Result("🐉 当前没有可攻击的世界 Boss。")
        remaining = self.db.cooldown_remaining(group_id, user_id, "boss_attack")
        if remaining:
            return Result(f"⚔️ 攻击冷却中，还有 {remaining} 秒。")
        if player["stamina"] < 10:
            return Result("❤️ 体力不足，需要至少 10 点。")
        self.db.change_stamina(group_id, user_id, -10)
        equip = self.db.get_equipped_stats(group_id, user_id)
        pet = self.db.get_active_pet(group_id, user_id)
        pet_atk = pet["attack"] if pet else 0
        base = int(self.cfg("boss_damage_base", group_id, 80))
        damage = random.randint(max(1, base // 2), base * 2) + player["level"] * 18 + equip["attack"] + pet_atk * 3
        if player["profession"] == "战士":
            damage = int(damage * 1.2)
        if random.random() < 0.08 + player["luck"] / 500:
            damage *= 2
            crit = True
        else:
            crit = False
        damage = max(1, damage)
        new_hp = max(0, int(group["boss_hp"]) - damage)
        self.db.update_group(group_id, boss_hp=new_hp)
        self.db.add_boss_damage(group_id, user_id, damage)
        self.db.update_player_metrics(group_id, user_id, total_boss_damage=damage)
        self.db.log_action(group_id, user_id, "boss_attack", f"damage={damage}")
        self.db.set_cooldown(group_id, user_id, "boss_attack", int(self.cfg("boss_attack_cooldown_seconds", group_id, 10)))
        xp = min(1000, 100 + damage // 3)
        self.db.change_exp(group_id, user_id, xp)
        day = self.db.get_today_key()
        self.db.ensure_daily_tasks(group_id, user_id, day)
        self.db.ensure_daily_tasks(group_id, user_id, day)
        self.db.progress_task(group_id, user_id, day, "game", 1)
        if new_hp <= 0:
            return Result(self.finish_boss(group_id, user_id), achievement="boss_defeat")
        taken=max(1,int(group["boss_hp"]*0.0005)+random.randint(6,18)-equip["defense"]//4)
        self.db.change_hp(group_id,user_id,-taken)
        hp_now=self.db.get_player("__GLOBAL_USER__",user_id)["hp"]
        if hp_now<=0:
            self.db.stop_auto_battle(group_id,user_id)
            return Result(f"⚔️ {name} 对【{group['boss_name']}】造成 {fmt_num(damage)} 点伤害{'！暴击' if crit else ''}\n🐉 剩余 HP：{fmt_num(new_hp)}\n💥 Boss 反击，你被击倒了。\n❤️ 战斗生命：0/{player['max_hp']}\n请使用 `/使用 potion 1` 恢复生命。")
        return Result(
            f"⚔️ {name} 对【{group['boss_name']}】造成 {fmt_num(damage)} 点伤害{'！暴击' if crit else ''}\n"
            f"🐉 剩余 HP：{fmt_num(new_hp)}\n💥 Boss 反击：-{taken} 战斗生命\n⭐ +{xp} EXP\n\n下一步：继续 `/攻击`，也可以用 `/技能使用 技能名` 或 `/自动战斗 Boss`。"
        )

    def finish_boss(self, group_id: str, finisher_id: str | None = None) -> str:
        group = self.db.get_group(group_id)
        if not group:
            return "❌ 找不到群世界。"
        name = group["boss_name"] or "世界 Boss"
        ranking = self.db.get_boss_ranking(group_id, 10)
        lines = [f"🏆【{name} 已被击败！】", "", "贡献榜："]
        for i, row in enumerate(ranking, 1):
            top = int(self.cfg("boss_top_reward", group_id, 8000))
            decay = int(self.cfg("boss_reward_decay", group_id, 600))
            floor = int(self.cfg("boss_min_reward", group_id, 1000))
            reward = max(floor, top - (i - 1) * decay)
            self.db.wallet_change(group_id, row["user_id"], coins_delta=reward, kind="boss_reward", note=f"击败{name}贡献第{i}名")
            self.db.change_exp(group_id, row["user_id"], reward // 10)
            lines.append(f"{i}. {row['name']}｜{fmt_num(row['damage'])}伤害｜+{fmt_num(reward)}💰")
            self.db.unlock_achievement(group_id, row["user_id"], "boss_hunter")
        self.db.update_group(group_id, boss_active=0, boss_hp=0, boss_name=None, boss_max_hp=0, boss_started_at=None, boss_ends_at=None)
        self.db.reset_boss_damage(group_id)
        if finisher_id:
            self.db.wallet_change(group_id, finisher_id, gems_delta=2, kind="boss_finisher", note="Boss终结奖励")
            lines.append("\n💎 终结者额外获得：2 钻石")
        return "\n".join(lines)

    NPC_TEMPLATES = [
        {"id":"merchant","name":"米娅","role":"流浪商人","description":"她推着装满药剂与矿石的小车路过这里。","actions":[{"key":"trade","label":"看一眼特价货架","reward":"coins"},{"key":"talk","label":"和她聊聊","reward":"exp"}]},
        {"id":"fortune","name":"璃月","role":"占星师","description":"她说今晚的星星格外愿意照顾冒险者。","actions":[{"key":"fortune","label":"进行一次占卜","reward":"luck"},{"key":"talk","label":"听她讲故事","reward":"exp"}]},
        {"id":"hunter","name":"洛克","role":"老猎人","description":"他刚从黑森林回来，手里还拎着一串狼牙。","actions":[{"key":"hunt","label":"接下猎人赠礼","reward":"item"},{"key":"talk","label":"听取狩猎技巧","reward":"exp"}]},
        {"id":"collector","name":"阿格斯","role":"收藏家","description":"只要是稀奇古怪的材料，他都愿意出钱收。","actions":[{"key":"trade","label":"拿材料换金币","reward":"coins"},{"key":"talk","label":"展示你的收藏","reward":"renown"}]},
        {"id":"traveler","name":"伊恩","role":"远行者","description":"他正在寻找下一条前往虚空领域的路线。","actions":[{"key":"quest","label":"接受旅行者赠礼","reward":"item"},{"key":"talk","label":"听地图情报","reward":"exp"}]},
    ]

    def spawn_npc(self, group_id: str) -> str:
        if not self.group_feature_enabled(group_id,"npc_enabled","npc_enabled",True):
            return "🧑‍🌾 本群随机 NPC 系统当前已关闭。"
        npc=random.choice(self.NPC_TEMPLATES)
        duration=max(5,int(self.cfg("npc_duration_minutes",group_id,30)))
        raw=self.db.raw if hasattr(self.db,"raw") else self.db
        raw.set_current_npc(group_id,npc, int(time.time())+duration*60)
        options=" ｜ ".join(f"{i+1}.{a['label']}" for i,a in enumerate(npc['actions']))
        raw.log_world_event(group_id,"npc",npc["name"],npc["description"],None,"NPC",group_id)
        return f"🧑‍🌾【神秘 NPC 出现】\n{npc['name']}（{npc['role']}）来到了群聊世界！\n{npc['description']}\n\n可互动：{options}\n发送 `/NPC` 查看详情。\n⏳ NPC 将停留约 {duration} 分钟。"

    def npc_status(self, group_id: str) -> str:
        npc=self.db.raw.get_current_npc(group_id) if hasattr(self.db,"raw") else self.db.get_current_npc(group_id)
        if not npc:
            return "🧑‍🌾 当前没有 NPC。等待世界随机事件即可。"
        remain=max(1,(npc["expires_at"]-int(time.time()))//60)
        options="\n".join(f"{i+1}. `/NPC {a['key']}` — {a['label']}" for i,a in enumerate(npc["actions"]))
        return f"🧑‍🌾【{npc['name']} · {npc['role']}】\n{npc['description']}\n\n{options}\n\n⏳ 剩余约 {remain} 分钟"

    def npc_interact(self, group_id: str, user_id: str, name: str, action: str) -> str:
        raw=self.db.raw if hasattr(self.db,"raw") else self.db
        npc=raw.get_current_npc(group_id)
        if not npc:
            return "🧑‍🌾 当前没有可互动的 NPC。"
        key=(action or "talk").strip().lower()
        mapping={str(a.get("key")):a for a in npc.get("actions",[])}
        if key.isdigit():
            idx=int(key)-1
            acts=npc.get("actions",[])
            if 0<=idx<len(acts): key=str(acts[idx].get("key"))
        act=mapping.get(key)
        if not act:
            return self.npc_status(group_id)
        if raw.npc_action_claimed(group_id,user_id,npc["id"],key):
            return "🧑‍🌾 这位 NPC 已经给过你这份奖励了。试试其他互动选项。"
        player,_=self.ensure_player(group_id,user_id,name)
        reward=act.get("reward")
        raw.mark_npc_action(group_id,user_id,npc["id"],key)
        if reward=="coins":
            gain=random.randint(600,1600); raw.wallet_change(group_id,user_id,coins_delta=gain,kind="npc",note=f"NPC {npc['name']} 交换")
            return f"🧑‍🌾 {npc['name']}：不错的材料！我愿意支付 💰{gain}。\n你的互动已完成。"
        if reward=="item":
            iid,iname,qty=random.choice([("potion","体力药水",1),("ore","强化矿石",2),("food","冒险便当",2),("crystal","强化水晶",1)])
            raw.add_item(group_id,user_id,iid,iname,qty)
            return f"🎁 {npc['name']} 送给你【{iname}】 ×{qty}。\n你的互动已完成。"
        if reward=="luck":
            raw.set_global_player_fields(user_id,{"luck":min(9999,int(player["luck"])+3)})
            return f"🔮 {npc['name']} 为你占卜：幸运 +3！\n当前幸运：{int(player['luck'])+3}"
        if reward=="renown":
            raw.set_global_player_fields(user_id,{"renown":min(999999,int(player["renown"])+20)})
            return f"🏅 收藏家被你的收藏打动了！声望 +20。\n当前声望：{int(player['renown'])+20}"
        gain=random.randint(120,320); raw.change_exp(group_id,user_id,gain)
        return f"📖 你和 {npc['name']} 聊了很久，获得 {gain} EXP。\n这次聊天已记录。"

    def random_tip(self, group_id: str) -> str:
        tips=[
            "💡 小提示：第一次进入玩法，建议按 `/签到 → /任务 → /探索` 的顺序开始。",
            "💡 小提示：探索遇到怪物后不要继续 `/探索`，先用 `/怪物` 查看，再用 `/攻击怪物` 或技能战斗。",
            "💡 小提示：输入 `/技能` 会列出所有技能、消耗、冷却和当前技能栏，组合还能触发羁绊。",
            "💡 小提示：金币是长期资源，商店购买前先看清商品作用；背包中的材料后续可能用于任务和强化。",
            "💡 小提示：体力会自动恢复。准备探索前留一点体力，应对随机怪物和危险事件会更从容。",
            "💡 小提示：击败普通怪物可以得到金币、EXP 和材料；高等级怪物奖励更高，但反击也更疼。",
            "💡 小提示：技能不是越强越好。重击、连斩、元素技能之间搭配得当，收益通常高于单一技能。",
            "💡 小提示：`/背包` 可以直接查看物品作用，常用恢复品可以用 `/使用 物品ID 数量`。",
            "💡 小提示：同一个角色可以跨群成长。换到另一个已开启群聊世界的群，不会丢等级、金币和装备。",
            "💡 小提示：世界事件会随机在某个群主动出现，但系统会限制主动消息频率，避免刷屏。",
            "💡 小提示：世界 Boss 更适合多人一起打，看看 `/Boss` 的贡献榜，输出越高奖励通常越好。",
            "💡 小提示：每天把 `/任务` 做完，再去挑战 Boss 或高级探索，是比较稳定的成长路线。",
            "💡 小提示：遇到陌生怪物先看 `/怪物`，确认 HP 和防御，再决定用普攻还是高倍率技能。",
            "💡 小提示：技能冷却期间可以换用其他技能，不必站着等；合理轮转能让战斗更顺。",
            "💡 小提示：低等级不要急着转职。先把基础装备和宠物养起来，10级以后职业选择会更多。",
            "💡 小提示：第一次不知道做什么？直接输入 `/世界` 看当前世界状态和推荐玩法。",
            "💡 小提示：忘记任何指令时输入 `/帮助`；想重新看教程可以输入 `/教程`。",
            "💡 小提示：新手礼包只是起点。你的长期目标是升级、收集、强化、技能搭配、Boss 和排行榜。",
        ]
        custom=self.json_cfg("tip_catalog_json",group_id)
        if isinstance(custom,dict):
            vals=[str(v) for v in custom.values() if str(v).strip()]
            if vals: tips=vals
        return random.choice(tips)

    def random_world_event(self, group_id: str) -> str:
        if not self.group_feature_enabled(group_id, "world_event_enabled", "enable_auto_world_events", True):
            return "🌤️ 本群世界事件系统当前已关闭。"
        custom = self.json_cfg("event_catalog_json", group_id)
        choices = []
        if custom:
            for key, value in custom.items():
                if isinstance(value, str):
                    choices.append({"key":str(key),"title":str(key),"description":value,"weather":str(key),"duration":60,"effects":{}})
                elif isinstance(value, dict) and value.get("description"):
                    effects=value.get("effects", {}) if isinstance(value.get("effects", {}), dict) else {}
                    # Accept effects directly on the event for easier admin editing.
                    for k in ("shop_discount_percent","explore_coin_multiplier","explore_stamina_extra","explore_gem_bonus_percent","checkin_bonus_coins","monster_chance_bonus_percent","location_reward_location","location_reward_multiplier"):
                        if k in value: effects[k]=value[k]
                    choices.append({"key":str(key),"title":str(value.get("title",key)),"description":str(value["description"]),"weather":str(value.get("weather","特殊天气")),"duration":int(value.get("duration_minutes",60) or 60),"effects":effects})
        if not choices:
            choices = [
                {"key":"rain","title":"☔ 暴雨","description":"今日世界进入暴雨天气！探索金币 +20%，但探索额外消耗 2 体力。","weather":"暴雨","duration":60,"effects":{"explore_coin_multiplier":1.20,"explore_stamina_extra":2}},
                {"key":"sunny","title":"☀️ 晴空","description":"阳光普照！本事件持续期间，每次签到额外获得 100 金币。","weather":"晴天","duration":120,"effects":{"checkin_bonus_coins":100}},
                {"key":"meteor","title":"🌌 流星雨","description":"流星划过天空！本小时探索发现钻石的概率提高。","weather":"流星雨","duration":60,"effects":{"explore_gem_bonus_percent":10}},
                {"key":"volcano","title":"🌋 火山异动","description":"火山开始震动！进入熔岩火山区域时，探索金币奖励提高 60%。","weather":"火山异动","duration":90,"effects":{"location_reward_location":"熔岩火山","location_reward_multiplier":1.60}},
                {"key":"wolves","title":"🐺 狼群出没","description":"黑森林出现狼群！探索遇怪概率提高 12%。","weather":"狼群","duration":90,"effects":{"monster_chance_bonus_percent":12}},
                {"key":"merchant","title":"🛒 神秘商队","description":"神秘商人来到了群里！本事件期间所有商店商品额外 10% off。","weather":"商队","duration":60,"effects":{"shop_discount_percent":10}},
            ]
        event=random.choice(choices)
        now=int(datetime.now(timezone.utc).timestamp())
        expires=now+max(1,int(event.get("duration",60))*60)
        raw=self.db.raw if hasattr(self.db,"raw") else self.db
        raw.set_world_event(group_id,event["key"],expires,event.get("effects",{}))
        raw.update_group(group_id,world_weather=event.get("weather","特殊天气"),last_event_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        effect_lines=[]
        effects=event.get("effects",{})
        if effects.get("shop_discount_percent"): effect_lines.append(f"🏪 商店额外 -{effects['shop_discount_percent']}%")
        if effects.get("explore_coin_multiplier"): effect_lines.append(f"🗺️ 探索金币 ×{effects['explore_coin_multiplier']}")
        if effects.get("explore_gem_bonus_percent"): effect_lines.append(f"💎 探索发现钻石概率 +{effects['explore_gem_bonus_percent']}%")
        if effects.get("checkin_bonus_coins"): effect_lines.append(f"📅 每次签到额外 +{effects['checkin_bonus_coins']}💰")
        if effects.get("monster_chance_bonus_percent"): effect_lines.append(f"👹 遇怪概率 +{effects['monster_chance_bonus_percent']}%")
        if effects.get("explore_stamina_extra"): effect_lines.append(f"❤️ 探索额外消耗 +{effects['explore_stamina_extra']} 体力")
        if effects.get("location_reward_multiplier") and effects.get("location_reward_location"): effect_lines.append(f"📍 {effects['location_reward_location']} 收益 ×{effects['location_reward_multiplier']}")
        if effect_lines:
            event["description"] += "\n" + " ｜ ".join(effect_lines)
        raw.log_world_event(group_id,"world_event",event["title"],event["description"],None,event.get("weather"),None)
        return f"🌎【世界事件：{event['title']}】\n{event['description']}\n\n⏳ 持续约 {max(1,int(event.get('duration',60)))} 分钟。\n输入 `/世界` 查看当前世界状态。"

    def world_status(self, group_id: str) -> str:
        group = self.db.get_group(group_id)
        if not group:
            self.db.upsert_group(group_id)
            group = self.db.get_group(group_id)
        boss_line = "无" if not group["boss_active"] else f"{group['boss_name']}（HP {fmt_num(group['boss_hp'])}/{fmt_num(group['boss_max_hp'])}）"
        event=self._active_world_event(group_id)
        event_line = "无" if not event else f"{event['key']}（剩余约 {max(1,(event['expires_at']-int(datetime.now(timezone.utc).timestamp()))//60)} 分钟）"
        npc=self.db.raw.get_current_npc(group_id) if hasattr(self.db,"raw") else self.db.get_current_npc(group_id)
        npc_line = "无" if not npc else f"{npc['name']} · {npc['role']}"
        return (
            "🌎 【群聊世界】\n"
            f"天气：{group['world_weather']}\n"
            f"地点：{group['world_location']}\n"
            f"当前事件：{event_line}\n"
            f"随机 NPC：{npc_line}\n"
            f"世界 Boss：{boss_line}\n"
            "\n"
            "🎮 推荐：/签到 /探索 /游戏 /宠物 /商店 /排行榜\n"
            "📖 完整帮助：/帮助"
        )

    def transfer(self, group_id: str, sender_id: str, target_id: str, amount: int, sender_name: str) -> str:
        if sender_id == target_id:
            return "❌ 不能给自己转账。"
        amount = max(1, min(amount, int(self.cfg("transfer_max_coins", group_id, 1_000_000))))
        sender, _ = self.ensure_player(group_id, sender_id, sender_name)
        target = self.db.get_player(group_id, target_id)
        if not target:
            return "❌ 对方还没有注册群聊世界。"
        if sender["coins"] < amount:
            return "💰 余额不足。"
        self.db.wallet_change(group_id, sender_id, coins_delta=-amount, kind="transfer_out", note=f"转给{target_id}")
        self.db.wallet_change(group_id, target_id, coins_delta=amount, kind="transfer_in", note=f"来自{sender_id}")
        return f"💸 转账成功！\n发送：{sender['name']} → {target['name']}\n金额：{fmt_num(amount)} 金币"


    def use_item(self, group_id: str, user_id: str, name: str, item_id: str, qty: int = 1) -> Result:
        self.ensure_player(group_id, user_id, name)
        qty = max(1, min(int(qty), 20))
        item = self.db.fetchone("SELECT * FROM inventory WHERE group_id=? AND user_id=? AND item_id=?", ("__GLOBAL_USER__", user_id, item_id))
        if not item or int(item["qty"]) < qty:
            return Result("🎒 你没有足够的这个物品。")
        if item_id == "potion":
            healed = 25 * qty
            self.db.add_item(group_id, user_id, "potion", "体力药水", -qty)
            self.db.change_stamina(group_id, user_id, healed); self.db.change_hp(group_id,user_id,healed)
            return Result(f"🧪 使用体力药水 ×{qty}，恢复 {healed} 体力。")
        if item_id == "super_potion":
            healed = 100 * qty
            self.db.add_item(group_id, user_id, "super_potion", "超级体力药水", -qty)
            self.db.change_stamina(group_id, user_id, healed); self.db.change_hp(group_id,user_id,healed)
            return Result(f"🧪 使用超级体力药水 ×{qty}，恢复 {healed} 体力。")
        if item_id == "energy_drink":
            healed = 50 * qty
            self.db.add_item(group_id, user_id, "energy_drink", "能量饮料", -qty)
            self.db.change_stamina(group_id, user_id, healed); self.db.change_hp(group_id,user_id,healed)
            self.db.change_exp(group_id, user_id, 100 * qty)
            return Result(f"🥤 使用能量饮料 ×{qty}，恢复 {healed} 体力并获得 {100*qty} EXP。")
        if item_id == "food":
            self.db.add_item(group_id, user_id, "food", "冒险便当", -qty)
            self.db.change_stamina(group_id, user_id, 10 * qty); self.db.change_hp(group_id,user_id,10*qty)
            self.db.change_exp(group_id, user_id, 40 * qty)
            return Result(f"🍱 吃下冒险便当 ×{qty}，恢复 {10*qty} 体力并获得 {40*qty} EXP。")
        return Result("❌ 这个物品目前不能直接使用。")

    def upgrade_equipment(self, group_id: str, user_id: str, equipment_id: int, name: str) -> Result:
        if not bool(self.cfg("equipment_enabled", group_id, True)):
            return Result("⚔️ 管理员已关闭装备系统。")
        self.ensure_player(group_id, user_id, name)
        eq = self.db.fetchone("SELECT * FROM equipment WHERE group_id=? AND user_id=? AND id=?", (group_id, user_id, equipment_id))
        if not eq:
            return Result("❌ 找不到这件装备。")
        level = int(eq["level"])
        if level >= int(self.cfg("equipment_max_level", group_id, 15)):
            return Result("⚔️ 这件装备已经达到强化上限。")
        ore_cost = max(1, int(self.cfg("equipment_ore_cost", group_id, 2)) + level // 3)
        ore = self.db.fetchone("SELECT qty FROM inventory WHERE group_id=? AND user_id=? AND item_id='ore'", (group_id, user_id))
        if not ore or int(ore["qty"]) < ore_cost:
            return Result(f"⛏️ 强化需要强化矿石 ×{ore_cost}。")
        self.db.add_item(group_id, user_id, "ore", "强化矿石", -ore_cost)
        success_rate = max(0.2, min(0.95, float(self.cfg("equipment_upgrade_base_rate", group_id, 0.78)) - level * 0.025))
        if random.random() > success_rate:
            return Result(f"💥 强化失败。\n装备：{eq['name']} Lv.{level}\n成功率：{int(success_rate*100)}%\n强化矿石已消耗：{ore_cost}")
        new_level = level + 1
        atk_gain = max(1, int(eq["attack"] * 0.12)) if int(eq["attack"]) else 0
        def_gain = max(1, int(eq["defense"] * 0.12)) if int(eq["defense"]) else 0
        self.db.execute("UPDATE equipment SET level=?,attack=attack+?,defense=defense+? WHERE id=? AND group_id=? AND user_id=?", (new_level, atk_gain, def_gain, equipment_id, group_id, user_id))
        return Result(f"✨ 强化成功！\n{eq['name']} Lv.{level} → Lv.{new_level}\n⚔️ +{atk_gain}｜🛡️ +{def_gain}")

    def fishing(self, group_id: str, user_id: str, name: str) -> Result:
        if not bool(self.cfg("fishing_enabled", group_id, True)):
            return Result("🎣 管理员已关闭钓鱼。")
        player, _ = self.ensure_player(group_id, user_id, name)
        cost = int(self.cfg("fishing_stamina_cost", group_id, 8))
        if player["stamina"] < cost:
            return Result(f"🎣 体力不足，需要 {cost} 点。")
        self.db.change_stamina(group_id, user_id, -cost)
        catches = [("小鱼", 80, 1), ("鲤鱼", 160, 2), ("金鳞鱼", 420, 4), ("彩虹鱼", 1200, 10)]
        pick = random.choices(catches, weights=[55,30,13,2])[0]
        self.db.wallet_change(group_id, user_id, coins_delta=pick[1], kind="fishing", note=pick[0])
        self.db.change_exp(group_id, user_id, 60 + pick[2]*20)
        self.db.log_action(group_id, user_id, "fishing", pick[0])
        ach = "" if self.db.unlock_achievement(group_id, user_id, "first_fishing") is False else "\n📜 解锁成就【钓鱼佬】"
        return Result(f"🎣 钓鱼成功！\n🐟 {pick[0]}\n💰 +{pick[1]} 金币\n⭐ +{60+pick[2]*20} EXP{ach}")

    def mining(self, group_id: str, user_id: str, name: str) -> Result:
        if not bool(self.cfg("mining_enabled", group_id, True)):
            return Result("⛏️ 管理员已关闭挖矿。")
        player, _ = self.ensure_player(group_id, user_id, name)
        cost = int(self.cfg("mining_stamina_cost", group_id, 12))
        if player["stamina"] < cost:
            return Result(f"⛏️ 体力不足，需要 {cost} 点。")
        self.db.change_stamina(group_id, user_id, -cost)
        ore_qty = random.randint(1, 4) + (1 if player["profession"] == "收藏家" else 0)
        crystal_chance = 0.08 + player["luck"] / 1000
        self.db.add_item(group_id, user_id, "ore", "强化矿石", ore_qty)
        extra = ""
        if random.random() < crystal_chance:
            self.db.add_item(group_id, user_id, "crystal", "强化水晶", 1)
            extra = "\n💎 还发现了强化水晶 ×1！"
        self.db.change_exp(group_id, user_id, 90)
        self.db.log_action(group_id, user_id, "mining", f"ore={ore_qty}")
        ach = "" if self.db.unlock_achievement(group_id, user_id, "first_mining") is False else "\n📜 解锁成就【矿工学徒】"
        return Result(f"⛏️ 挖矿完成！\n强化矿石 ×{ore_qty}{extra}\n⭐ +90 EXP{ach}")

    def working(self, group_id: str, user_id: str, name: str, job: str = "普通打工") -> Result:
        if not bool(self.cfg("work_enabled", group_id, True)):
            return Result("🧰 管理员已关闭打工。")
        player, _ = self.ensure_player(group_id, user_id, name)
        cd = self.db.cooldown_remaining(group_id, user_id, "working")
        if cd:
            return Result(f"🧰 你刚打完工，还要等 {cd//60} 分钟。")
        jobs = {"普通打工": (220, 420), "搬砖": (300, 650), "夜班": (500, 1100)}
        low, high = jobs.get(job, jobs["普通打工"])
        if player["profession"] == "农夫":
            low, high = int(low*1.1), int(high*1.1)
        coins = random.randint(low, high)
        self.db.wallet_change(group_id, user_id, coins_delta=coins, kind="work", note=job)
        self.db.change_exp(group_id, user_id, 80)
        self.db.set_cooldown(group_id, user_id, "working", int(self.cfg("work_cooldown_minutes", group_id, 30))*60)
        self.db.update_player_metrics(group_id, user_id, total_work=1)
        self.db.log_action(group_id, user_id, "work", job)
        count_row = self.db.get_player(group_id, user_id)
        achievement = ""
        if count_row and int(count_row["total_work"]) >= 10 and self.db.unlock_achievement(group_id, user_id, "worker"):
            achievement = "\n📜 解锁成就【打工皇帝】"
        return Result(f"🧰 打工完成！\n岗位：{job}\n💰 +{coins} 金币\n⭐ +80 EXP\n⏳ 冷却：{self.cfg('work_cooldown_minutes', group_id, 30)} 分钟{achievement}")

    def game_start(self, group_id: str, user_id: str, game_type: str) -> str:
        if not bool(self.cfg("game_enabled", group_id, True)):
            return "🎮 管理员已关闭小游戏。"
        now = utc_ts()
        current = self.db.get_game(group_id)
        if current:
            return "🎮 本群已经有正在进行的小游戏。"
        if game_type == "guess":
            data = {"answer": random.randint(1, 100), "tries": 0, "max_tries": 7}
            self.db.save_game(group_id, game_type, data, now + 180, user_id)
            return "🎯 猜数字开始！\n我已经想好了 1~100 的数字。\n你有 7 次机会，发送 `/猜 50`。"
        if game_type == "bomb":
            player_rows = self.db.get_top_players(group_id, "level", 10)
            eligible = [row["user_id"] for row in player_rows]
            data = {"bomb": random.choice(eligible) if eligible else user_id, "joined": eligible[:10]}
            self.db.save_game(group_id, game_type, data, now + 120, user_id)
            return "💣 炸弹游戏开始！\n系统已在当前活跃玩家中随机选择炸弹持有者。\n输入 `/抽炸弹` 查看你的结果。"
        if game_type == "rps":
            self.db.save_game(group_id, game_type, {}, now + 60, user_id)
            return "✊✌️✋ 猜拳开始！\n发送 `/猜拳 石头`、`/猜拳 剪刀` 或 `/猜拳 布`。"
        if game_type == "dice":
            value = random.randint(1, 6)
            self.db.save_game(group_id, game_type, {"value": value}, now + 30, user_id)
            return f"🎲 你掷出了：{value}\n（本轮结束）"
        return "❌ 未知游戏。"

    def game_guess(self, group_id: str, user_id: str, guess: int, name: str) -> str:
        row = self.db.get_game(group_id)
        if not row or row["game_type"] != "guess":
            return "🎯 当前没有猜数字游戏，请先 `/猜数字`。"
        data = json.loads(row["data_json"])
        data["tries"] += 1
        if guess == data["answer"]:
            self.db.clear_game(group_id)
            reward = max(150, int(self.cfg("game_guess_reward", group_id, 900)) - (data["tries"] - 1) * 100)
            self.db.wallet_change(group_id, user_id, coins_delta=reward, kind="game_guess", note=f"第{data['tries']}次猜中")
            self.db.change_exp(group_id, user_id, reward // 5)
            day = self.db.get_today_key()
            self.db.ensure_daily_tasks(group_id, user_id, day)
            self.db.progress_task(group_id, user_id, day, "game", 1)
            self.db.update_player_metrics(group_id, user_id, total_games=1)
            self.db.log_action(group_id, user_id, "game", "guess")
            return f"🎉 {name} 猜中了！答案就是 {guess}。\n💰 +{reward}\n⭐ +{reward // 5} EXP"
        if data["tries"] >= data["max_tries"]:
            answer = data["answer"]
            self.db.clear_game(group_id)
            return f"💥 游戏结束！7 次机会用完。\n正确答案：{answer}"
        self.db.save_game(group_id, "guess", data, row["expires_at"], row["created_by"])
        hint = "大一点" if guess < data["answer"] else "小一点"
        return f"❌ 不对！答案应该{hint}。\n剩余机会：{data['max_tries'] - data['tries']}"

    def game_rps(self, group_id: str, user_id: str, choice: str, name: str) -> str:
        row = self.db.get_game(group_id)
        if not row or row["game_type"] != "rps":
            return "✊ 当前没有猜拳游戏，请先 `/猜拳`。"
        choice = choice.strip()
        valid = {"石头", "剪刀", "布"}
        if choice not in valid:
            return "请输入：石头 / 剪刀 / 布"
        bot = random.choice(list(valid))
        if bot == choice:
            result = "平局"
            coins = int(self.cfg("game_rps_draw_reward", group_id, 100))
        elif (choice, bot) in {("石头", "剪刀"), ("剪刀", "布"), ("布", "石头")}:
            result = "你赢了"
            coins = int(self.cfg("game_rps_win_reward", group_id, 260))
        else:
            result = "你输了"
            coins = 0
        self.db.clear_game(group_id)
        self.db.update_player_metrics(group_id, user_id, total_games=1)
        self.db.log_action(group_id, user_id, "game", "rps")
        if coins:
            self.db.wallet_change(group_id, user_id, coins_delta=coins, kind="game_rps", note=result)
        return f"✊ 你：{choice}\n🤖 机器人：{bot}\n结果：{result}" + (f"\n💰 +{coins}" if coins else "")

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Small SQLite data layer.

    All writes are guarded by a re-entrant lock and short transactions.  The
    plugin intentionally keeps the schema local so it can be copied/backed up
    without another database service.
    """

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS groups (
            group_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            session_origin TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_message_at TEXT,
            last_event_at TEXT,
            last_tip_at TEXT,
            last_boss_at TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            today_key TEXT NOT NULL DEFAULT '',
            today_messages INTEGER NOT NULL DEFAULT 0,
            today_active_users INTEGER NOT NULL DEFAULT 0,
            world_weather TEXT NOT NULL DEFAULT '晴天',
            world_location TEXT NOT NULL DEFAULT '新手村',
            boss_active INTEGER NOT NULL DEFAULT 0,
            boss_name TEXT,
            boss_hp INTEGER NOT NULL DEFAULT 0,
            boss_max_hp INTEGER NOT NULL DEFAULT 0,
            boss_started_at TEXT,
            boss_ends_at TEXT,
            current_event_key TEXT,
            current_event_expires_at INTEGER NOT NULL DEFAULT 0,
            current_event_effects_json TEXT NOT NULL DEFAULT '{}',
            world_event_enabled INTEGER NOT NULL DEFAULT 1,
            explore_enabled INTEGER NOT NULL DEFAULT 1,
            monster_enabled INTEGER NOT NULL DEFAULT 1,
            monster_chance_percent INTEGER NOT NULL DEFAULT 16,
            monster_max_count INTEGER NOT NULL DEFAULT 3,
            monster_multi_chance_percent INTEGER NOT NULL DEFAULT 28,
            npc_enabled INTEGER NOT NULL DEFAULT 1,
            npc_chance_percent INTEGER NOT NULL DEFAULT 10,
            npc_interval_minutes INTEGER NOT NULL DEFAULT 120,
            ai_enabled INTEGER NOT NULL DEFAULT 0,
            last_npc_at TEXT,
            current_npc_id TEXT,
            current_npc_name TEXT,
            current_npc_role TEXT,
            current_npc_description TEXT,
            current_npc_actions_json TEXT NOT NULL DEFAULT '[]',
            current_npc_expires_at INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS players (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '冒险者',
            level INTEGER NOT NULL DEFAULT 1,
            exp INTEGER NOT NULL DEFAULT 0,
            coins INTEGER NOT NULL DEFAULT 0,
            gems INTEGER NOT NULL DEFAULT 0,
            stamina INTEGER NOT NULL DEFAULT 100,
            max_stamina INTEGER NOT NULL DEFAULT 100,
            luck INTEGER NOT NULL DEFAULT 5,
            renown INTEGER NOT NULL DEFAULT 0,
            profession TEXT NOT NULL DEFAULT '无职业',
            title TEXT NOT NULL DEFAULT '初出茅庐',
            streak INTEGER NOT NULL DEFAULT 0,
            total_checkin INTEGER NOT NULL DEFAULT 0,
            last_checkin TEXT,
            last_stamina_at TEXT NOT NULL DEFAULT '',
            banned INTEGER NOT NULL DEFAULT 0,
            protected_until TEXT,
            explore_count INTEGER NOT NULL DEFAULT 0,
            explore_day TEXT NOT NULL DEFAULT '',
            active_pet_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_seen_at TEXT,
            tutorial_status TEXT NOT NULL DEFAULT 'pending',
            tutorial_step INTEGER NOT NULL DEFAULT 0,
            total_explores INTEGER NOT NULL DEFAULT 0,
            total_games INTEGER NOT NULL DEFAULT 0,
            total_work INTEGER NOT NULL DEFAULT 0,
            total_boss_damage INTEGER NOT NULL DEFAULT 0,
            total_earned_coins INTEGER NOT NULL DEFAULT 0,
            total_spent_coins INTEGER NOT NULL DEFAULT 0,
            last_action_at TEXT,
            player_uid TEXT NOT NULL DEFAULT '',
            battle_attack INTEGER NOT NULL DEFAULT 50,
            battle_defense INTEGER NOT NULL DEFAULT 5,
            battle_crit_rate REAL NOT NULL DEFAULT 8.0,
            battle_dodge_rate REAL NOT NULL DEFAULT 3.0,
            battle_speed INTEGER NOT NULL DEFAULT 100,
            battle_wins INTEGER NOT NULL DEFAULT 0,
            battle_losses INTEGER NOT NULL DEFAULT 0,
            battle_draws INTEGER NOT NULL DEFAULT 0,
            battle_kills INTEGER NOT NULL DEFAULT 0,
            battle_deaths INTEGER NOT NULL DEFAULT 0,
            pvp_rating INTEGER NOT NULL DEFAULT 1000,
            pvp_streak INTEGER NOT NULL DEFAULT 0,
            death_state INTEGER NOT NULL DEFAULT 0,
            respawn_at INTEGER NOT NULL DEFAULT 0,
            revive_count INTEGER NOT NULL DEFAULT 0,
            last_combat_group_id TEXT,
            last_combat_at TEXT,
            PRIMARY KEY (group_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS inventory (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_name TEXT NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id, item_id)
        );

        CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            species TEXT NOT NULL,
            rarity TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            exp INTEGER NOT NULL DEFAULT 0,
            attack INTEGER NOT NULL DEFAULT 1,
            defense INTEGER NOT NULL DEFAULT 1,
            luck INTEGER NOT NULL DEFAULT 1,
            personality TEXT NOT NULL DEFAULT '忠诚',
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            slot TEXT NOT NULL,
            rarity TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 1,
            attack INTEGER NOT NULL DEFAULT 0,
            defense INTEGER NOT NULL DEFAULT 0,
            explore_bonus INTEGER NOT NULL DEFAULT 0,
            equipped INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            coins_delta INTEGER NOT NULL DEFAULT 0,
            gems_delta INTEGER NOT NULL DEFAULT 0,
            coins_balance INTEGER NOT NULL,
            gems_balance INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS daily_tasks (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            date_key TEXT NOT NULL,
            task_id TEXT NOT NULL,
            progress INTEGER NOT NULL DEFAULT 0,
            target INTEGER NOT NULL,
            reward_coins INTEGER NOT NULL DEFAULT 0,
            reward_exp INTEGER NOT NULL DEFAULT 0,
            completed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id, date_key, task_id)
        );

        CREATE TABLE IF NOT EXISTS achievements (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            unlocked_at TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id, key)
        );

        CREATE TABLE IF NOT EXISTS cooldowns (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            PRIMARY KEY (group_id, user_id, key)
        );

        CREATE TABLE IF NOT EXISTS game_sessions (
            group_id TEXT PRIMARY KEY,
            game_type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            expires_at INTEGER NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS boss_damage (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            damage INTEGER NOT NULL DEFAULT 0,
            attacks INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            admin_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_user_id TEXT,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS message_stats (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            date_key TEXT NOT NULL,
            messages INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, user_id, date_key)
        );

        CREATE TABLE IF NOT EXISTS group_members (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '冒险者',
            joined_at TEXT NOT NULL,
            last_seen_at TEXT,
            PRIMARY KEY (group_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS invite_codes (
            code TEXT PRIMARY KEY,
            inviter_user_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            use_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS invite_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inviter_user_id TEXT NOT NULL,
            invitee_user_id TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL,
            coins_inviter INTEGER NOT NULL DEFAULT 0,
            gems_inviter INTEGER NOT NULL DEFAULT 0,
            coins_invitee INTEGER NOT NULL DEFAULT 0,
            gems_invitee INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS auto_battles (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT 'monster',
            skill_mode TEXT NOT NULL DEFAULT 'auto',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_turn_at INTEGER NOT NULL DEFAULT 0,
            turns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id,user_id)
        );

        CREATE TABLE IF NOT EXISTS duel_queue (
            user_id TEXT PRIMARY KEY,
            group_id TEXT NOT NULL,
            session_origin TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '冒险者',
            rating INTEGER NOT NULL DEFAULT 1000,
            joined_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
        );

        CREATE TABLE IF NOT EXISTS duel_battles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            p1_user_id TEXT NOT NULL, p1_group_id TEXT NOT NULL, p1_origin TEXT NOT NULL, p1_name TEXT NOT NULL,
            p2_user_id TEXT NOT NULL, p2_group_id TEXT NOT NULL, p2_origin TEXT NOT NULL, p2_name TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            turn_user_id TEXT, round_no INTEGER NOT NULL DEFAULT 1,
            p1_hp INTEGER NOT NULL, p1_max_hp INTEGER NOT NULL, p2_hp INTEGER NOT NULL, p2_max_hp INTEGER NOT NULL,
            p1_guard INTEGER NOT NULL DEFAULT 0, p2_guard INTEGER NOT NULL DEFAULT 0,
            p1_stamina INTEGER NOT NULL DEFAULT 100, p2_stamina INTEGER NOT NULL DEFAULT 100,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at INTEGER NOT NULL,
            winner_user_id TEXT, loser_user_id TEXT, result_json TEXT NOT NULL DEFAULT '{}',
            last_action_text TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_duel_active_p1 ON duel_battles(p1_user_id,state);
        CREATE INDEX IF NOT EXISTS idx_duel_active_p2 ON duel_battles(p2_user_id,state);
        CREATE INDEX IF NOT EXISTS idx_duel_queue_rating ON duel_queue(status,rating,joined_at);

        CREATE TABLE IF NOT EXISTS duel_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            battle_id INTEGER NOT NULL, sender_user_id TEXT NOT NULL, recipient_user_id TEXT NOT NULL,
            message TEXT NOT NULL, created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_duel_messages_recipient ON duel_messages(recipient_user_id,created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_group_members_user ON group_members(user_id);
        CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id);

        CREATE TABLE IF NOT EXISTS world_event_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            triggered_by TEXT,
            weather TEXT,
            location TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS monster_encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
            monster_id TEXT NOT NULL, monster_name TEXT NOT NULL, hp INTEGER NOT NULL, max_hp INTEGER NOT NULL,
            attack INTEGER NOT NULL DEFAULT 1, defense INTEGER NOT NULL DEFAULT 0, reward_coins INTEGER NOT NULL DEFAULT 0,
            reward_exp INTEGER NOT NULL DEFAULT 0, reward_items_json TEXT NOT NULL DEFAULT '{}', skills_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_monster_encounter_user ON monster_encounters(group_id,user_id,status);
        CREATE TABLE IF NOT EXISTS player_skills (
            user_id TEXT NOT NULL, skill_id TEXT NOT NULL, level INTEGER NOT NULL DEFAULT 1,
            equipped INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY(user_id,skill_id)
        );
        CREATE TABLE IF NOT EXISTS skill_cooldowns (
            user_id TEXT NOT NULL, skill_id TEXT NOT NULL, expires_at INTEGER NOT NULL, PRIMARY KEY(user_id,skill_id)
        );

        CREATE TABLE IF NOT EXISTS npc_interactions (
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            npc_id TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(group_id,user_id,npc_id,action)
        );

        CREATE INDEX IF NOT EXISTS idx_npc_interactions_group ON npc_interactions(group_id,created_at DESC);

        CREATE TABLE IF NOT EXISTS action_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_players_coins ON players(group_id, coins DESC);
        CREATE INDEX IF NOT EXISTS idx_players_level ON players(group_id, level DESC, exp DESC);
        CREATE INDEX IF NOT EXISTS idx_transactions_group ON transactions(group_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_admin_logs_group ON admin_logs(group_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_event_logs_group ON world_event_logs(group_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_action_logs_group ON action_logs(group_id, created_at DESC);
        """
        with self.lock:
            self.conn.executescript(schema)
            self.conn.commit()
            # Backward-compatible migrations for databases created by v1.0.
            existing = {r[1] for r in self.conn.execute("PRAGMA table_info(players)").fetchall()}
            migrations = {
                "tutorial_status": "ALTER TABLE players ADD COLUMN tutorial_status TEXT NOT NULL DEFAULT 'pending'",
                "tutorial_step": "ALTER TABLE players ADD COLUMN tutorial_step INTEGER NOT NULL DEFAULT 0",
                "total_explores": "ALTER TABLE players ADD COLUMN total_explores INTEGER NOT NULL DEFAULT 0",
                "total_games": "ALTER TABLE players ADD COLUMN total_games INTEGER NOT NULL DEFAULT 0",
                "total_work": "ALTER TABLE players ADD COLUMN total_work INTEGER NOT NULL DEFAULT 0",
                "total_boss_damage": "ALTER TABLE players ADD COLUMN total_boss_damage INTEGER NOT NULL DEFAULT 0",
                "total_earned_coins": "ALTER TABLE players ADD COLUMN total_earned_coins INTEGER NOT NULL DEFAULT 0",
                "total_spent_coins": "ALTER TABLE players ADD COLUMN total_spent_coins INTEGER NOT NULL DEFAULT 0",
                "last_action_at": "ALTER TABLE players ADD COLUMN last_action_at TEXT",
                "hp": "ALTER TABLE players ADD COLUMN hp INTEGER NOT NULL DEFAULT 100",
                "max_hp": "ALTER TABLE players ADD COLUMN max_hp INTEGER NOT NULL DEFAULT 100",
                "player_uid": "ALTER TABLE players ADD COLUMN player_uid TEXT NOT NULL DEFAULT ''",
                "battle_attack": "ALTER TABLE players ADD COLUMN battle_attack INTEGER NOT NULL DEFAULT 50",
                "battle_defense": "ALTER TABLE players ADD COLUMN battle_defense INTEGER NOT NULL DEFAULT 5",
                "battle_crit_rate": "ALTER TABLE players ADD COLUMN battle_crit_rate REAL NOT NULL DEFAULT 8.0",
                "battle_dodge_rate": "ALTER TABLE players ADD COLUMN battle_dodge_rate REAL NOT NULL DEFAULT 3.0",
                "battle_speed": "ALTER TABLE players ADD COLUMN battle_speed INTEGER NOT NULL DEFAULT 100",
                "battle_wins": "ALTER TABLE players ADD COLUMN battle_wins INTEGER NOT NULL DEFAULT 0",
                "battle_losses": "ALTER TABLE players ADD COLUMN battle_losses INTEGER NOT NULL DEFAULT 0",
                "battle_draws": "ALTER TABLE players ADD COLUMN battle_draws INTEGER NOT NULL DEFAULT 0",
                "battle_kills": "ALTER TABLE players ADD COLUMN battle_kills INTEGER NOT NULL DEFAULT 0",
                "battle_deaths": "ALTER TABLE players ADD COLUMN battle_deaths INTEGER NOT NULL DEFAULT 0",
                "pvp_rating": "ALTER TABLE players ADD COLUMN pvp_rating INTEGER NOT NULL DEFAULT 1000",
                "pvp_streak": "ALTER TABLE players ADD COLUMN pvp_streak INTEGER NOT NULL DEFAULT 0",
                "death_state": "ALTER TABLE players ADD COLUMN death_state INTEGER NOT NULL DEFAULT 0",
                "respawn_at": "ALTER TABLE players ADD COLUMN respawn_at INTEGER NOT NULL DEFAULT 0",
                "revive_count": "ALTER TABLE players ADD COLUMN revive_count INTEGER NOT NULL DEFAULT 0",
                "last_combat_group_id": "ALTER TABLE players ADD COLUMN last_combat_group_id TEXT",
                "last_combat_at": "ALTER TABLE players ADD COLUMN last_combat_at TEXT",
            }
            for field, sql in migrations.items():
                if field not in existing:
                    self.conn.execute(sql)
            monster_existing = {r[1] for r in self.conn.execute("PRAGMA table_info(monster_encounters)").fetchall()}
            if "skills_json" not in monster_existing:
                self.conn.execute("ALTER TABLE monster_encounters ADD COLUMN skills_json TEXT NOT NULL DEFAULT '[]'")
            self.conn.execute("UPDATE players SET max_hp=CASE WHEN max_hp<1 THEN 100 ELSE max_hp END, hp=CASE WHEN hp<0 THEN 0 WHEN hp>max_hp THEN max_hp ELSE hp END")
            group_existing={r[1] for r in self.conn.execute("PRAGMA table_info(groups)").fetchall()}
            group_migrations = {
                "last_tip_at": "ALTER TABLE groups ADD COLUMN last_tip_at TEXT",
                "current_event_key": "ALTER TABLE groups ADD COLUMN current_event_key TEXT",
                "current_event_expires_at": "ALTER TABLE groups ADD COLUMN current_event_expires_at INTEGER NOT NULL DEFAULT 0",
                "current_event_effects_json": "ALTER TABLE groups ADD COLUMN current_event_effects_json TEXT NOT NULL DEFAULT '{}'",
                "world_event_enabled": "ALTER TABLE groups ADD COLUMN world_event_enabled INTEGER NOT NULL DEFAULT 1",
                "explore_enabled": "ALTER TABLE groups ADD COLUMN explore_enabled INTEGER NOT NULL DEFAULT 1",
                "monster_enabled": "ALTER TABLE groups ADD COLUMN monster_enabled INTEGER NOT NULL DEFAULT 1",
                "monster_chance_percent": "ALTER TABLE groups ADD COLUMN monster_chance_percent INTEGER NOT NULL DEFAULT 16",
                "monster_max_count": "ALTER TABLE groups ADD COLUMN monster_max_count INTEGER NOT NULL DEFAULT 3",
                "monster_multi_chance_percent": "ALTER TABLE groups ADD COLUMN monster_multi_chance_percent INTEGER NOT NULL DEFAULT 28",
                "npc_enabled": "ALTER TABLE groups ADD COLUMN npc_enabled INTEGER NOT NULL DEFAULT 1",
                "npc_chance_percent": "ALTER TABLE groups ADD COLUMN npc_chance_percent INTEGER NOT NULL DEFAULT 10",
                "npc_interval_minutes": "ALTER TABLE groups ADD COLUMN npc_interval_minutes INTEGER NOT NULL DEFAULT 120",
                "ai_enabled": "ALTER TABLE groups ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 0",
                "last_npc_at": "ALTER TABLE groups ADD COLUMN last_npc_at TEXT",
                "current_npc_id": "ALTER TABLE groups ADD COLUMN current_npc_id TEXT",
                "current_npc_name": "ALTER TABLE groups ADD COLUMN current_npc_name TEXT",
                "current_npc_role": "ALTER TABLE groups ADD COLUMN current_npc_role TEXT",
                "current_npc_description": "ALTER TABLE groups ADD COLUMN current_npc_description TEXT",
                "current_npc_actions_json": "ALTER TABLE groups ADD COLUMN current_npc_actions_json TEXT NOT NULL DEFAULT '[]'",
                "current_npc_expires_at": "ALTER TABLE groups ADD COLUMN current_npc_expires_at INTEGER NOT NULL DEFAULT 0",
            }
            for field, sql in group_migrations.items():
                if field not in group_existing:
                    self.conn.execute(sql)
            # Cross-group user migration: keep one canonical profile per platform user.
            self.conn.execute("""INSERT OR IGNORE INTO players(group_id,user_id,name,level,exp,coins,gems,stamina,max_stamina,luck,renown,profession,title,streak,total_checkin,last_checkin,last_stamina_at,banned,protected_until,explore_count,explore_day,active_pet_id,created_at,updated_at,last_seen_at,tutorial_status,tutorial_step,total_explores,total_games,total_work,total_boss_damage,total_earned_coins,total_spent_coins,last_action_at) SELECT '__GLOBAL_USER__',p.user_id,p.name,p.level,p.exp,p.coins,p.gems,p.stamina,p.max_stamina,p.luck,p.renown,p.profession,p.title,p.streak,p.total_checkin,p.last_checkin,p.last_stamina_at,p.banned,p.protected_until,p.explore_count,p.explore_day,p.active_pet_id,p.created_at,p.updated_at,p.last_seen_at,p.tutorial_status,p.tutorial_step,p.total_explores,p.total_games,p.total_work,p.total_boss_damage,p.total_earned_coins,p.total_spent_coins,p.last_action_at FROM players p JOIN (SELECT user_id,MAX(level) AS max_level FROM players WHERE group_id!='__GLOBAL_USER__' GROUP BY user_id) x ON x.user_id=p.user_id AND x.max_level=p.level WHERE p.group_id!='__GLOBAL_USER__'""")
            self.conn.execute("INSERT OR IGNORE INTO inventory(group_id,user_id,item_id,item_name,qty) SELECT '__GLOBAL_USER__',user_id,item_id,item_name,qty FROM inventory WHERE group_id!='__GLOBAL_USER__'")
            self.conn.execute("INSERT OR IGNORE INTO pets(group_id,user_id,species,rarity,level,exp,attack,defense,luck,personality,active,created_at) SELECT '__GLOBAL_USER__',user_id,species,rarity,level,exp,attack,defense,luck,personality,active,created_at FROM pets WHERE group_id!='__GLOBAL_USER__'")
            self.conn.execute("INSERT OR IGNORE INTO equipment(group_id,user_id,name,slot,rarity,level,attack,defense,explore_bonus,equipped,created_at) SELECT '__GLOBAL_USER__',user_id,name,slot,rarity,level,attack,defense,explore_bonus,equipped,created_at FROM equipment WHERE group_id!='__GLOBAL_USER__'")
            self.conn.execute("INSERT OR IGNORE INTO transactions(group_id,user_id,kind,coins_delta,gems_delta,coins_balance,gems_balance,note,created_at) SELECT '__GLOBAL_USER__',user_id,kind,coins_delta,gems_delta,coins_balance,gems_balance,note,created_at FROM transactions WHERE group_id!='__GLOBAL_USER__'")
            # Build an explicit per-group membership index. Player-owned data stays global;
            # this table only answers: which groups has this user actually joined the world in?
            self.conn.execute("INSERT OR IGNORE INTO group_members(group_id,user_id,name,joined_at,last_seen_at) "
                              "SELECT p.group_id,p.user_id,p.name,COALESCE(p.created_at,?),p.last_seen_at "
                              "FROM players p WHERE p.group_id!='__GLOBAL_USER__'", (utc_now(),))
            import secrets as _secrets
            blanks=self.conn.execute("SELECT rowid FROM players WHERE group_id='__GLOBAL_USER__' AND (player_uid='' OR player_uid IS NULL)").fetchall()
            used={str(r[0]) for r in self.conn.execute("SELECT player_uid FROM players WHERE player_uid!='' AND player_uid IS NOT NULL").fetchall()}
            for row in blanks:
                candidate=''
                while not candidate or candidate in used:
                    candidate='GW-'+_secrets.token_hex(5).upper()
                used.add(candidate)
                self.conn.execute("UPDATE players SET player_uid=? WHERE rowid=?", (candidate,row[0]))
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_players_player_uid ON players(player_uid) WHERE player_uid!=''")
            self.conn.execute("UPDATE players SET battle_attack=50 WHERE battle_attack IS NULL OR battle_attack<1")
            self.conn.execute("UPDATE players SET battle_defense=5 WHERE battle_defense IS NULL OR battle_defense<0")
            self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self.lock:
            self.conn.executemany(sql, [tuple(r) for r in rows])
            self.conn.commit()

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def transaction(self):
        return _Tx(self)

    def upsert_group(self, group_id: str, origin: str | None = None) -> sqlite3.Row:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO groups(group_id, session_origin, created_at, updated_at, today_key)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    session_origin=COALESCE(excluded.session_origin, groups.session_origin),
                    updated_at=excluded.updated_at
                """,
                (group_id, origin, now, now, now[:10]),
            )
        return self.fetchone("SELECT * FROM groups WHERE group_id=?", (group_id,))

    def get_player(self, group_id: str, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM players WHERE group_id=? AND user_id=?", (group_id, user_id)
        )

    def ensure_player(
        self,
        group_id: str,
        user_id: str,
        name: str,
        coins: int,
        gems: int,
        max_stamina: int,
        protection_hours: int,
    ) -> tuple[sqlite3.Row, bool]:
        existing = self.get_player(group_id, user_id)
        if existing:
            if name and name != existing["name"]:
                self.execute(
                    "UPDATE players SET name=?, updated_at=?, last_seen_at=? WHERE group_id=? AND user_id=?",
                    (name, utc_now(), utc_now(), group_id, user_id),
                )
                existing = self.get_player(group_id, user_id)
            return existing, False

        now = utc_now()
        protected_until = None
        if protection_hours > 0:
            from datetime import timedelta

            protected_until = (
                datetime.now(timezone.utc) + timedelta(hours=protection_hours)
            ).isoformat(timespec="seconds")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO players(
                    group_id,user_id,name,coins,gems,stamina,max_stamina,
                    last_stamina_at,protected_until,created_at,updated_at,last_seen_at,
                    tutorial_status,tutorial_step,last_action_at,player_uid
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    group_id,
                    user_id,
                    name or "冒险者",
                    coins,
                    gems,
                    max_stamina,
                    max_stamina,
                    now,
                    protected_until,
                    now,
                    now,
                    now,
                    "pending",
                    0,
                    now,
                    "",
                ),
            )
            conn.execute(
                "INSERT INTO transactions(group_id,user_id,kind,coins_delta,gems_delta,coins_balance,gems_balance,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    "new_player",
                    coins,
                    gems,
                    coins,
                    gems,
                    "新玩家礼包",
                    now,
                ),
            )
        if group_id == "__GLOBAL_USER__":
            import secrets as _secrets
            candidate=''
            used={str(r[0]) for r in self.fetchall("SELECT player_uid FROM players WHERE player_uid!='' AND player_uid IS NOT NULL") }
            while not candidate or candidate in used:
                candidate='GW-'+_secrets.token_hex(5).upper()
            self.execute("UPDATE players SET player_uid=? WHERE group_id=? AND user_id=?", (candidate,group_id,user_id))
        return self.get_player(group_id, user_id), True

    def refresh_stamina(self, player: sqlite3.Row, regen_minutes: int, regen_amount: int) -> sqlite3.Row:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        try:
            last_ts = int(datetime.fromisoformat(player["last_stamina_at"]).timestamp())
        except Exception:
            last_ts = now_ts
        periods = max(0, (now_ts - last_ts) // max(60, regen_minutes * 60))
        if periods <= 0:
            return player
        new_stamina = min(player["max_stamina"], player["stamina"] + periods * regen_amount)
        hp_gain = periods * max(1, regen_amount * 2)
        current_hp = int(player["hp"]) if "hp" in player.keys() else int(player["max_stamina"])
        max_hp = int(player["max_hp"]) if "max_hp" in player.keys() else int(player["max_stamina"])
        # 死亡玩家的生命只允许通过复活流程恢复，避免自然体力恢复造成“死亡但满血”的状态。
        new_hp = current_hp if int(player["death_state"] or 0) else min(max_hp, current_hp + hp_gain)
        new_last = datetime.fromtimestamp(
            last_ts + periods * regen_minutes * 60, tz=timezone.utc
        ).isoformat(timespec="seconds")
        self.execute(
            "UPDATE players SET stamina=?, hp=?, last_stamina_at=?, updated_at=? WHERE group_id=? AND user_id=?",
            (new_stamina, new_hp, new_last, utc_now(), player["group_id"], player["user_id"]),
        )
        return self.get_player(player["group_id"], player["user_id"])

    def wallet_change(
        self,
        group_id: str,
        user_id: str,
        coins_delta: int = 0,
        gems_delta: int = 0,
        kind: str = "wallet",
        note: str = "",
    ) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT coins,gems FROM players WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            if not row:
                return None
            new_coins = int(row["coins"]) + coins_delta
            new_gems = int(row["gems"]) + gems_delta
            if new_coins < 0 or new_gems < 0:
                return None
            now = utc_now()
            earn = max(0, coins_delta)
            spend = max(0, -coins_delta)
            conn.execute(
                "UPDATE players SET coins=?, gems=?, updated_at=?, total_earned_coins=total_earned_coins+?, total_spent_coins=total_spent_coins+? WHERE group_id=? AND user_id=?",
                (new_coins, new_gems, now, earn, spend, group_id, user_id),
            )
            conn.execute(
                "INSERT INTO transactions(group_id,user_id,kind,coins_delta,gems_delta,coins_balance,gems_balance,note,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    kind,
                    coins_delta,
                    gems_delta,
                    new_coins,
                    new_gems,
                    note,
                    now,
                ),
            )
        return self.get_player(group_id, user_id)

    def change_exp(self, group_id: str, user_id: str, exp_gain: int) -> tuple[sqlite3.Row, int]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM players WHERE group_id=? AND user_id=?", (group_id, user_id)
            ).fetchone()
            if not row:
                raise ValueError("player not found")
            level = int(row["level"])
            exp = int(row["exp"]) + max(0, exp_gain)
            level_ups = 0
            while exp >= self.level_requirement(level) and level < 999:
                exp -= self.level_requirement(level)
                level += 1
                level_ups += 1
            now = utc_now()
            conn.execute(
                "UPDATE players SET level=?,exp=?,updated_at=? WHERE group_id=? AND user_id=?",
                (level, exp, now, group_id, user_id),
            )
        return self.get_player(group_id, user_id), level_ups

    @staticmethod
    def level_requirement(level: int) -> int:
        return 150 + level * level * 45

    def change_hp(self, group_id: str, user_id: str, delta: int) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row=conn.execute("SELECT hp,max_hp FROM players WHERE group_id=? AND user_id=?",(group_id,user_id)).fetchone()
            if not row:return None
            hp=max(0,min(int(row["max_hp"]),int(row["hp"])+int(delta)))
            conn.execute("UPDATE players SET hp=?,updated_at=? WHERE group_id=? AND user_id=?",(hp,utc_now(),group_id,user_id))
        return self.get_player(group_id,user_id)

    def restore_hp_full(self, group_id: str, user_id: str) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row=conn.execute("SELECT max_hp FROM players WHERE group_id=? AND user_id=?",(group_id,user_id)).fetchone()
            if not row:return None
            conn.execute("UPDATE players SET hp=max_hp,updated_at=? WHERE group_id=? AND user_id=?",(utc_now(),group_id,user_id))
        return self.get_player(group_id,user_id)

    def change_stamina(self, group_id: str, user_id: str, delta: int) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT stamina,max_stamina FROM players WHERE group_id=? AND user_id=?",
                (group_id, user_id),
            ).fetchone()
            if not row:
                return None
            new_stamina = max(0, min(row["max_stamina"], row["stamina"] + delta))
            conn.execute(
                "UPDATE players SET stamina=?,updated_at=? WHERE group_id=? AND user_id=?",
                (new_stamina, utc_now(), group_id, user_id),
            )
        return self.get_player(group_id, user_id)

    def set_player_field(self, group_id: str, user_id: str, field: str, value: Any) -> None:
        allowed = {"name", "profession", "title", "banned", "luck", "renown", "stamina", "max_stamina"}
        if field not in allowed:
            raise ValueError("invalid field")
        self.execute(
            f"UPDATE players SET {field}=?,updated_at=? WHERE group_id=? AND user_id=?",
            (value, utc_now(), group_id, user_id),
        )

    def add_item(self, group_id: str, user_id: str, item_id: str, item_name: str, qty: int = 1) -> None:
        if qty == 0:
            return
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT qty FROM inventory WHERE group_id=? AND user_id=? AND item_id=?",
                (group_id, user_id, item_id),
            ).fetchone()
            if row:
                new_qty = row["qty"] + qty
                if new_qty <= 0:
                    conn.execute(
                        "DELETE FROM inventory WHERE group_id=? AND user_id=? AND item_id=?",
                        (group_id, user_id, item_id),
                    )
                else:
                    conn.execute(
                        "UPDATE inventory SET qty=?,item_name=? WHERE group_id=? AND user_id=? AND item_id=?",
                        (new_qty, item_name, group_id, user_id, item_id),
                    )
            elif qty > 0:
                conn.execute(
                    "INSERT INTO inventory(group_id,user_id,item_id,item_name,qty) VALUES(?,?,?,?,?)",
                    (group_id, user_id, item_id, item_name, qty),
                )

    def get_inventory(self, group_id: str, user_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM inventory WHERE group_id=? AND user_id=? AND qty>0 ORDER BY item_name",
            (group_id, user_id),
        )

    def count_item(self, group_id: str, user_id: str, item_id: str) -> int:
        row = self.fetchone(
            "SELECT qty FROM inventory WHERE group_id=? AND user_id=? AND item_id=?",
            (group_id, user_id, item_id),
        )
        return int(row["qty"]) if row else 0

    def create_pet(
        self,
        group_id: str,
        user_id: str,
        species: str,
        rarity: str,
        level: int,
        attack: int,
        defense: int,
        luck: int,
        personality: str,
    ) -> int:
        now = utc_now()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO pets(group_id,user_id,species,rarity,level,attack,defense,luck,personality,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    species,
                    rarity,
                    level,
                    attack,
                    defense,
                    luck,
                    personality,
                    now,
                ),
            )
            pet_id = cur.lastrowid
        return int(pet_id)

    def get_pets(self, group_id: str, user_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM pets WHERE group_id=? AND user_id=? ORDER BY rarity DESC,level DESC,id",
            (group_id, user_id),
        )

    def get_active_pet(self, group_id: str, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone(
            "SELECT * FROM pets WHERE group_id=? AND user_id=? AND active=1 LIMIT 1",
            (group_id, user_id),
        )

    def activate_pet(self, group_id: str, user_id: str, pet_id: int) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM pets WHERE id=? AND group_id=? AND user_id=?", (pet_id, group_id, user_id)
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE pets SET active=0 WHERE group_id=? AND user_id=?", (group_id, user_id)
            )
            conn.execute("UPDATE pets SET active=1 WHERE id=?", (pet_id,))
            conn.execute(
                "UPDATE players SET active_pet_id=?,updated_at=? WHERE group_id=? AND user_id=?",
                (pet_id, utc_now(), group_id, user_id),
            )
        return self.fetchone("SELECT * FROM pets WHERE id=?", (pet_id,))

    def add_pet_exp(self, pet_id: int, exp_gain: int) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM pets WHERE id=?", (pet_id,)).fetchone()
            if not row:
                return None
            level = int(row["level"])
            exp = int(row["exp"]) + max(0, exp_gain)
            while exp >= 100 + level * 60 and level < 100:
                exp -= 100 + level * 60
                level += 1
            conn.execute("UPDATE pets SET level=?,exp=? WHERE id=?", (level, exp, pet_id))
        return self.fetchone("SELECT * FROM pets WHERE id=?", (pet_id,))

    def add_equipment(
        self,
        group_id: str,
        user_id: str,
        name: str,
        slot: str,
        rarity: str,
        level: int,
        attack: int,
        defense: int,
        explore_bonus: int,
        equipped: bool = False,
    ) -> int:
        with self.transaction() as conn:
            if equipped:
                conn.execute(
                    "UPDATE equipment SET equipped=0 WHERE group_id=? AND user_id=? AND slot=?",
                    (group_id, user_id, slot),
                )
            cur = conn.execute(
                "INSERT INTO equipment(group_id,user_id,name,slot,rarity,level,attack,defense,explore_bonus,equipped,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    group_id,
                    user_id,
                    name,
                    slot,
                    rarity,
                    level,
                    attack,
                    defense,
                    explore_bonus,
                    1 if equipped else 0,
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def get_equipment(self, group_id: str, user_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM equipment WHERE group_id=? AND user_id=? ORDER BY equipped DESC, rarity DESC, id",
            (group_id, user_id),
        )

    def get_equipped_stats(self, group_id: str, user_id: str) -> dict[str, int]:
        row = self.fetchone(
            "SELECT COALESCE(SUM(attack),0) attack, COALESCE(SUM(defense),0) defense, COALESCE(SUM(explore_bonus),0) explore_bonus FROM equipment WHERE group_id=? AND user_id=? AND equipped=1",
            (group_id, user_id),
        )
        return {"attack": int(row["attack"]), "defense": int(row["defense"]), "explore_bonus": int(row["explore_bonus"])}

    def get_today_key(self) -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")

    def touch_message(self, group_id: str, user_id: str, origin: str) -> None:
        now = utc_now()
        day = self.get_today_key()
        with self.transaction() as conn:
            row = conn.execute("SELECT today_key FROM groups WHERE group_id=?", (group_id,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO groups(group_id,session_origin,created_at,updated_at,today_key) VALUES(?,?,?,?,?)",
                    (group_id, origin, now, now, day),
                )
                row = {"today_key": day}
            if row["today_key"] != day:
                conn.execute(
                    "UPDATE groups SET today_key=?,today_messages=0,today_active_users=0 WHERE group_id=?",
                    (day, group_id),
                )
            conn.execute(
                "UPDATE groups SET session_origin=?,last_message_at=?,updated_at=?,message_count=message_count+1,today_messages=today_messages+1 WHERE group_id=?",
                (origin, now, now, group_id),
            )
            conn.execute(
                "INSERT INTO message_stats(group_id,user_id,date_key,messages) VALUES(?,?,?,1) ON CONFLICT(group_id,user_id,date_key) DO UPDATE SET messages=messages+1",
                (group_id, user_id, day),
            )
            conn.execute(
                "UPDATE group_members SET last_seen_at=? WHERE group_id=? AND user_id=?",
                (now, group_id, user_id),
            )
            conn.execute(
                "UPDATE players SET last_seen_at=?,updated_at=? WHERE group_id=? AND user_id=?",
                (now, now, "__GLOBAL_USER__", user_id),
            )
            # Count unique active users from today's stats to keep it cheap.
            active = conn.execute(
                "SELECT COUNT(*) FROM message_stats WHERE group_id=? AND date_key=?",
                (group_id, day),
            ).fetchone()[0]
            conn.execute(
                "UPDATE groups SET today_active_users=? WHERE group_id=?",
                (active, group_id),
            )

    def get_group(self, group_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM groups WHERE group_id=?", (group_id,))

    def update_group(self, group_id: str, **fields: Any) -> None:
        allowed = {
            "enabled", "session_origin", "last_event_at", "last_boss_at", "world_weather", "world_location",
            "boss_active", "boss_name", "boss_hp", "boss_max_hp", "boss_started_at", "boss_ends_at",
            "current_event_key", "current_event_expires_at", "current_event_effects_json", "world_event_enabled",
            "explore_enabled", "monster_enabled", "monster_chance_percent", "monster_max_count",
            "monster_multi_chance_percent", "npc_enabled", "npc_chance_percent", "npc_interval_minutes",
            "ai_enabled", "last_npc_at", "current_npc_id", "current_npc_name", "current_npc_role",
            "current_npc_description", "current_npc_actions_json", "current_npc_expires_at",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return
        clean["updated_at"] = utc_now()
        sets = ",".join(f"{key}=?" for key in clean)
        self.execute(
            f"UPDATE groups SET {sets} WHERE group_id=?",
            (*clean.values(), group_id),
        )

    def set_cooldown(self, group_id: str, user_id: str, key: str, seconds: int) -> None:
        expires = int(datetime.now(timezone.utc).timestamp()) + max(0, seconds)
        self.execute(
            "INSERT INTO cooldowns(group_id,user_id,key,expires_at) VALUES(?,?,?,?) ON CONFLICT(group_id,user_id,key) DO UPDATE SET expires_at=excluded.expires_at",
            (group_id, user_id, key, expires),
        )

    def cooldown_remaining(self, group_id: str, user_id: str, key: str) -> int:
        row = self.fetchone(
            "SELECT expires_at FROM cooldowns WHERE group_id=? AND user_id=? AND key=?",
            (group_id, user_id, key),
        )
        if not row:
            return 0
        remaining = int(row["expires_at"]) - int(datetime.now(timezone.utc).timestamp())
        if remaining <= 0:
            self.execute(
                "DELETE FROM cooldowns WHERE group_id=? AND user_id=? AND key=?",
                (group_id, user_id, key),
            )
            return 0
        return remaining

    def save_game(self, group_id: str, game_type: str, data: dict[str, Any], expires_at: int, created_by: str) -> None:
        self.execute(
            "INSERT INTO game_sessions(group_id,game_type,data_json,expires_at,created_by,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET game_type=excluded.game_type,data_json=excluded.data_json,expires_at=excluded.expires_at,created_by=excluded.created_by,created_at=excluded.created_at",
            (group_id, game_type, json.dumps(data, ensure_ascii=False), expires_at, created_by, utc_now()),
        )

    def get_game(self, group_id: str) -> Optional[sqlite3.Row]:
        row = self.fetchone("SELECT * FROM game_sessions WHERE group_id=?", (group_id,))
        if not row:
            return None
        if row["expires_at"] < int(datetime.now(timezone.utc).timestamp()):
            self.execute("DELETE FROM game_sessions WHERE group_id=?", (group_id,))
            return None
        return row

    def clear_game(self, group_id: str) -> None:
        self.execute("DELETE FROM game_sessions WHERE group_id=?", (group_id,))

    def add_boss_damage(self, group_id: str, user_id: str, damage: int) -> None:
        self.execute(
            "INSERT INTO boss_damage(group_id,user_id,damage,attacks) VALUES(?,?,?,1) ON CONFLICT(group_id,user_id) DO UPDATE SET damage=damage+excluded.damage,attacks=attacks+1",
            (group_id, user_id, damage),
        )

    def reset_boss_damage(self, group_id: str) -> None:
        self.execute("DELETE FROM boss_damage WHERE group_id=?", (group_id,))

    def get_boss_ranking(self, group_id: str, limit: int = 10) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT b.*,COALESCE(p.name,b.user_id) name FROM boss_damage b LEFT JOIN players p ON p.group_id=b.group_id AND p.user_id=b.user_id WHERE b.group_id=? ORDER BY b.damage DESC LIMIT ?",
            (group_id, limit),
        )

    def add_admin_log(self, group_id: str, admin_id: str, action: str, target_user_id: str | None, detail: str) -> None:
        self.execute(
            "INSERT INTO admin_logs(group_id,admin_id,action,target_user_id,detail,created_at) VALUES(?,?,?,?,?,?)",
            (group_id, admin_id, action, target_user_id, detail, utc_now()),
        )

    def get_top_players(self, group_id: str, order: str, limit: int = 10) -> list[sqlite3.Row]:
        allowed = {"coins", "level", "exp", "luck", "renown"}
        if order not in allowed:
            order = "level"
        return self.fetchall(
            f"SELECT * FROM players WHERE group_id='__GLOBAL_USER__' AND banned=0 ORDER BY {order} DESC, level DESC, exp DESC LIMIT ?",
            (limit,),
        )

    def get_recent_logs(self, group_id: str, limit: int = 30) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM admin_logs WHERE group_id=? ORDER BY created_at DESC LIMIT ?",
            (group_id, limit),
        )

    def get_group_stats(self, group_id: str) -> dict[str, int]:
        row = self.get_group(group_id)
        players = self.fetchone("SELECT COUNT(*) c FROM group_members WHERE group_id=?", (group_id,))["c"]
        return {
            "players": int(players),
            "messages": int(row["message_count"] if row else 0),
            "today_messages": int(row["today_messages"] if row else 0),
            "today_active_users": int(row["today_active_users"] if row else 0),
        }

    def get_player_stats(self, group_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT user_id,name,level,exp,coins,gems,stamina,profession,renown,last_seen_at FROM players WHERE group_id=? ORDER BY level DESC,exp DESC LIMIT 100",
            (group_id,),
        )

    def get_task_rows(self, group_id: str, user_id: str, date_key: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM daily_tasks WHERE group_id=? AND user_id=? AND date_key=? ORDER BY task_id",
            (group_id, user_id, date_key),
        )

    def ensure_daily_tasks(self, group_id: str, user_id: str, date_key: str) -> list[sqlite3.Row]:
        existing = self.get_task_rows(group_id, user_id, date_key)
        if existing:
            return existing
        tasks = [
            ("checkin", 1, 250, 120),
            ("explore", 3, 500, 300),
            ("game", 2, 400, 240),
        ]
        self.executemany(
            "INSERT INTO daily_tasks(group_id,user_id,date_key,task_id,target,reward_coins,reward_exp) VALUES(?,?,?,?,?,?,?)",
            [(group_id, user_id, date_key, t, target, coins, exp) for t, target, coins, exp in tasks],
        )
        return self.get_task_rows(group_id, user_id, date_key)

    def progress_task(self, group_id: str, user_id: str, date_key: str, task_id: str, amount: int) -> Optional[sqlite3.Row]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM daily_tasks WHERE group_id=? AND user_id=? AND date_key=? AND task_id=?",
                (group_id, user_id, date_key, task_id),
            ).fetchone()
            if not row or row["completed"]:
                return row
            progress = min(row["target"], row["progress"] + max(0, amount))
            completed = 1 if progress >= row["target"] else 0
            conn.execute(
                "UPDATE daily_tasks SET progress=?,completed=? WHERE group_id=? AND user_id=? AND date_key=? AND task_id=?",
                (progress, completed, group_id, user_id, date_key, task_id),
            )
        return self.fetchone(
            "SELECT * FROM daily_tasks WHERE group_id=? AND user_id=? AND date_key=? AND task_id=?",
            (group_id, user_id, date_key, task_id),
        )

    def unlock_achievement(self, group_id: str, user_id: str, key: str) -> bool:
        try:
            self.execute(
                "INSERT INTO achievements(group_id,user_id,key,unlocked_at) VALUES(?,?,?,?)",
                (group_id, user_id, key, utc_now()),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_achievements(self, group_id: str, user_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM achievements WHERE group_id=? AND user_id=? ORDER BY unlocked_at DESC",
            (group_id, user_id),
        )

    def leaderboard_activity(self, group_id: str, date_key: str, limit: int = 10) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT m.user_id,m.messages,COALESCE(p.name,m.user_id) name FROM message_stats m LEFT JOIN players p ON p.group_id=m.group_id AND p.user_id=m.user_id WHERE m.group_id=? AND m.date_key=? ORDER BY m.messages DESC LIMIT ?",
            (group_id, date_key, limit),
        )


    def set_tutorial(self, group_id: str, user_id: str, status: str, step: int = 0) -> None:
        allowed = {"pending", "completed", "skipped"}
        status = status if status in allowed else "pending"
        self.execute(
            "UPDATE players SET tutorial_status=?, tutorial_step=?, updated_at=? WHERE group_id=? AND user_id=?",
            (status, max(0, int(step)), utc_now(), group_id, user_id),
        )

    def update_player_metrics(self, group_id: str, user_id: str, **fields: int) -> None:
        allowed = {"total_explores", "total_games", "total_work", "total_boss_damage"}
        updates = {k: int(v) for k, v in fields.items() if k in allowed}
        if not updates:
            return
        set_sql = ", ".join(f"{key}={key}+?" for key in updates)
        self.execute(
            f"UPDATE players SET {set_sql}, last_action_at=?, updated_at=? WHERE group_id=? AND user_id=?",
            (*updates.values(), utc_now(), utc_now(), group_id, user_id),
        )

    def log_action(self, group_id: str, user_id: str, action: str, detail: str = "") -> None:
        self.execute(
            "INSERT INTO action_logs(group_id,user_id,action,detail,created_at) VALUES(?,?,?,?,?)",
            (group_id, user_id, action, detail[:500], utc_now()),
        )

    def log_world_event(self, group_id: str, event_type: str, title: str, description: str,
                        triggered_by: str | None = None, weather: str | None = None,
                        location: str | None = None) -> None:
        self.execute(
            "INSERT INTO world_event_logs(group_id,event_type,title,description,triggered_by,weather,location,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (group_id, event_type, title, description[:2000], triggered_by, weather, location, utc_now()),
        )

    def get_recent_events(self, group_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM world_event_logs WHERE group_id=? ORDER BY created_at DESC LIMIT ?",
            (group_id, max(1, min(limit, 500))),
        )

    def get_transactions(self, group_id: str, limit: int = 100, user_id: str | None = None) -> list[sqlite3.Row]:
        if user_id:
            return self.fetchall(
                "SELECT * FROM transactions WHERE group_id=? AND user_id=? ORDER BY created_at DESC LIMIT ?",
                (group_id, user_id, max(1, min(limit, 500))),
            )
        return self.fetchall(
            "SELECT * FROM transactions WHERE group_id=? ORDER BY created_at DESC LIMIT ?",
            (group_id, max(1, min(limit, 500))),
        )

    def create_monster_encounter(self, group_id: str, user_id: str, monster: dict[str, Any], expires_at: int) -> int:
        import json as _json
        skills = monster.get("skills", []) if isinstance(monster.get("skills", []), list) else []
        with self.transaction() as conn:
            cur=conn.execute(
                "INSERT INTO monster_encounters(group_id,user_id,monster_id,monster_name,hp,max_hp,attack,defense,reward_coins,reward_exp,reward_items_json,skills_json,status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (group_id,user_id,monster.get("id","custom"),monster["name"],monster["hp"],monster["hp"],monster.get("attack",1),monster.get("defense",0),monster.get("coins",0),monster.get("exp",0),_json.dumps(monster.get("items",{}),ensure_ascii=False),_json.dumps(skills,ensure_ascii=False),"active",utc_now(),expires_at),
            )
            return int(cur.lastrowid)
    def get_active_monster(self, group_id: str, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM monster_encounters WHERE group_id=? AND user_id=? AND status='active' AND expires_at>? ORDER BY id DESC LIMIT 1", (group_id,user_id,int(datetime.now(timezone.utc).timestamp())))
    def update_monster(self, encounter_id: int, **fields: Any) -> None:
        fields={k:v for k,v in fields.items() if k in {"hp","status","expires_at"}}
        if fields: self.execute("UPDATE monster_encounters SET "+",".join(f"{k}=?" for k in fields)+" WHERE id=?", (*fields.values(),encounter_id))

    def set_world_event(self, group_id: str, key: str, expires_at: int, effects: dict[str, Any]) -> None:
        self.update_group(
            group_id,
            current_event_key=key,
            current_event_expires_at=int(expires_at),
            current_event_effects_json=json.dumps(effects or {}, ensure_ascii=False),
        )

    def get_active_world_event(self, group_id: str) -> Optional[dict[str, Any]]:
        row = self.get_group(group_id)
        if not row or not row["current_event_key"]:
            return None
        now = int(datetime.now(timezone.utc).timestamp())
        if int(row["current_event_expires_at"] or 0) <= now:
            self.update_group(group_id, current_event_key=None, current_event_expires_at=0, current_event_effects_json="{}")
            return None
        try:
            effects = json.loads(row["current_event_effects_json"] or "{}")
            if not isinstance(effects, dict):
                effects = {}
        except Exception:
            effects = {}
        return {"key": row["current_event_key"], "expires_at": int(row["current_event_expires_at"] or 0), "effects": effects}

    def set_current_npc(self, group_id: str, npc: dict[str, Any], expires_at: int) -> None:
        self.update_group(
            group_id,
            current_npc_id=str(npc.get("id", "npc")),
            current_npc_name=str(npc.get("name", "神秘 NPC")),
            current_npc_role=str(npc.get("role", "旅人")),
            current_npc_description=str(npc.get("description", ""))[:1000],
            current_npc_actions_json=json.dumps(npc.get("actions", []), ensure_ascii=False),
            current_npc_expires_at=int(expires_at),
            last_npc_at=utc_now(),
        )

    def get_current_npc(self, group_id: str) -> Optional[dict[str, Any]]:
        row = self.get_group(group_id)
        if not row or not row["current_npc_id"]:
            return None
        now = int(datetime.now(timezone.utc).timestamp())
        if int(row["current_npc_expires_at"] or 0) <= now:
            self.clear_current_npc(group_id)
            return None
        # V1.6.1 used the static NPC template id (e.g. `fortune`) as the
        # interaction identity. That made a later spawn inherit old claims.
        # Migrate any still-active legacy NPC to a unique per-spawn identity.
        npc_id = str(row["current_npc_id"] or "")
        if ":" not in npc_id:
            npc_id = f"{npc_id}:{uuid.uuid4().hex[:12]}"
            self.update_group(group_id, current_npc_id=npc_id)
        try:
            actions = json.loads(row["current_npc_actions_json"] or "[]")
            if not isinstance(actions, list): actions = []
        except Exception:
            actions = []
        return {
            "id": npc_id, "name": row["current_npc_name"], "role": row["current_npc_role"],
            "description": row["current_npc_description"], "actions": actions, "expires_at": int(row["current_npc_expires_at"] or 0),
        }

    def clear_current_npc(self, group_id: str) -> None:
        self.update_group(group_id, current_npc_id=None, current_npc_name=None, current_npc_role=None, current_npc_description=None, current_npc_actions_json="[]", current_npc_expires_at=0)

    def npc_action_claimed(self, group_id: str, user_id: str, npc_id: str, action: str) -> bool:
        return bool(self.fetchone("SELECT 1 FROM npc_interactions WHERE group_id=? AND user_id=? AND npc_id=? AND action=?", (group_id,user_id,npc_id,action)))

    def mark_npc_action(self, group_id: str, user_id: str, npc_id: str, action: str) -> None:
        self.execute("INSERT OR IGNORE INTO npc_interactions(group_id,user_id,npc_id,action,created_at) VALUES(?,?,?,?,?)", (group_id,user_id,npc_id,action,utc_now()))

    def ensure_invite_code(self, user_id: str, length: int = 6) -> str:
        import secrets, string
        row = self.fetchone("SELECT code FROM invite_codes WHERE inviter_user_id=?", (str(user_id),))
        if row: return str(row["code"])
        alphabet = string.ascii_uppercase + string.digits
        for _ in range(20):
            code = "".join(secrets.choice(alphabet) for _ in range(max(4, min(12, int(length)))))
            try:
                self.execute("INSERT INTO invite_codes(code,inviter_user_id,created_at,use_count) VALUES(?,?,?,0)", (code,str(user_id),utc_now()))
                return code
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("无法生成邀请码")

    def get_invite_code(self, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM invite_codes WHERE inviter_user_id=?", (str(user_id),))

    def get_inviter_by_code(self, code: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM invite_codes WHERE code=?", (str(code).strip().upper(),))

    def count_invites(self, inviter_user_id: str) -> int:
        row=self.fetchone("SELECT COUNT(*) c FROM invite_records WHERE inviter_user_id=?", (str(inviter_user_id),))
        return int(row["c"] if row else 0)

    def invite_used_by(self, invitee_user_id: str) -> bool:
        row=self.fetchone("SELECT 1 FROM invite_records WHERE invitee_user_id=? LIMIT 1", (str(invitee_user_id),))
        return bool(row)

    def record_invite(self, inviter_user_id: str, invitee_user_id: str, code: str, rewards: dict) -> bool:
        if str(inviter_user_id)==str(invitee_user_id) or self.invite_used_by(invitee_user_id): return False
        with self.transaction() as conn:
            row=conn.execute("SELECT 1 FROM invite_codes WHERE code=? AND inviter_user_id=?", (str(code).upper(),str(inviter_user_id))).fetchone()
            if not row: return False
            conn.execute("INSERT INTO invite_records(inviter_user_id,invitee_user_id,code,coins_inviter,gems_inviter,coins_invitee,gems_invitee,created_at) VALUES(?,?,?,?,?,?,?,?)", (str(inviter_user_id),str(invitee_user_id),str(code).upper(),int(rewards.get('inviter_coins',0)),int(rewards.get('inviter_gems',0)),int(rewards.get('invitee_coins',0)),int(rewards.get('invitee_gems',0)),utc_now()))
            conn.execute("UPDATE invite_codes SET use_count=use_count+1 WHERE code=?", (str(code).upper(),))
        return True

    def start_auto_battle(self, group_id: str, user_id: str, target_type: str='monster', skill_mode: str='auto') -> None:
        self.execute("INSERT INTO auto_battles(group_id,user_id,target_type,skill_mode,enabled,last_turn_at,turns) VALUES(?,?,?,?,1,0,0) ON CONFLICT(group_id,user_id) DO UPDATE SET target_type=excluded.target_type,skill_mode=excluded.skill_mode,enabled=1", (group_id,user_id,target_type,skill_mode))

    def stop_auto_battle(self, group_id: str, user_id: str) -> None:
        self.execute("UPDATE auto_battles SET enabled=0 WHERE group_id=? AND user_id=?", (group_id,user_id))

    def get_auto_battle(self, group_id: str, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM auto_battles WHERE group_id=? AND user_id=? AND enabled=1", (group_id,user_id))

    def get_auto_battles_due(self, now_ts: int, interval: int=8) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM auto_battles WHERE enabled=1 AND (? - last_turn_at)>=? ORDER BY last_turn_at ASC LIMIT 100", (now_ts,interval))

    def touch_auto_battle(self, group_id: str, user_id: str, now_ts: int, turns_inc: int=1) -> None:
        self.execute("UPDATE auto_battles SET last_turn_at=?,turns=turns+? WHERE group_id=? AND user_id=?", (now_ts,turns_inc,group_id,user_id))

    def disable_all_auto_battle(self, group_id: str, user_id: str) -> None:
        self.stop_auto_battle(group_id,user_id)

    def register_group_member(self, group_id: str, user_id: str, name: str = "冒险者") -> None:
        if not group_id or not user_id or group_id == "__GLOBAL_USER__":
            return
        now = utc_now()
        self.execute(
            "INSERT INTO group_members(group_id,user_id,name,joined_at,last_seen_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(group_id,user_id) DO UPDATE SET name=excluded.name,last_seen_at=excluded.last_seen_at",
            (group_id, user_id, name or "冒险者", now, now),
        )

    def touch_group_member(self, group_id: str, user_id: str, name: str = "冒险者") -> None:
        if not group_id or not user_id or group_id == "__GLOBAL_USER__":
            return
        now = utc_now()
        self.execute(
            "UPDATE group_members SET name=?,last_seen_at=? WHERE group_id=? AND user_id=?",
            (name or "冒险者", now, group_id, user_id),
        )

    def get_user_groups(self, user_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT g.group_id,g.enabled,g.world_weather,g.world_location,gm.joined_at,gm.last_seen_at "
            "FROM group_members gm JOIN groups g ON g.group_id=gm.group_id "
            "WHERE gm.user_id=? ORDER BY gm.last_seen_at DESC",
            (user_id,),
        )

    def set_global_player_fields(self, user_id: str, fields: dict[str, Any]) -> Optional[sqlite3.Row]:
        allowed = {
            "name", "level", "exp", "coins", "gems", "stamina", "max_stamina",
            "luck", "renown", "profession", "title", "streak", "total_checkin",
            "banned", "tutorial_status", "tutorial_step", "hp", "max_hp",
            "last_checkin", "protected_until", "explore_count", "explore_day", "active_pet_id",
            "total_explores", "total_games", "total_work", "total_boss_damage", "total_earned_coins",
            "total_spent_coins", "last_action_at",
            "battle_attack", "battle_defense", "battle_crit_rate", "battle_dodge_rate", "battle_speed",
            "battle_wins", "battle_losses", "battle_draws", "battle_kills", "battle_deaths", "pvp_rating", "pvp_streak",
            "death_state", "respawn_at", "revive_count", "last_combat_group_id", "last_combat_at",
        }
        clean = {k: v for k, v in fields.items() if k in allowed}
        if not clean:
            return self.get_player("__GLOBAL_USER__", user_id)
        if "level" in clean:
            clean["level"] = max(1, min(999, int(clean["level"])))
        if "exp" in clean:
            clean["exp"] = max(0, int(clean["exp"]))
        if "coins" in clean:
            clean["coins"] = max(0, int(clean["coins"]))
        if "gems" in clean:
            clean["gems"] = max(0, int(clean["gems"]))
        if "max_stamina" in clean:
            clean["max_stamina"] = max(1, min(9999, int(clean["max_stamina"])) )
        if "stamina" in clean:
            clean["stamina"] = max(0, int(clean["stamina"]))
        if "max_hp" in clean:
            clean["max_hp"] = max(1, min(9999, int(clean["max_hp"])))
        if "hp" in clean:
            clean["hp"] = max(0, int(clean["hp"]))
        if "max_hp" in clean and "hp" not in clean:
            current = self.get_player("__GLOBAL_USER__", user_id)
            if current: clean["hp"] = min(int(current["hp"]), clean["max_hp"])
        if "max_hp" in clean and "hp" in clean:
            clean["hp"] = min(clean["hp"], clean["max_hp"])
        if "max_stamina" in clean and "stamina" not in clean:
            current = self.get_player("__GLOBAL_USER__", user_id)
            if current:
                clean["stamina"] = min(int(current["stamina"]), clean["max_stamina"])
        if "max_stamina" in clean and "stamina" in clean:
            clean["stamina"] = min(clean["stamina"], clean["max_stamina"])
        if "luck" in clean:
            clean["luck"] = max(0, min(9999, int(clean["luck"])))
        if "renown" in clean:
            clean["renown"] = max(0, min(999999, int(clean["renown"])))
        if "battle_attack" in clean: clean["battle_attack"] = max(1, min(999999, int(clean["battle_attack"])))
        if "battle_defense" in clean: clean["battle_defense"] = max(0, min(999999, int(clean["battle_defense"])))
        if "battle_crit_rate" in clean: clean["battle_crit_rate"] = max(0.0, min(95.0, float(clean["battle_crit_rate"])))
        if "battle_dodge_rate" in clean: clean["battle_dodge_rate"] = max(0.0, min(80.0, float(clean["battle_dodge_rate"])))
        if "battle_speed" in clean: clean["battle_speed"] = max(1, min(9999, int(clean["battle_speed"])))
        for counter in ("streak","total_checkin","tutorial_step","explore_count","total_explores","total_games","total_work","total_boss_damage","total_earned_coins","total_spent_coins"):
            if counter in clean:
                clean[counter] = max(0, int(clean[counter]))
        if "tutorial_step" in clean:
            clean["tutorial_step"] = min(10, clean["tutorial_step"])
        if "active_pet_id" in clean and clean["active_pet_id"] is not None:
            clean["active_pet_id"] = max(0, int(clean["active_pet_id"])) or None
        if "banned" in clean:
            clean["banned"] = 1 if bool(clean["banned"]) else 0
        clean["updated_at"] = utc_now()
        sets = ",".join(f"{k}=?" for k in clean)
        self.execute(f"UPDATE players SET {sets} WHERE group_id=? AND user_id=?", (*clean.values(), "__GLOBAL_USER__", user_id))
        return self.get_player("__GLOBAL_USER__", user_id)

    def get_global_players(self, limit: int = 1000, search: str = "") -> list[sqlite3.Row]:
        sql="""SELECT p.*,
            COALESCE((SELECT SUM(ms.messages) FROM message_stats ms WHERE ms.user_id=p.user_id),0) AS messages,
            COALESCE((SELECT COUNT(*) FROM pets pt WHERE pt.group_id='__GLOBAL_USER__' AND pt.user_id=p.user_id),0) AS pet_count,
            COALESCE((SELECT COUNT(*) FROM equipment e WHERE e.group_id='__GLOBAL_USER__' AND e.user_id=p.user_id),0) AS equipment_count,
            COALESCE((SELECT COUNT(*) FROM group_members gm WHERE gm.user_id=p.user_id),0) AS group_count,
            COALESCE((SELECT code FROM invite_codes ic WHERE ic.inviter_user_id=p.user_id LIMIT 1),'') AS invite_code,
            COALESCE((SELECT COUNT(*) FROM invite_records ir WHERE ir.inviter_user_id=p.user_id),0) AS invite_count
            FROM players p WHERE p.group_id='__GLOBAL_USER__'"""
        params=[]
        if search:
            sql += " AND (p.user_id LIKE ? OR p.player_uid LIKE ? OR p.name LIKE ?)"; params += [f"%{search}%",f"%{search}%",f"%{search}%"]
        sql += " ORDER BY p.level DESC,p.exp DESC LIMIT ?"; params.append(max(1,min(5000,limit)))
        return self.fetchall(sql,params)

    def get_dashboard_summary(self, group_id: str | None = None) -> dict[str, int]:
        if group_id:
            grow = self.fetchone("SELECT COUNT(*) c, COALESCE(SUM(message_count),0) msg FROM groups WHERE group_id=?", (group_id,))
            prow = self.fetchone(
                "SELECT COUNT(*) c, COALESCE(SUM(p.coins),0) coins, COALESCE(SUM(p.gems),0) gems, "
                "COALESCE(AVG(p.level),0) avg_level, COALESCE(SUM(p.total_explores),0) explores, "
                "COALESCE(SUM(p.total_games),0) games FROM group_members gm "
                "JOIN players p ON p.user_id=gm.user_id AND p.group_id='__GLOBAL_USER__' WHERE gm.group_id=?",
                (group_id,),
            )
            trow = self.fetchone(
                "SELECT COUNT(*) c, COALESCE(SUM(CASE WHEN coins_delta>0 THEN coins_delta ELSE 0 END),0) earned, "
                "COALESCE(SUM(CASE WHEN coins_delta<0 THEN -coins_delta ELSE 0 END),0) spent "
                "FROM transactions WHERE group_id='__GLOBAL_USER__'",
            )
            return {
                "groups": int(grow["c"] if grow else 0),
                "messages": int(grow["msg"] if grow else 0),
                "players": int(prow["c"] if prow else 0),
                "coins": int(prow["coins"] if prow else 0),
                "gems": int(prow["gems"] if prow else 0),
                "avg_level": round(float(prow["avg_level"] if prow else 0), 2),
                "explores": int(prow["explores"] if prow else 0),
                "games": int(prow["games"] if prow else 0),
                "earned": int(trow["earned"] if trow else 0),
                "spent": int(trow["spent"] if trow else 0),
            }
        grow = self.fetchone("SELECT COUNT(*) c, COALESCE(SUM(message_count),0) msg FROM groups")
        prow = self.fetchone(
            "SELECT COUNT(*) c, COALESCE(SUM(coins),0) coins, COALESCE(SUM(gems),0) gems, "
            "COALESCE(AVG(level),0) avg_level, COALESCE(SUM(total_explores),0) explores, "
            "COALESCE(SUM(total_games),0) games FROM players WHERE group_id='__GLOBAL_USER__'"
        )
        trow = self.fetchone(
            "SELECT COUNT(*) c, COALESCE(SUM(CASE WHEN coins_delta>0 THEN coins_delta ELSE 0 END),0) earned, "
            "COALESCE(SUM(CASE WHEN coins_delta<0 THEN -coins_delta ELSE 0 END),0) spent FROM transactions WHERE group_id='__GLOBAL_USER__'"
        )
        return {
            "groups": int(grow["c"] if grow else 0),
            "messages": int(grow["msg"] if grow else 0),
            "players": int(prow["c"] if prow else 0),
            "coins": int(prow["coins"] if prow else 0),
            "gems": int(prow["gems"] if prow else 0),
            "avg_level": round(float(prow["avg_level"] if prow else 0), 2),
            "explores": int(prow["explores"] if prow else 0),
            "games": int(prow["games"] if prow else 0),
            "earned": int(trow["earned"] if trow else 0),
            "spent": int(trow["spent"] if trow else 0),
        }

    def get_group_details(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.fetchall(
            """
            SELECT g.*,
                   COALESCE((SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=g.group_id),0) AS player_count,
                   COALESCE((SELECT SUM(p.coins) FROM players p JOIN group_members gm ON gm.user_id=p.user_id WHERE p.group_id='__GLOBAL_USER__' AND gm.group_id=g.group_id),0) AS coin_supply,
                   COALESCE((SELECT SUM(p.gems) FROM players p JOIN group_members gm ON gm.user_id=p.user_id WHERE p.group_id='__GLOBAL_USER__' AND gm.group_id=g.group_id),0) AS gem_supply,
                   COALESCE((SELECT AVG(p.level) FROM players p JOIN group_members gm ON gm.user_id=p.user_id WHERE p.group_id='__GLOBAL_USER__' AND gm.group_id=g.group_id),0) AS avg_level,
                   COALESCE((SELECT SUM(p.total_explores) FROM players p JOIN group_members gm ON gm.user_id=p.user_id WHERE p.group_id='__GLOBAL_USER__' AND gm.group_id=g.group_id),0) AS total_explores,
                   COALESCE((SELECT SUM(p.total_games) FROM players p JOIN group_members gm ON gm.user_id=p.user_id WHERE p.group_id='__GLOBAL_USER__' AND gm.group_id=g.group_id),0) AS total_games
            FROM groups g
            ORDER BY g.updated_at DESC LIMIT ?
            """,
            (max(1, min(limit, 500)),),
        )

    def get_player_dashboard(self, group_id: str, limit: int = 200, search: str = "") -> list[sqlite3.Row]:
        like = f"%{search}%"
        return self.fetchall(
            """
            SELECT p.*,\
                   COALESCE((SELECT COUNT(*) FROM pets x WHERE x.group_id=p.group_id AND x.user_id=p.user_id),0) AS pet_count,\
                   COALESCE((SELECT COUNT(*) FROM equipment x WHERE x.group_id=p.group_id AND x.user_id=p.user_id),0) AS equipment_count,\
                   COALESCE((SELECT COUNT(*) FROM inventory x WHERE x.group_id=p.group_id AND x.user_id=p.user_id AND x.qty>0),0) AS item_types,\
                   COALESCE((SELECT SUM(messages) FROM message_stats m WHERE m.group_id=p.group_id AND m.user_id=p.user_id),0) AS messages,\
                   COALESCE((SELECT MAX(created_at) FROM transactions t WHERE t.group_id=p.group_id AND t.user_id=p.user_id), NULL) AS last_transaction_at\
            FROM players p\
            WHERE p.group_id=? AND (p.user_id LIKE ? OR p.name LIKE ?)\
            ORDER BY p.level DESC,p.exp DESC,p.coins DESC LIMIT ?
            """,
            (group_id, like, like, max(1, min(limit, 1000))),
        )

    def get_dashboard_tasks(self, date_key: str, group_id: str | None = None, limit: int = 1000) -> list[sqlite3.Row]:
        """Return actual daily-task rows used by the game, including their global player names."""
        where = ["t.date_key=?"]
        params: list[Any] = [date_key]
        if group_id:
            # Current task data is player-global. When a group is selected, keep only
            # players who have actually entered that group. Legacy group-scoped rows
            # are accepted as well for backwards compatibility.
            where.append("(t.group_id='__GLOBAL_USER__' OR t.group_id=?)")
            params.append(group_id)
        sql = f"""
            SELECT t.*, COALESCE(p.name, t.user_id) AS player_name,
                   COALESCE(p.level, 1) AS player_level,
                   COALESCE((SELECT GROUP_CONCAT(gm2.group_id, ', ') FROM group_members gm2 WHERE gm2.user_id=t.user_id), '') AS member_groups
            FROM daily_tasks t
            LEFT JOIN players p ON p.user_id=t.user_id AND p.group_id='__GLOBAL_USER__'
            WHERE {' AND '.join(where)}
            ORDER BY t.completed DESC, t.user_id, t.task_id
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 5000)))
        rows = self.fetchall(sql, params)
        if group_id:
            # For global tasks, filter membership in Python because GROUP_CONCAT is
            # easier to keep compatible across older SQLite versions.
            rows = [r for r in rows if (r['group_id'] == group_id or group_id in str(r['member_groups'] or '').split(', '))]
        return rows[:max(1, min(int(limit), 5000))]

    def get_dashboard_tutorials(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT user_id,name,level,tutorial_status,tutorial_step,last_seen_at,updated_at FROM players WHERE group_id='__GLOBAL_USER__' ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(int(limit), 5000)),),
        )

    def get_group_action_logs(self, group_id: str, limit: int = 100) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM action_logs WHERE group_id=? ORDER BY created_at DESC LIMIT ?",
            (group_id, max(1, min(limit, 500))),
        )


    # ------------------------- player UID / PvP -------------------------
    def find_player_by_uid(self, player_uid: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM players WHERE group_id='__GLOBAL_USER__' AND player_uid=? LIMIT 1", (str(player_uid).strip(),))

    def get_active_duel_for_user(self, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("""
            SELECT * FROM duel_battles WHERE state='active' AND (p1_user_id=? OR p2_user_id=?)
            ORDER BY id DESC LIMIT 1
        """, (str(user_id),str(user_id)))

    def clear_duel_queue_user(self, user_id: str) -> None:
        self.execute("UPDATE duel_queue SET status='cancelled' WHERE user_id=? AND status='queued'", (str(user_id),))

    def get_duel_queue_user(self, user_id: str) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM duel_queue WHERE user_id=? AND status='queued' LIMIT 1", (str(user_id),))

    def enqueue_duel(self, user_id: str, group_id: str, session_origin: str, name: str, rating: int, expires_at: int) -> None:
        now=int(time.time())
        self.execute("""INSERT INTO duel_queue(user_id,group_id,session_origin,name,rating,joined_at,expires_at,status)
            VALUES(?,?,?,?,?,?,?,'queued') ON CONFLICT(user_id) DO UPDATE SET group_id=excluded.group_id,session_origin=excluded.session_origin,name=excluded.name,rating=excluded.rating,joined_at=excluded.joined_at,expires_at=excluded.expires_at,status='queued'""",
                     (str(user_id),str(group_id),str(session_origin),name or '冒险者',int(rating),now,int(expires_at)))

    def get_duel_candidates(self, now_ts: int, rating: int, rating_range: int, exclude_user: str, limit: int = 20) -> list[sqlite3.Row]:
        self.execute("UPDATE duel_queue SET status='expired' WHERE status='queued' AND expires_at<=?", (int(now_ts),))
        low=max(0,int(rating)-max(0,int(rating_range)))
        high=int(rating)+max(0,int(rating_range))
        return self.fetchall("""SELECT * FROM duel_queue WHERE status='queued' AND user_id!=? AND rating BETWEEN ? AND ?
            ORDER BY ABS(rating-?) ASC, joined_at ASC LIMIT ?""", (str(exclude_user),low,high,int(rating),max(1,min(int(limit),100))))

    def create_duel_battle(self, p1: dict[str,Any], p2: dict[str,Any], starter: str, expires_at: int) -> int:
        now=utc_now()
        with self.transaction() as conn:
            cur=conn.execute("""INSERT INTO duel_battles(
                p1_user_id,p1_group_id,p1_origin,p1_name,p2_user_id,p2_group_id,p2_origin,p2_name,state,turn_user_id,round_no,
                p1_hp,p1_max_hp,p2_hp,p2_max_hp,p1_guard,p2_guard,p1_stamina,p2_stamina,created_at,updated_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                p1['user_id'],p1['group_id'],p1['origin'],p1.get('name','冒险者'),p2['user_id'],p2['group_id'],p2['origin'],p2.get('name','冒险者'),
                'active',starter,1,int(p1['max_hp']),int(p1['max_hp']),int(p2['max_hp']),int(p2['max_hp']),0,0,100,100,now,now,int(expires_at)
            ))
            conn.execute("UPDATE duel_queue SET status='matched' WHERE user_id IN (?,?)", (p1['user_id'],p2['user_id']))
            return int(cur.lastrowid)

    def get_duel(self, battle_id: int) -> Optional[sqlite3.Row]:
        return self.fetchone("SELECT * FROM duel_battles WHERE id=?", (int(battle_id),))

    def update_duel(self, battle_id: int, **fields: Any) -> None:
        allowed={'state','turn_user_id','round_no','p1_hp','p2_hp','p1_guard','p2_guard','p1_stamina','p2_stamina','updated_at','expires_at','winner_user_id','loser_user_id','result_json','last_action_text'}
        clean={k:v for k,v in fields.items() if k in allowed}
        if clean:
            clean.setdefault('updated_at',utc_now())
            self.execute("UPDATE duel_battles SET "+','.join(f'{k}=?' for k in clean)+" WHERE id=?", (*clean.values(),int(battle_id)))

    def latest_finished_duel(self, user_id: str, window_seconds: int) -> Optional[sqlite3.Row]:
        cutoff=int(time.time())-max(1,int(window_seconds))
        return self.fetchone("""SELECT * FROM duel_battles WHERE state IN ('finished','expired') AND (p1_user_id=? OR p2_user_id=?)
            AND CAST(strftime('%s',updated_at) AS INTEGER)>=? ORDER BY id DESC LIMIT 1""", (str(user_id),str(user_id),cutoff))

    def insert_duel_message(self, battle_id: int, sender_user_id: str, recipient_user_id: str, message: str) -> int:
        cur=self.execute("INSERT INTO duel_messages(battle_id,sender_user_id,recipient_user_id,message,created_at,delivered) VALUES(?,?,?,?,?,0)", (int(battle_id),str(sender_user_id),str(recipient_user_id),str(message)[:500],utc_now()))
        return int(cur.lastrowid)

    def get_due_respawns(self, now_ts: int, limit: int=100) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM players WHERE group_id='__GLOBAL_USER__' AND death_state=1 AND respawn_at>0 AND respawn_at<=? ORDER BY respawn_at ASC LIMIT ?", (int(now_ts),max(1,min(int(limit),500))))

    def get_undelivered_duel_messages(self, recipient_user_id: str, limit: int=20) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM duel_messages WHERE recipient_user_id=? AND delivered=0 ORDER BY id ASC LIMIT ?", (str(recipient_user_id),max(1,min(int(limit),100))))

    def mark_duel_messages_delivered(self, recipient_user_id: str, ids: list[int]) -> None:
        if not ids:return
        marks=','.join('?' for _ in ids)
        self.execute(f"UPDATE duel_messages SET delivered=1 WHERE recipient_user_id=? AND id IN ({marks})", [str(recipient_user_id),*map(int,ids)])


class _Tx:
    _counter = 0
    def __init__(self, db: Database):
        self.db=db
        self.conn=db.conn
        self.savepoint=None
    def __enter__(self):
        self.db.lock.acquire()
        if self.conn.in_transaction:
            _Tx._counter += 1
            self.savepoint=f"gw_sp_{_Tx._counter}"
            self.conn.execute(f"SAVEPOINT {self.savepoint}")
        else:
            self.conn.execute("BEGIN IMMEDIATE")
        return self.conn
    def __exit__(self, exc_type, exc, tb):
        try:
            if self.savepoint:
                if exc_type:
                    self.conn.execute(f"ROLLBACK TO SAVEPOINT {self.savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {self.savepoint}")
            elif exc_type:
                self.conn.rollback()
            else:
                self.conn.commit()
        finally:
            self.db.lock.release()
        return False


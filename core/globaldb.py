from __future__ import annotations

GLOBAL = "__GLOBAL_USER__"

class GlobalPlayerDB:
    """Route player-owned data to one global scope while keeping world data per group."""
    PLAYER_METHODS = {
        "get_player","ensure_player","wallet_change","change_exp","change_stamina","set_player_field",
        "add_item","get_inventory","count_item","create_pet","change_hp","restore_hp_full","get_pets","get_active_pet","activate_pet",
        "add_equipment","get_equipment","get_equipped_stats","set_cooldown","cooldown_remaining",
        "get_task_rows","ensure_daily_tasks","progress_task","unlock_achievement","get_achievements",
        "set_tutorial","update_player_metrics"
    }
    def __init__(self, db): self._db=db
    def get_skills(self, user_id):
        return self._db.fetchall("SELECT * FROM player_skills WHERE user_id=? ORDER BY equipped DESC, skill_id", (user_id,))
    def set_skill(self, user_id, skill_id, level=1, equipped=0):
        self._db.execute("INSERT INTO player_skills(user_id,skill_id,level,equipped,created_at) VALUES(?,?,?,?,datetime('now')) ON CONFLICT(user_id,skill_id) DO UPDATE SET level=excluded.level,equipped=excluded.equipped", (user_id,skill_id,level,equipped))
    def set_skill_equipped(self, user_id, skill_id, equipped):
        self._db.execute("UPDATE player_skills SET equipped=? WHERE user_id=? AND skill_id=?", (equipped,user_id,skill_id))
    def skill_cooldown_remaining(self, user_id, skill_id):
        row=self._db.fetchone("SELECT expires_at FROM skill_cooldowns WHERE user_id=? AND skill_id=?", (user_id,skill_id))
        import time
        return max(0,int(row[0])-int(time.time())) if row else 0
    def set_skill_cooldown(self, user_id, skill_id, seconds):
        import time
        self._db.execute("INSERT INTO skill_cooldowns(user_id,skill_id,expires_at) VALUES(?,?,?) ON CONFLICT(user_id,skill_id) DO UPDATE SET expires_at=excluded.expires_at", (user_id,skill_id,int(time.time())+seconds))

    def get_global_player(self, user_id):
        return self._db.get_player(GLOBAL, str(user_id))

    def update_global_player(self, user_id, **fields):
        return self._db.set_global_player_fields(str(user_id), fields)

    def __getattr__(self, name):
        fn=getattr(self._db,name)
        if name in self.PLAYER_METHODS:
            def wrapped(*args, **kwargs):
                if args and len(args)>=1:
                    args=list(args); args[0]=GLOBAL; args=tuple(args)
                elif "group_id" in kwargs: kwargs["group_id"]=GLOBAL
                return fn(*args, **kwargs)
            return wrapped
        return fn
    @property
    def raw(self): return self._db

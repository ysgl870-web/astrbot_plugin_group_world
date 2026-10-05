const bridge = window.AstrBotPluginPage;
const state = { tab: 'overview', token: null, groups: [], groupId: null, settings: null, schema: null, players: [], search: '', groupSearch: '', summary: null, dynamicSeq: 0 };
const NAV = [
  ['overview','概览','▦'],['groups','群组管理','⌂'],['players','玩家中心','♙'],['economy','经济流水','￥'],['events','世界事件','✦'],
  ['settings','高级配置','⚙'],['logs','审计日志','◌'],['tasks','任务系统','✓'],['tutorial','新手教程','?']
];
const TITLES = {overview:['概览','世界状态、活跃度、经济与系统健康度。'],groups:['群组管理','逐群控制世界开关、Boss、事件与运行状态。'],players:['玩家中心','查看更完整的成长、活跃、财富与教程状态。'],economy:['经济流水','追踪金币、钻石的每一笔流入和流出。'],events:['世界事件','查看历史事件、天气和世界变化记录。'],settings:['高级配置','细粒度控制经济、玩法、权限、教程与群级覆盖。'],logs:['审计日志','管理员操作与玩家行为审计，便于定位异常。'],tasks:['任务系统','查看今日每日任务的真实进度、完成状态和奖励。'],tutorial:['新手教程','查看玩家教程状态与真实完成情况。']};
const $ = (id)=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function num(v){return Number(v||0).toLocaleString('zh-CN');}
function toast(msg){const el=$('toast');el.textContent=msg;el.classList.add('show');clearTimeout(window.__toast);window.__toast=setTimeout(()=>el.classList.remove('show'),2600);}
function setTheme(ctx){document.documentElement.dataset.theme=ctx?.isDark?'dark':'light';}
async function boot(){
  const ctx=await bridge.ready(); setTheme(ctx); bridge.onContext(setTheme);
  renderNav();
  const b=await bridge.apiGet('bootstrap');
  $('versionText').textContent=`群聊世界 V${b.version||'1.6.2'} · 作者 ysgl`;
  if(!b.authenticated){ if(!b.password_configured && !(b.web_admin_usernames||[]).length){ showLogin('请先在 AstrBot 插件配置中设置“Web 管理口令”或配置 Web 超级管理员账号。'); } else showLogin(''); }
  else await loadCore();
}
function showLogin(hint){$('loginOverlay').classList.remove('hidden');$('loginHint').textContent=hint||'';}
function renderNav(){ $('nav').innerHTML=NAV.map(([id,t,i])=>`<button data-tab="${id}" class="${state.tab===id?'active':''}">${i} <span>${t}</span></button>`).join(''); $('nav').querySelectorAll('button').forEach(b=>b.onclick=()=>{state.tab=b.dataset.tab;renderNav();render();}); }
async function login(){try{const r=await bridge.apiPost('login',{password:$('loginPassword').value});state.token=r.token;$('loginOverlay').classList.add('hidden');$('loginPassword').value='';await loadCore();toast('管理中心已解锁，有效期约 6 小时。');}catch(e){$('loginHint').textContent=e.message||'解锁失败';}}
function gparams(extra={}){return {...extra, ...(state.token?{token:state.token}:{})};}
async function apiGet(endpoint, params={}){return bridge.apiGet(endpoint,gparams(params));}
async function apiPost(endpoint, body={}){return bridge.apiPost(endpoint,{...body,...(state.token?{token:state.token}:{})});}
async function loadCore(){
  try{
    const [ov,gs,ss]=await Promise.all([apiGet('overview'),apiGet('groups'),apiGet('settings')]);
    state.summary=ov.summary||{}; state.groups=gs.groups||ov.groups||[]; state.settings=ss.config||{}; state.schema=ss.schema||{};
    if(state.groupId===null && state.groups.length) state.groupId=state.groups[0].group_id;
    renderGroupSelect(); render();
  }catch(e){ if(String(e.message).includes('无权')||String(e.message).includes('403')) showLogin(e.message); else toast(e.message||String(e)); }
}
function renderGroupSelect(){
  const sel=$('groupSelect');
  const globalOnly=['players','economy','settings','tutorial'];
  if(globalOnly.includes(state.tab)){sel.style.display='none';return;}
  sel.style.display='block';
  sel.innerHTML=`<option value="">全部群组</option>`+state.groups.map(g=>`<option value="${esc(g.group_id)}" ${String(g.group_id)===String(state.groupId)?'selected':''}>${esc(g.group_id)} · ${g.player_count||0}位玩家</option>`).join('');
  sel.value=state.groupId===null?'':String(state.groupId);
  sel.onchange=()=>{state.groupId=sel.value;render();};
}
function syncAiSettingsUi(){const master=$('[data-setting=ai_enabled]');if(!master)return;const childKeys=['ai_npc_dialogue_enabled','ai_max_reply_chars','ai_npc_prompt','auto_battle_ai_enabled','auto_battle_ai_aggression','auto_battle_defense_threshold','duel_ai_enhance','duel_ai_aggression'];for(const key of childKeys){const el=$(`[data-setting=\"${key}\"]`);if(!el)continue;const wrap=el.closest('.field')||el.parentElement;wrap?.classList.toggle('ai-disabled',!master.checked);el.disabled=!master.checked;}master.addEventListener('change',()=>syncAiSettingsUi(),{once:true});}
function render(){const [title,desc]=TITLES[state.tab];$('pageTitle').textContent=title;$('pageDesc').textContent=desc;renderGroupSelect();$('content').innerHTML=renderTab();attach();if(state.tab==='settings')syncAiSettingsUi();}
function renderTab(){switch(state.tab){case'overview':return renderOverview();case'groups':return renderGroups();case'players':return `<div id="playersRoot">加载玩家中…</div>`;case'economy':return `<div id="economyRoot">加载流水中…</div>`;case'events':return `<div id="eventsRoot">加载事件中…</div>`;case'settings':return renderSettings();case'logs':return `<div id="logsRoot">加载日志中…</div>`;case'tasks':return `<div id="tasksRoot">加载任务中…</div>`;case'tutorial':return `<div id="tutorialRoot">加载教程数据中…</div>`;default:return '';}}
function renderOverview(){const summary=state.summary||{};const total=Number(summary.players||0),coins=Number(summary.coins||0),msgs=Number(summary.messages||0),boss=state.groups.filter(g=>g.boss_active).length;const active=state.groups.reduce((a,g)=>a+Number(g.today_active_users||0),0);const avg=Number(summary.avg_level||0);const max=Math.max(1,...state.groups.map(g=>Number(g.player_count||0)));
 return `<div class="grid"><div class="metric"><div class="label">群组</div><div class="value">${state.groups.length}</div><div class="sub">已被插件记录的群</div></div><div class="metric"><div class="label">玩家</div><div class="value">${num(total)}</div><div class="sub">跨群角色总数</div></div><div class="metric"><div class="label">金币流通</div><div class="value">${num(coins)}</div><div class="sub">当前玩家余额合计</div></div><div class="metric"><div class="label">世界 Boss</div><div class="value">${boss}</div><div class="sub">当前活跃 Boss 数</div></div></div>
 <div class="split"><section class="card"><div class="card-head"><div><div class="card-title">世界运行概览</div><div class="section-desc">今日活跃、消息、平均等级与各群规模。</div></div><button class="ghost small" id="overviewRefresh">刷新</button></div><div class="stat-stack"><div class="mini"><div class="k">今日活跃用户</div><div class="v">${num(active)}</div></div><div class="mini"><div class="k">累计消息</div><div class="v">${num(msgs)}</div></div><div class="mini"><div class="k">平均等级</div><div class="v">${avg.toFixed(2)}</div></div><div class="mini"><div class="k">累计探索</div><div class="v">${num(state.groups.reduce((a,g)=>a+(g.total_explores||0),0))}</div></div></div><div class="bars" style="margin-top:18px">${state.groups.slice(0,10).map(g=>`<div class="bar-row"><span>${esc(g.group_id)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,(Number(g.player_count||0)/max)*100)}%"></div></div><b>${g.player_count||0} 人</b></div>`).join('')||'<div class="empty">暂无群数据</div>'}</div></section>
 <section class="card"><div class="card-head"><div><div class="card-title">当前群状态</div><div class="section-desc">${state.groupId?`群 ${esc(state.groupId)}`:'选择一个群查看详细状态'}</div></div></div>${renderSelectedGroup()}</section></div>`;}
function renderSelectedGroup(){const g=state.groups.find(x=>x.group_id===state.groupId)||state.groups[0];if(!g)return '<div class="empty">还没有记录到群消息。</div>';return `<div class="kv"><div>状态</div><div><span class="pill ${g.enabled?'good':'danger'}">${g.enabled?'已启用':'已关闭'}</span></div><div>天气</div><div>${esc(g.world_weather)}</div><div>地点</div><div>${esc(g.world_location)}</div><div>玩家</div><div>${num(g.player_count)}</div><div>今日消息</div><div>${num(g.today_messages)}</div><div>今日活跃</div><div>${num(g.today_active_users)}</div><div>金币流通</div><div>${num(g.coin_supply)}</div><div>钻石流通</div><div>${num(g.gem_supply)}</div><div>Boss</div><div>${g.boss_active?`${esc(g.boss_name)} · ${num(g.boss_hp)}/${num(g.boss_max_hp)}`:'暂无'}</div></div>`;}
function renderGroups(){
  const q=String(state.groupSearch||'').trim().toLowerCase();
  const rows=state.groups.filter(g=>{
    if(!q)return true;
    return String(g.group_id||'').toLowerCase().includes(q) || String(g.session_origin||'').toLowerCase().includes(q);
  });
  return `<section class="card"><div class="card-head"><div><div class="card-title">群组控制台</div><div class="section-desc">可搜索群聊、调整世界规则、召唤 NPC、结束事件与管理 Boss；所有后台操作都会记录审计日志。</div></div><div class="toolbar"><input id="groupSearch" value="${esc(state.groupSearch)}" placeholder="搜索群聊 / 群号 / 会话来源"/><button class="ghost" id="searchGroups">搜索群聊</button></div></div><div class="notice" style="margin-bottom:12px"><strong>群聊：${rows.length}</strong>　当前展示 ${rows.length} / ${state.groups.length}，搜索只在已记录群聊中进行。</div><div class="table-wrap"><table class="table"><thead><tr><th>群 ID</th><th>状态</th><th>玩家</th><th>今日消息</th><th>今日活跃</th><th>天气 / 地点</th><th>事件 / NPC</th><th>Boss</th><th>操作</th></tr></thead><tbody>${rows.map(g=>`<tr><td class="code">${esc(g.group_id)}</td><td><span class="pill ${g.enabled?'good':'danger'}">${g.enabled?'启用':'关闭'}</span><br><span class="muted">事件 ${g.world_event_enabled?'开':'关'} · 怪物 ${g.monster_enabled?'开':'关'}</span></td><td>${g.player_count||0}</td><td>${num(g.today_messages)}</td><td>${num(g.today_active_users)}</td><td>${esc(g.world_weather)} · ${esc(g.world_location)}</td><td>${g.current_event_key?`✦ ${esc(g.current_event_key)}`:'—'}${g.current_npc_name?`<br>🧑‍🌾 ${esc(g.current_npc_name)}`:''}</td><td>${g.boss_active?`${esc(g.boss_name)}<br>${num(g.boss_hp)}/${num(g.boss_max_hp)}`:'—'}</td><td><div class="actions"><button class="small" data-group-action="toggle" data-group="${esc(g.group_id)}">${g.enabled?'关闭':'开启'}</button><button class="small" data-group-action="event" data-group="${esc(g.group_id)}">触发事件</button><button class="small" data-group-action="boss_start" data-group="${esc(g.group_id)}">开 Boss</button><button class="small danger" data-group-action="boss_end" data-group="${esc(g.group_id)}">结束 Boss</button><button class="small" data-group-editor="${esc(g.group_id)}">世界设置</button></div></td></tr>`).join('')||'<tr><td colspan="9"><div class="empty">没有匹配的群聊</div></td></tr>'}</tbody></table></div></section>`;
}
function renderSettings(){const groups=[['权限与访问',['owner_user_ids','admin_user_ids','session_admin_enabled','session_admin_allowed_actions','web_admin_usernames','web_admin_password']],['群与基础',['enabled','disabled_group_ids','group_overrides_json','default_new_player_coins','default_new_player_gems','max_stamina','stamina_regen_minutes','stamina_regen_amount','new_player_protection_hours']],['经济系统',['transfer_max_coins','profession_change_cost','profession_cooldown_days','checkin_min_coins','checkin_max_coins','checkin_streak_bonus_per_day','checkin_streak_bonus_cap','shop_enabled','shop_discount_percent','shop_catalog_json']],['探索 / 成长',['explore_enabled','explore_daily_limit','explore_stamina_cost','explore_deep_extra_cost','explore_danger_extra_cost','explore_reward_multiplier','explore_rare_bonus_percent','explore_danger_percent','monster_enabled','explore_monster_chance_percent','explore_monster_max_count','explore_monster_multi_chance_percent','monster_encounter_minutes','monster_reward_multiplier','monster_catalog_json','equipment_enabled','equipment_max_level','equipment_ore_cost','equipment_upgrade_base_rate','skill_system_enabled','skill_slot_limit','skill_bond_enabled']],['NPC / 世界互动',['npc_enabled','npc_chance_percent','npc_interval_minutes','npc_duration_minutes']],['AI 智能设置',['ai_enabled','ai_npc_dialogue_enabled','ai_max_reply_chars','ai_npc_prompt','auto_battle_ai_enabled','auto_battle_ai_aggression','auto_battle_defense_threshold','duel_ai_enhance','duel_ai_aggression']],['宠物 / 任务',['pet_enabled','pet_draw_cost','pet_draw_cooldown_seconds','pet_rarity_json','daily_tasks_enabled','daily_task_reward_multiplier','achievements_enabled']],['游戏 / 活动',['game_enabled','game_guess_reward','game_rps_win_reward','game_rps_draw_reward','game_bomb_safe_reward','fishing_enabled','fishing_stamina_cost','mining_enabled','mining_stamina_cost','work_enabled','work_cooldown_minutes']],['战斗 / 复活 / 决斗',['auto_battle_enabled','auto_battle_interval_seconds','auto_battle_max_turns','auto_battle_broadcast_every','auto_buy_revive_item','revive_item_cost','revive_hp_percent','respawn_countdown_seconds','respawn_hp_percent','duel_enabled','duel_queue_timeout_seconds','duel_match_rating_range','duel_turn_timeout_seconds','duel_rating_delta','duel_message_window_seconds']],['Boss / 世界',['boss_enabled','enable_auto_boss','boss_interval_hours','boss_duration_hours','boss_max_hp','boss_damage_base','boss_attack_cooldown_seconds','boss_top_reward','boss_reward_decay','boss_min_reward','boss_skill_chance_percent','enable_auto_world_events','world_event_interval_minutes','world_event_chance','event_catalog_json']],['教程 / 数据',['tutorial_enabled','tutorial_auto_start','tutorial_allow_skip','tutorial_pages_json','proactive_tips_enabled','proactive_tip_interval_minutes','tip_catalog_json','data_retention_days','max_dashboard_rows']]];return `<section class="card"><div class="card-head"><div><div class="card-title">高级配置</div><div class="section-desc">优先使用本页管理复杂设置；AstrBot 原生配置页仍可作为底层配置入口。</div></div><div class="actions"><button class="ghost" id="reloadSettings">重新读取</button><button class="primary" id="saveSettings">保存全部修改</button></div></div><div class="notice"><strong>AI 总开关：</strong>关闭后所有 AI 增强统一停用；下面子选项保持独立配置，默认均为开启。关闭总开关不会影响普通游戏、普通自动战斗与决斗基础逻辑。<br><strong>权限模型：</strong>世界超级管理员（owner_user_ids / Web 超管） ＞ 会话管理员 ＞ 玩家。</div>${groups.map(([title,keys])=>`<div class="form-section"><div class="card-title">${title}</div><div class="form-grid">${keys.filter(k=>state.schema[k]).map(renderField).join('')}</div></div>`).join('')}</section>`;}
function renderField(key){const sp=state.schema[key]||{}, val=state.settings[key]??sp.default??'';const desc=sp.description||key;const hint=sp.hint||'';let input='';if(sp.type==='bool'){input=`<label style="display:flex;align-items:center;gap:8px"><input data-setting="${esc(key)}" type="checkbox" ${val?'checked':''}/> <span>${esc(desc)}</span></label>`;return `<div class="field"><div>${input}</div><div class="hint">${esc(hint)}</div></div>`;}if(sp.type==='text'){input=`<textarea data-setting="${esc(key)}">${esc(val)}</textarea>`;}else if(sp.type==='int'||sp.type==='float'){input=`<input data-setting="${esc(key)}" type="number" value="${esc(val)}" ${sp.slider?`min="${sp.slider.min}" max="${sp.slider.max}" step="${sp.slider.step}"`:''}/>`;}else{input=`<input data-setting="${esc(key)}" type="${sp.secret?'password':'text'}" value="${esc(val==='********'?'':val)}" placeholder="${sp.secret?'留空表示不修改':desc}"/>`; }return `<div class="field"><label>${esc(desc)}</label>${input}<div class="hint">${esc(hint)}</div></div>`;}
function renderTasks(rows,summary,date){
  const statusLabel=r=>r.completed?'✅ 已完成':`⬜ ${r.progress||0}/${r.target||0}`;
  return `<section class="card"><div class="card-head"><div><div class="card-title">每日任务数据</div><div class="section-desc">日期：${esc(date||'')} · 这里读取的是玩家实际任务记录，不再只显示静态说明。</div></div><span class="pill ${summary.rows?'good':'muted'}">${num(summary.completed||0)} 项完成</span></div><div class="grid"><div class="metric"><div class="label">任务记录</div><div class="value">${num(summary.rows||rows.length)}</div></div><div class="metric"><div class="label">涉及玩家</div><div class="value">${num(summary.players||0)}</div></div><div class="metric"><div class="label">已完成</div><div class="value">${num(summary.completed||0)}</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>玩家</th><th>任务</th><th>进度</th><th>奖励</th><th>状态</th><th>群组记录</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.player_name||r.user_id)}</b><br><span class="code">${esc(r.user_id)}</span> · Lv.${r.player_level||1}</td><td>${esc(({checkin:'完成签到',explore:'完成探索',game:'完成小游戏'})[r.task_id]||r.task_id)}</td><td>${num(r.progress)}/${num(r.target)}</td><td>💰 ${num(r.reward_coins)} · ⭐ ${num(r.reward_exp)}</td><td>${statusLabel(r)}</td><td>${esc(r.member_groups||'—')}</td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">暂无任务记录。玩家执行 /任务 后会产生数据。</div></td></tr>'}</tbody></table></div></section>`;
}
function renderTutorial(rows,counts){
  return `<section class="split"><div class="card"><div class="card-head"><div><div class="card-title">教程状态</div><div class="section-desc">展示玩家真实教程状态，不再只显示静态教程说明。</div></div><span class="pill good">完成 ${num(counts.completed||0)} 人</span></div><div class="grid"><div class="metric"><div class="label">进行中</div><div class="value">${num(counts.pending||0)}</div></div><div class="metric"><div class="label">已完成</div><div class="value">${num(counts.completed||0)}</div></div><div class="metric"><div class="label">已跳过</div><div class="value">${num(counts.skipped||0)}</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>玩家</th><th>教程状态</th><th>当前页</th><th>最近活跃</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.name||r.user_id)}</b><br><span class="code">${esc(r.user_id)}</span></td><td>${r.tutorial_status==='completed'?'✅ 已完成':(r.tutorial_status==='skipped'?'⏭️ 已跳过':'📖 进行中')}</td><td>${num(r.tutorial_step||0)}</td><td>${esc(r.last_seen_at||r.updated_at||'—')}</td></tr>`).join('')||'<tr><td colspan="4"><div class="empty">暂无教程数据。</div></td></tr>'}</tbody></table></div></div><div class="card"><div class="card-head"><div class="card-title">教程流程</div></div>${[1,2,3,4,5,6].map((n,i)=>`<div class="notice" style="margin-top:10px"><strong>${n}. ${['认识世界','经济与体力','探索世界','装备与宠物','小游戏与 Boss','社交与长期成长'][i]}</strong><br>${['/世界 /我的 /帮助','签到、任务、体力与经济流水','地图、普通/深度/危险探索','宠物、装备、强化','小游戏与世界 Boss','排行榜、转账、成就与长期成长'][i]}</div>`).join('')}<div class="notice" style="margin-top:12px"><strong>自定义教程</strong><br>在“高级配置 → 教程 / 数据”中修改 <span class="code">tutorial_pages_json</span>。玩家可使用 <span class="code">/教程</span>、<span class="code">/继续教程</span>、<span class="code">/跳过教程</span>、<span class="code">/教程 重开</span>。</div></div></section>`;
}
async function loadDynamic(){
  const tab=state.tab, gid=state.groupId || '', seq=++state.dynamicSeq;
  const map={players:'playersRoot',economy:'economyRoot',events:'eventsRoot',logs:'logsRoot',tasks:'tasksRoot',tutorial:'tutorialRoot'};
  const rootId=map[tab];
  if(!rootId)return;
  try{
    let html='';
    if(tab==='players'){
      const d=await apiGet('players',{search:state.search}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      state.players=d.players||[]; html=renderPlayers();
    }else if(tab==='economy'){
      const d=await apiGet('transactions',{}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      html=renderEconomy(d.transactions||[]);
    }else if(tab==='events'){
      const d=await apiGet('events',{group_id:gid}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      html=renderEvents(d.events||[],gid);
    }else if(tab==='logs'){
      const d=await apiGet('logs',{group_id:gid}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      html=renderLogs(d,gid);
    }else if(tab==='tasks'){
      const d=await apiGet('tasks',{group_id:gid}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      html=renderTasks(d.tasks||[],d.summary||{},d.date||'');
    }else if(tab==='tutorial'){
      const d=await apiGet('tutorials',{}); if(state.tab!==tab||state.dynamicSeq!==seq)return;
      html=renderTutorial(d.tutorials||[],d.counts||{});
    }
    const root=$(rootId);
    if(!root || !root.isConnected || state.tab!==tab || state.dynamicSeq!==seq)return;
    root.outerHTML=html;
    attach();
  }catch(e){
    if(state.tab===tab&&state.dynamicSeq===seq){
      const root=$(rootId);
      if(root&&root.isConnected) root.innerHTML=`<div class="empty">数据加载失败：${esc(e.message||String(e))}。请点击右上角“刷新数据”重试。</div>`;
    }
    if(state.tab===tab)toast(e.message||String(e));
  }
}
function renderPlayers(){return `<section class="card"><div class="card-head"><div><div class="card-title">玩家数据中心</div><div class="section-desc">这里只显示全局唯一玩家。同一用户跨群共享等级、金币、钻石、背包、宠物、装备与成长数据；“跨群”来自实际进入群聊世界的记录，而不是群消息数量。</div></div><div class="toolbar"><input id="playerSearch" value="${esc(state.search)}" placeholder="搜索昵称 / 玩家UID / QQ/平台ID"/><button class="ghost" id="searchPlayers">搜索</button></div></div><div class="notice" style="margin-bottom:12px"><strong>总玩家：${num((state.summary||{}).players||state.players.length)}</strong>　显示 ${state.players.length} 人。群聊人数不会重复计入全局玩家总数。</div><div class="table-wrap"><table class="table"><thead><tr><th>用户</th><th>跨群</th><th>成长</th><th>资产</th><th>状态</th><th>活跃</th><th>系统数据</th><th>操作</th></tr></thead><tbody>${state.players.map(p=>`<tr><td><b>${esc(p.name)}</b><br><span class="code">UID ${esc(p.player_uid||'—')}</span><br><span class="muted">QQ/平台ID：${esc(p.user_id)}</span></td><td>${num(p.group_count||0)} 个群<br><span class="muted">邀请 ${num(p.invite_count||0)} 人</span><br><span class="code">${esc(p.invite_code||'—')}</span></td><td>Lv.${p.level}<br>${num(p.exp)} EXP<br>${esc(p.profession)}</td><td>💰 ${num(p.coins)}<br>💎 ${num(p.gems)}<br>❤️ ${p.stamina}/${p.max_stamina}</td><td>${p.banned?'🚫 已封禁':'✅ 正常'}<br><span class="muted">${esc(p.tutorial_status||'pending')} / ${p.tutorial_step}</span></td><td>${esc(p.last_seen_at||'—')}</td><td>⚔️ ${num(p.battle_wins)}胜 / ${num(p.battle_losses)}负<br>🏆 PVP ${num(p.pvp_rating)}<br>💀 ${p.death_state?'死亡':'存活'}<br>⚔️ ATK ${num(p.battle_attack)} / DEF ${num(p.battle_defense)}</td><td><div class="actions"><button class="small ${p.banned?'success':'danger'}" data-player-action="${p.banned?'unban':'ban'}" data-user="${esc(p.user_id)}">${p.banned?'解封':'封禁'}</button><button class="small" data-player-action="grant_coins" data-user="${esc(p.user_id)}">+金币</button><button class="small" data-player-action="take_coins" data-user="${esc(p.user_id)}">-金币</button><button class="small" data-player-action="grant_gems" data-user="${esc(p.user_id)}">+钻石</button><button class="small" data-player-action="set_level" data-user="${esc(p.user_id)}">设等级</button><button class="small" data-player-action="edit" data-user="${esc(p.user_id)}">编辑数据</button><button class="small" data-player-action="tutorial_reset" data-user="${esc(p.user_id)}">重开教程</button><button class="small danger" data-player-action="reset" data-user="${esc(p.user_id)}">重置</button></div></td></tr>`).join('')||'<tr><td colspan="8"><div class="empty">暂无玩家</div></td></tr>'}</tbody></table></div></section>`;}
function renderEconomy(rows){return `<section class="card"><div class="card-head"><div><div class="card-title">经济流水</div><div class="section-desc">所有钱包变化都进入流水表，管理员可核查余额变化原因。</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>用户</th><th>类型</th><th>金币变化</th><th>钻石变化</th><th>变更后余额</th><th>备注</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.created_at)}</td><td class="code">${esc(r.user_id)}</td><td>${esc(r.kind)}</td><td class="${r.coins_delta>0?'good':''}">${r.coins_delta>0?'+':''}${num(r.coins_delta)}</td><td>${r.gems_delta>0?'+':''}${num(r.gems_delta)}</td><td>${num(r.coins_balance)} / 💎${num(r.gems_balance)}</td><td>${esc(r.note)}</td></tr>`).join('')||'<tr><td colspan="7"><div class="empty">暂无流水</div></td></tr>'}</tbody></table></div></section>`;}
function renderEvents(rows,gid){return `<section class="card"><div class="card-head"><div><div class="card-title">世界事件历史</div><div class="section-desc">${gid?`当前群：${esc(gid)}`:'当前显示全部群组'} · 天气、事件类型、触发来源和描述都会保留下来。</div></div><button class="ghost small" id="eventsRefresh">刷新</button></div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>类型</th><th>标题</th><th>天气</th><th>地点</th><th>触发者</th><th>详情</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.created_at)}</td><td>${esc(r.event_type)}</td><td>${esc(r.title)}</td><td>${esc(r.weather||'')}</td><td>${esc(r.location||'')}</td><td>${esc(r.triggered_by||'自动')}</td><td>${esc(r.description)}</td></tr>`).join('')||'<tr><td colspan="7"><div class="empty">暂无事件</div></td></tr>'}</tbody></table></div></section>`;}
function renderLogs(d,gid){return `<section class="split"><div class="card"><div class="card-head"><div class="card-title">管理员审计</div><div class="section-desc">${gid?`当前群：${esc(gid)}`:'当前显示全部群组'}</div></div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>管理员</th><th>动作</th><th>目标</th><th>详情</th></tr></thead><tbody>${(d.admin_logs||[]).map(r=>`<tr><td>${esc(r.created_at)}</td><td>${esc(r.admin_id)}</td><td>${esc(r.action)}</td><td>${esc(r.target_user_id||'')}</td><td>${esc(r.detail)}</td></tr>`).join('')||'<tr><td colspan="5"><div class="empty">暂无日志</div></td></tr>'}</tbody></table></div></div><div class="card"><div class="card-head"><div class="card-title">玩家行为</div></div><div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>用户</th><th>动作</th><th>详情</th></tr></thead><tbody>${(d.action_logs||[]).map(r=>`<tr><td>${esc(r.created_at)}</td><td>${esc(r.user_id)}</td><td>${esc(r.action)}</td><td>${esc(r.detail)}</td></tr>`).join('')||'<tr><td colspan="4"><div class="empty">暂无行为</div></td></tr>'}</tbody></table></div></div></section>`;}
function openGroupEditor(groupId){
  const g=state.groups.find(x=>String(x.group_id)===String(groupId));
  if(!g){toast('群聊数据不存在');return;}
  const set=(id,v)=>{const el=$(id);if(el)el.value=v??'';};
  const check=(id,v)=>{const el=$(id);if(el)el.checked=!!v;};
  const modalLabel=$('groupModalGroup'); if(modalLabel)modalLabel.textContent=`群 ID：${groupId}`;
  set('gf_weather',g.world_weather);set('gf_location',g.world_location);
  check('gf_event_enabled',g.world_event_enabled);check('gf_explore_enabled',g.explore_enabled);check('gf_monster_enabled',g.monster_enabled);
  set('gf_monster_chance',g.monster_chance_percent??16);set('gf_monster_max',g.monster_max_count??3);set('gf_monster_multi',g.monster_multi_chance_percent??28);
  check('gf_npc_enabled',g.npc_enabled);set('gf_npc_chance',g.npc_chance_percent??10);set('gf_npc_interval',g.npc_interval_minutes??120);check('gf_ai_enabled',g.ai_enabled);
  const modal=$('groupModal'); if(modal)modal.dataset.group=String(groupId);
  modal?.classList.remove('hidden');
}
async function saveGroupSettings(){
  const modal=$('groupModal'); const groupId=modal?.dataset.group||'';
  if(!groupId){toast('未选择群聊');return;}
  const val=id=>$(id)?.value||''; const checked=id=>!!$(id)?.checked;
  try{
    const r=await apiPost('group/action',{group_id:groupId,action:'save_settings',settings:{world_weather:val('gf_weather').trim(),world_location:val('gf_location').trim(),world_event_enabled:checked('gf_event_enabled')?1:0,explore_enabled:checked('gf_explore_enabled')?1:0,monster_enabled:checked('gf_monster_enabled')?1:0,monster_chance_percent:Number(val('gf_monster_chance')),monster_max_count:Number(val('gf_monster_max')),monster_multi_chance_percent:Number(val('gf_monster_multi')),npc_enabled:checked('gf_npc_enabled')?1:0,npc_chance_percent:Number(val('gf_npc_chance')),npc_interval_minutes:Number(val('gf_npc_interval')),ai_enabled:checked('gf_ai_enabled')?1:0}});
    toast(r.message||'群世界设置已保存');
    modal.classList.add('hidden');
    await loadCore();
  }catch(e){toast(e.message||String(e));}
}
function closeGroupEditor(){$('groupModal')?.classList.add('hidden');}

async function openPlayerEditor(user){
  try{
    const p=state.players.find(x=>String(x.user_id)===String(user));
    if(!p){toast('玩家数据不存在');return;}
    const set=(id,v)=>{const el=$(id);if(el)el.value=v??'';};
    const text=(id)=>String($(id)?.value??'').trim();
    const number=(id)=>Number($(id)?.value??0);
    const userLabel=$('playerModalUser'); if(userLabel)userLabel.textContent=`UID：${p.player_uid||'—'} ｜ QQ/平台ID：${user}`;
    set('pf_name',p.name);set('pf_level',p.level);set('pf_exp',p.exp);set('pf_battle_attack',p.battle_attack||50);set('pf_battle_defense',p.battle_defense||5);set('pf_battle_crit_rate',p.battle_crit_rate??8);set('pf_battle_dodge_rate',p.battle_dodge_rate??3);set('pf_battle_speed',p.battle_speed||100);set('pf_pvp_rating',p.pvp_rating||1000);set('pf_pvp_streak',p.pvp_streak||0);set('pf_battle_wins',p.battle_wins||0);set('pf_battle_losses',p.battle_losses||0);set('pf_battle_draws',p.battle_draws||0);set('pf_battle_kills',p.battle_kills||0);set('pf_battle_deaths',p.battle_deaths||0);set('pf_death_state',p.death_state||0);set('pf_respawn_at',p.respawn_at||0);set('pf_revive_count',p.revive_count||0);set('pf_coins',p.coins);set('pf_gems',p.gems);set('pf_stamina',p.stamina);set('pf_max_stamina',p.max_stamina);set('pf_hp',p.hp);set('pf_max_hp',p.max_hp);set('pf_luck',p.luck);set('pf_renown',p.renown);set('pf_profession',p.profession);set('pf_title',p.title);set('pf_streak',p.streak);set('pf_total_checkin',p.total_checkin);set('pf_tutorial_status',p.tutorial_status||'pending');set('pf_tutorial_step',p.tutorial_step||0);
    set('pf_last_checkin',p.last_checkin);set('pf_protected_until',p.protected_until);set('pf_explore_count',p.explore_count||0);set('pf_explore_day',p.explore_day);set('pf_total_explores',p.total_explores||0);set('pf_total_games',p.total_games||0);set('pf_total_work',p.total_work||0);set('pf_total_boss_damage',p.total_boss_damage||0);set('pf_total_earned_coins',p.total_earned_coins||0);set('pf_total_spent_coins',p.total_spent_coins||0);set('pf_active_pet_id',p.active_pet_id??'');set('pf_last_action_at',p.last_action_at);
    $('playerModal')?.classList.remove('hidden');
    $('playerSave').onclick=async()=>{
      const activePet=text('pf_active_pet_id');
      const fields={name:text('pf_name'),level:number('pf_level'),exp:number('pf_exp'),coins:number('pf_coins'),gems:number('pf_gems'),battle_attack:number('pf_battle_attack'),battle_defense:number('pf_battle_defense'),battle_crit_rate:number('pf_battle_crit_rate'),battle_dodge_rate:number('pf_battle_dodge_rate'),battle_speed:number('pf_battle_speed'),pvp_rating:number('pf_pvp_rating'),pvp_streak:number('pf_pvp_streak'),battle_wins:number('pf_battle_wins'),battle_losses:number('pf_battle_losses'),battle_draws:number('pf_battle_draws'),battle_kills:number('pf_battle_kills'),battle_deaths:number('pf_battle_deaths'),death_state:number('pf_death_state'),respawn_at:number('pf_respawn_at'),revive_count:number('pf_revive_count'),stamina:number('pf_stamina'),max_stamina:number('pf_max_stamina'),hp:number('pf_hp'),max_hp:number('pf_max_hp'),luck:number('pf_luck'),renown:number('pf_renown'),profession:text('pf_profession'),title:text('pf_title'),streak:number('pf_streak'),total_checkin:number('pf_total_checkin'),tutorial_status:String($('pf_tutorial_status')?.value||'pending'),tutorial_step:number('pf_tutorial_step'),last_checkin:text('pf_last_checkin')||'',protected_until:text('pf_protected_until')||'',explore_count:number('pf_explore_count'),explore_day:text('pf_explore_day')||'',total_explores:number('pf_total_explores'),total_games:number('pf_total_games'),total_work:number('pf_total_work'),total_boss_damage:number('pf_total_boss_damage'),total_earned_coins:number('pf_total_earned_coins'),total_spent_coins:number('pf_total_spent_coins'),active_pet_id:activePet,last_action_at:text('pf_last_action_at')||''};
      try{const r=await apiPost('player/action',{user_id:user,action:'edit',fields});toast(r.message||'玩家数据已更新');$('playerModal')?.classList.add('hidden');await loadDynamic();}catch(e){toast(e.message||String(e));}
    };
    $('playerResetTutorial').onclick=async()=>{try{const r=await apiPost('player/action',{user_id:user,action:'tutorial_reset'});toast(r.message||'教程已重开');set('pf_tutorial_status','pending');set('pf_tutorial_step',1);await loadDynamic();}catch(e){toast(e.message||String(e));}};
  }catch(e){toast(e.message||String(e));}
}
function closePlayerEditor(){$('playerModal')?.classList.add('hidden');}
function openImport(){ $('importModal')?.classList.remove('hidden'); $('importHint').textContent=''; }
function closeImport(){ $('importModal')?.classList.add('hidden'); }
async function importData(){
  let text=$('importText')?.value.trim()||'';
  const file=$('importFile')?.files?.[0];
  if(!text && file){ text=await file.text(); }
  if(!text){toast('请选择 JSON 文件或粘贴 JSON 数据');return;}
  const hint=$('importHint'); if(hint)hint.textContent='正在验证并导入，请勿重复点击。';
  try{
    const snapshot=JSON.parse(text);
    const r=await apiPost('data/import',{snapshot});
    toast(r.message||'数据导入成功');
    if(hint)hint.textContent=`完成。自动备份：${r.backup||'已创建'}`;
    $('importModal')?.classList.add('hidden');
    await loadCore();
  }catch(e){ if(hint)hint.textContent=e.message||String(e); toast(e.message||String(e)); }
}
function attach(){
  const bind=(id,event,fn)=>{const el=$(id);if(el)el[event]=fn;};
  bind('playerModalClose','onclick',closePlayerEditor); if($('playerModal'))$('playerModal').onclick=e=>{if(e.target.id==='playerModal')closePlayerEditor();};
  bind('groupModalClose','onclick',closeGroupEditor); if($('groupModal'))$('groupModal').onclick=e=>{if(e.target.id==='groupModal')closeGroupEditor();};
  bind('importModalClose','onclick',closeImport); bind('importCancel','onclick',closeImport); if($('importModal'))$('importModal').onclick=e=>{if(e.target.id==='importModal')closeImport();};
  bind('overviewRefresh','onclick',()=>loadCore());
  bind('refreshBtn','onclick',()=>loadCore());
  bind('exportBtn','onclick',exportData); bind('importBtn','onclick',openImport);
  bind('importRun','onclick',importData);
  bind('loginBtn','onclick',login);
  bind('loginPassword','onkeydown',e=>{if(e.key==='Enter')login();});
  bind('reloadSettings','onclick',async()=>{try{const d=await apiGet('settings');state.settings=d.config;state.schema=d.schema;render();toast('配置已重新读取');}catch(e){toast(e.message||String(e));}});
  bind('saveSettings','onclick',saveSettings);
  bind('searchPlayers','onclick',()=>{state.search=$('playerSearch')?.value.trim()||'';loadDynamic();});
  bind('playerSearch','onkeydown',e=>{if(e.key==='Enter'){state.search=e.target.value.trim();loadDynamic();}});
  bind('searchGroups','onclick',()=>{state.groupSearch=$('groupSearch')?.value.trim()||'';render();});
  bind('groupSearch','onkeydown',e=>{if(e.key==='Enter'){state.groupSearch=e.target.value.trim();render();}});
  bind('eventsRefresh','onclick',()=>loadDynamic());
  document.querySelectorAll('[data-group-editor]').forEach(b=>{b.onclick=()=>openGroupEditor(b.dataset.groupEditor);});
  document.querySelectorAll('[data-group-action]').forEach(b=>{b.onclick=async()=>{if(b.dataset.busy==='1')return;b.dataset.busy='1';try{const r=await apiPost('group/action',{group_id:b.dataset.group,action:b.dataset.groupAction});toast(r.message||'操作成功');await loadCore();}catch(e){toast(e.message||String(e));}finally{b.dataset.busy='0';}};});
  bind('groupSave','onclick',saveGroupSettings);
  bind('groupSpawnNpc','onclick',async()=>{if($('groupSpawnNpc').dataset.busy==='1')return;$('groupSpawnNpc').dataset.busy='1';try{const gid=$('groupModal')?.dataset.group||'';const r=await apiPost('group/action',{group_id:gid,action:'spawn_npc'});toast(r.message||'NPC 已召唤');if(!r.duplicate)await loadCore();openGroupEditor(gid);}catch(e){toast(e.message||String(e));}finally{$('groupSpawnNpc').dataset.busy='0';}});
  bind('groupEndEvent','onclick',async()=>{if($('groupEndEvent').dataset.busy==='1')return;$('groupEndEvent').dataset.busy='1';try{const gid=$('groupModal')?.dataset.group||'';const r=await apiPost('group/action',{group_id:gid,action:'end_event'});toast(r.message||'事件已结束');await loadCore();openGroupEditor(gid);}catch(e){toast(e.message||String(e));}finally{$('groupEndEvent').dataset.busy='0';}});
  bind('groupClearNpc','onclick',async()=>{if($('groupClearNpc').dataset.busy==='1')return;$('groupClearNpc').dataset.busy='1';try{const gid=$('groupModal')?.dataset.group||'';const r=await apiPost('group/action',{group_id:gid,action:'clear_npc'});toast(r.message||'NPC 已离开');await loadCore();openGroupEditor(gid);}catch(e){toast(e.message||String(e));}finally{$('groupClearNpc').dataset.busy='0';}});
  document.querySelectorAll('[data-player-action]').forEach(b=>{b.onclick=async()=>{const action=b.dataset.playerAction;const user=b.dataset.user;let amount=0;if(['grant_coins','take_coins','grant_gems'].includes(action)){amount=Number.parseInt(prompt('请输入数值：','1000')||'0',10);if(!Number.isFinite(amount)||amount<=0)return;}if(action==='edit'||action==='set_level'){openPlayerEditor(user);return;}if(action==='reset'&&!confirm('确认重置该玩家？这是全局角色重置，会清空跨群共享的背包、宠物、装备、成就和技能。'))return;if(b.dataset.busy==='1')return;b.dataset.busy='1';try{const r=await apiPost('player/action',{user_id:user,action,amount});toast(r.message||'玩家操作成功');await loadDynamic();}catch(e){toast(e.message||String(e));}finally{b.dataset.busy='0';}};});
  if(['players','economy','events','logs','tasks','tutorial'].includes(state.tab))loadDynamic();
}
async function saveSettings(){const changes={};document.querySelectorAll('[data-setting]').forEach(el=>{const key=el.dataset.setting,sp=state.schema[key]||{};if(sp.type==='bool')changes[key]=el.checked;else if(sp.type==='int')changes[key]=Number.parseInt(el.value||'0',10);else if(sp.type==='float')changes[key]=Number.parseFloat(el.value||'0');else if(sp.type==='text')changes[key]=el.value;else if(sp.secret&&el.value===''){}else changes[key]=el.value;});try{const r=await apiPost('settings/save',{changes});state.settings=r.config||state.settings;toast('全部配置保存成功');render();}catch(e){toast(e.message||String(e));}}
async function exportData(){try{const r=await bridge.download('data/export',gparams({group_id:state.groupId||''}),'group-world-export.json');toast(`导出已生成：${r.filename||'group-world-export.json'}`);}catch(e){toast(e.message||String(e));}}
boot().catch(e=>showLogin(e.message||String(e)));

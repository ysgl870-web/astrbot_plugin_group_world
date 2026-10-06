const bridge = window.AstrBotPluginPage;
const state = { tab: 'overview', token: null, groups: [], groupId: null, settings: null, schema: null, players: [], search: '', groupSearch: '', summary: null, dynamicSeq: 0, _system: null, _broadcastCampaigns: [], _cloudPackages: [], _cloudSelected: [] , _cloudProductCatalog: [], _cloudAnnouncements: [], _cloudSite: {}, _cloudPackageSearch: '', _cloudPackageAuthor: '', _cloudPackageCategory: '', _cloudOnlySelected: false, _cloudProductSearch: '', _menu: {} };
const NAV = [
  ['menu','帮助菜单','🖼️'],['overview','概览','▦'],['groups','群组管理','⌂'],['players','玩家中心','♙'],['economy','经济流水','￥'],['events','世界事件','✦'],
  ['system','系统运维','⚡'],['settings','高级配置','⚙'],['logs','审计日志','◌'],['tasks','任务系统','✓'],['tutorial','新手教程','?'],['cloud','云端玩法','☁']
];
const TITLES = {overview:['概览','世界状态、活跃度、经济与系统健康度。'],groups:['群组管理','逐群控制世界开关、Boss、事件与运行状态。'],players:['玩家中心','查看更完整的成长、活跃、财富与教程状态。'],economy:['经济流水','追踪金币、钻石的每一笔流入和流出。'],events:['世界事件','查看历史事件、天气和世界变化记录。'],settings:['高级配置','细粒度控制经济、玩法、权限、教程与群级覆盖。'],logs:['审计日志','管理员操作与玩家行为审计，便于定位异常。'],tasks:['任务系统','查看今日每日任务的真实进度、完成状态和奖励。'],tutorial:['新手教程','查看玩家教程状态与真实完成情况。'],system:['系统运维','服务器资源、插件内存、缓存清理与逐群群发。'],menu:['帮助菜单','配置 /帮助 菜单图片与文字的发送方式、顺序和失败兜底。'],cloud:['云端玩法','连接 ysgl.bot.cd/astrbot，额外获取 Boss、商品、教程等自定义数据。']};
const $ = (id)=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function num(v){return Number(v||0).toLocaleString('zh-CN');}
function toast(msg){const el=$('toast');el.textContent=msg;el.classList.add('show');clearTimeout(window.__toast);window.__toast=setTimeout(()=>el.classList.remove('show'),2600);}
function setTheme(ctx){document.documentElement.dataset.theme=ctx?.isDark?'dark':'light';}
async function boot(){
  const ctx=await bridge.ready(); setTheme(ctx); bridge.onContext(setTheme);
  renderNav();
  const b=await bridge.apiGet('bootstrap');
  $('versionText').textContent=`群聊世界 V${b.version||'1.11.29'} · 作者 ysgl`;
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
  const globalOnly=['players','economy','settings','tutorial','system','menu'];
  if(globalOnly.includes(state.tab)){sel.style.display='none';return;}
  sel.style.display='block';
  sel.innerHTML=`<option value="">全部群组</option>`+state.groups.map(g=>`<option value="${esc(g.group_id)}" ${String(g.group_id)===String(state.groupId)?'selected':''}>${esc(g.group_id)} · ${g.player_count||0}位玩家</option>`).join('');
  sel.value=state.groupId===null?'':String(state.groupId);
  sel.onchange=()=>{state.groupId=sel.value;render();};
}
function render(){const [title,desc]=TITLES[state.tab];$('pageTitle').textContent=title;$('pageDesc').textContent=desc;renderGroupSelect();$('content').innerHTML=renderTab();attach();if(state.tab==='cloud')loadCloud();if(state.tab==='system')loadSystem();if(state.tab==='menu')loadMenu();}
function renderTab(){switch(state.tab){case'overview':return renderOverview();case'groups':return renderGroups();case'players':return `<div id="playersRoot">加载玩家中…</div>`;case'economy':return `<div id="economyRoot">加载流水中…</div>`;case'events':return `<div id="eventsRoot">加载事件中…</div>`;case'settings':return renderSettings();case'logs':return `<div id="logsRoot">加载日志中…</div>`;case'tasks':return `<div id="tasksRoot">加载任务中…</div>`;case'tutorial':return `<div id="tutorialRoot">加载教程数据中…</div>`;case'system':return renderSystem();case'menu':return '<div id="menuRoot">加载帮助菜单设置中…</div>';case'cloud':return renderCloud();default:return '';}}
function renderOverview(){const summary=state.summary||{};const total=Number(summary.players||0),coins=Number(summary.coins||0),msgs=Number(summary.messages||0),boss=state.groups.filter(g=>g.boss_active).length;const active=state.groups.reduce((a,g)=>a+Number(g.today_active_users||0),0);const avg=Number(summary.avg_level||0);const max=Math.max(1,...state.groups.map(g=>Number(g.player_count||0)));
 return `<div class="grid"><div class="metric"><div class="label">群组</div><div class="value">${state.groups.length}</div><div class="sub">已被插件记录的群</div></div><div class="metric"><div class="label">玩家</div><div class="value">${num(total)}</div><div class="sub">跨群角色总数</div></div><div class="metric"><div class="label">金币流通</div><div class="value">${num(coins)}</div><div class="sub">当前玩家余额合计</div></div><div class="metric"><div class="label">世界 Boss</div><div class="value">${boss}</div><div class="sub">当前活跃 Boss 数</div></div></div>
 <div class="split"><section class="card"><div class="card-head"><div><div class="card-title">世界运行概览</div><div class="section-desc">今日活跃、消息、平均等级与各群规模。</div></div><button class="ghost small" id="overviewRefresh">刷新</button></div><div class="stat-stack"><div class="mini"><div class="k">今日活跃用户</div><div class="v">${num(active)}</div></div><div class="mini"><div class="k">累计消息</div><div class="v">${num(msgs)}</div></div><div class="mini"><div class="k">平均等级</div><div class="v">${avg.toFixed(2)}</div></div><div class="mini"><div class="k">累计探索</div><div class="v">${num(state.groups.reduce((a,g)=>a+(g.total_explores||0),0))}</div></div></div><div class="bars" style="margin-top:18px">${state.groups.slice(0,10).map(g=>`<div class="bar-row"><span>${esc(g.group_id)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,(Number(g.player_count||0)/max)*100)}%"></div></div><b>${g.player_count||0} 人</b></div>`).join('')||'<div class="empty">暂无群数据</div>'}</div></section>
 <section class="card"><div class="card-head"><div><div class="card-title">当前群状态</div><div class="section-desc">${state.groupId?`群 ${esc(state.groupId)}`:'选择一个群查看详细状态'}</div></div></div>${renderSelectedGroup()}</section></div>
 <section class="card"><div class="card-head"><div><div class="card-title">数据备份与恢复</div><div class="section-desc">插件本地数据的完整导入/导出。云端相关数据请在“云端玩法”分区处理。</div></div></div><div class="actions"><button class="ghost" id="importBtn">导入插件数据</button><button class="primary" id="exportBtn">导出插件数据</button></div></section>`;}
function renderSelectedGroup(){const g=state.groups.find(x=>x.group_id===state.groupId)||state.groups[0];if(!g)return '<div class="empty">还没有记录到群消息。</div>';return `<div class="kv"><div>状态</div><div><span class="pill ${g.enabled?'good':'danger'}">${g.enabled?'已启用':'已关闭'}</span></div><div>天气</div><div>${esc(g.world_weather)}</div><div>地点</div><div>${esc(g.world_location)}</div><div>玩家</div><div>${num(g.player_count)}</div><div>今日消息</div><div>${num(g.today_messages)}</div><div>今日活跃</div><div>${num(g.today_active_users)}</div><div>金币流通</div><div>${num(g.coin_supply)}</div><div>钻石流通</div><div>${num(g.gem_supply)}</div><div>Boss</div><div>${g.boss_active?`${esc(g.boss_name)} · ${num(g.boss_hp)}/${num(g.boss_max_hp)}`:'暂无'}</div></div>`;}
function renderGroups(){
  const q=String(state.groupSearch||'').trim().toLowerCase();
  const rows=state.groups.filter(g=>{
    if(!q)return true;
    return String(g.group_id||'').toLowerCase().includes(q) || String(g.session_origin||'').toLowerCase().includes(q);
  });
  return `<section class="card"><div class="card-head"><div><div class="card-title">群组控制台</div><div class="section-desc">可搜索群聊、调整世界规则、召唤 NPC、结束事件与管理 Boss；所有后台操作都会记录审计日志。</div></div><div class="toolbar"><input id="groupSearch" value="${esc(state.groupSearch)}" placeholder="搜索群聊 / 群号 / 会话来源"/><button class="ghost" id="searchGroups">搜索群聊</button></div></div><div class="notice" style="margin-bottom:12px"><strong>群聊：${rows.length}</strong>　当前展示 ${rows.length} / ${state.groups.length}，搜索只在已记录群聊中进行。</div><div class="table-wrap"><table class="table"><thead><tr><th>群 ID</th><th>状态</th><th>玩家</th><th>今日消息</th><th>今日活跃</th><th>天气 / 地点</th><th>事件 / NPC</th><th>Boss</th><th>操作</th></tr></thead><tbody>${rows.map(g=>`<tr><td class="code">${esc(g.group_id)}</td><td><span class="pill ${g.enabled?'good':'danger'}">${g.enabled?'启用':'关闭'}</span><br><span class="muted">事件 ${g.world_event_enabled?'开':'关'} · 怪物 ${g.monster_enabled?'开':'关'}</span></td><td>${g.player_count||0}</td><td>${num(g.today_messages)}</td><td>${num(g.today_active_users)}</td><td>${esc(g.world_weather)} · ${esc(g.world_location)}</td><td>${g.current_event_key?`✦ ${esc(g.current_event_key)}`:'—'}${g.current_npc_name?`<br>🧑‍🌾 ${esc(g.current_npc_name)}`:''}</td><td>${g.boss_active?`${esc(g.boss_name)}<br>${num(g.boss_hp)}/${num(g.boss_max_hp)}`:'—'}</td><td><div class="actions"><button class="small" data-group-action="toggle" data-group="${esc(g.group_id)}">${g.enabled?'关闭':'开启'}</button><button class="small" data-group-action="event" data-group="${esc(g.group_id)}">触发事件</button><button class="small" data-group-action="boss_start" data-group="${esc(g.group_id)}">开 Boss</button><button class="small danger" data-group-action="boss_end" data-group="${esc(g.group_id)}">结束 Boss</button><button class="small" data-group-editor="${esc(g.group_id)}">世界设置</button></div></td></tr>`).join('')||'<tr><td colspan="9"><div class="empty">没有匹配的群聊</div></td></tr>'}</tbody></table></div></section>`;
}
function renderSystem(){
  const x=state._system||{};
  const m=x.metrics||{};
  const sv=m.server||{},pr=m.process||{},pl=m.plugin||{},cfg=x.config||{};
  const systemGroups=Array.isArray(x.broadcasts)&&x.broadcasts.length?x.broadcasts:(Array.isArray(x.groups)&&x.groups.length?x.groups:[]);
  const groups=systemGroups.length?systemGroups:state.groups.map(g=>({group_id:g.group_id,session_origin:g.session_origin||'',enabled:!!g.enabled,world_enabled:!!g.enabled,player_count:Number(g.player_count||0),total_sent:0}));
  const broadcastState=state._broadcastCampaigns||{campaigns:[],running:[]};
  const campaigns=Array.isArray(broadcastState.campaigns)?broadcastState.campaigns:[];
  const fmt=v=>{const n=Number(v||0);if(!n)return '0 B';const u=['B','KB','MB','GB','TB'];let i=0,z=n;while(z>=1024&&i<u.length-1){z/=1024;i++;}return `${z.toFixed(i?1:0)} ${u[i]}`};
  const dt=v=>v?new Date(Number(v)*1000).toLocaleString('zh-CN',{hour12:false}):'—';
  const escText=s=>esc(String(s??''));
  const enabledCount=groups.filter(r=>r.enabled).length;
  const selected=groups;
  return `<div class="system-hero"><div><div class="eyebrow">SYSTEM OPERATIONS</div><h2>资源监控与群发中心</h2><p class="muted">服务器资源、缓存维护，以及独立的“立即群发 / 循环群发”配置中心。</p></div><button class="ghost" id="systemRefresh">刷新状态</button></div>
  <div class="system-grid"><div class="system-metric"><span>服务器内存</span><b>${fmt(sv.memory_used)} / ${fmt(sv.memory_total)}</b><small>${Number(sv.memory_percent||0).toFixed(1)}% 已使用</small></div><div class="system-metric"><span>CPU</span><b>${Number(sv.cpu_percent||0).toFixed(1)}%</b><small>系统实时占用</small></div><div class="system-metric"><span>插件进程 RSS</span><b>${fmt(pr.rss)}</b><small>PID ${esc(pr.pid||'—')}</small></div><div class="system-metric"><span>插件追踪内存</span><b>${fmt(pl.tracemalloc_current)}</b><small>峰值 ${fmt(pl.tracemalloc_peak)}</small></div><div class="system-metric"><span>插件数据目录</span><b>${fmt(pl.data_size)}</b><small>数据库、配置等</small></div><div class="system-metric"><span>缓存目录</span><b>${fmt(pl.cache_size)}</b><small>可自动清理</small></div></div>
  <section class="card"><div class="card-head"><div><div class="card-title">内存与缓存管理</div><div class="section-desc">自动清理只处理可重建缓存，不会删除 SQLite 主数据库。</div></div><button class="danger" id="systemCleanup">立即清理</button></div><div class="form-grid"><label>自动清理<input id="sys_auto_cleanup" type="checkbox" ${cfg.maintenance_auto_cleanup?'checked':''}></label><label>清理时间（服务器本地）<input id="sys_cleanup_time" type="time" value="${esc(cfg.maintenance_cleanup_time||'04:30')}"></label><label>缓存保留天数<input id="sys_retention" type="number" min="0" max="365" value="${Number(cfg.maintenance_cache_retention_days||7)}"></label></div><div class="notice">插件内存展示分为<strong>进程 RSS</strong>与<strong>tracemalloc 追踪分配</strong>：后者是 Python 分配跟踪值，不代表整个进程占用。</div></section>

  <section class="card"><div class="card-head"><div><div class="card-title">📢 独立群发中心</div><div class="section-desc">先勾选目标群，再选择发送模式。立即群发按“群间隔”逐群发送；循环群发会持续保存任务并在每轮之间等待设定的循环间隔。</div></div><span class="pill good">已记录 ${groups.length} 群</span></div>
    <div class="form-grid">
      <label style="grid-column:1/-1">消息内容<textarea id="campaignMessage" rows="5" maxlength="3000" placeholder="输入要发送到选中群的内容…">${escText(cfg.group_broadcast_default_message||'')}</textarea></label>
      <label>发送模式<select id="campaignMode"><option value="immediate">立即发送（一次）</option><option value="loop">循环发送（持续）</option></select></label>
      <label>群聊间隔（秒）<input id="campaignGroupGap" type="number" min="1" max="86400" value="1"><div class="hint">每发送到一个群后，至少等待 1 秒再发送下一个群。</div></label>
      <label id="campaignLoopBox" style="display:none">循环间隔（分钟）<input id="campaignLoopInterval" type="number" min="1" max="525600" value="1"><div class="hint">每轮全部群发送完成后，再等待此时间开始下一轮，最低 1 分钟。</div></label>
      <label id="campaignNameBox" style="display:none">任务名称<input id="campaignName" maxlength="80" value="循环群发"><div class="hint">方便后台同时管理多个循环群发任务。</div></label>
    </div>
    <div class="toolbar" style="margin-top:12px"><button class="ghost" id="campaignSelectAll">全选</button><button class="ghost" id="campaignSelectNone">全不选</button><button class="ghost" id="campaignSelectEnabled">选择已启用群</button><span class="notice" style="margin:0;flex:1">已选 <strong id="campaignSelectedCount">0</strong> / ${groups.length} 群</span><button class="primary" id="campaignExecute">立即发送</button><button class="primary" id="campaignStartLoop" style="display:none">开始循环发送</button></div>
    <div class="table-wrap" style="margin-top:14px"><table class="table broadcast-table"><thead><tr><th style="width:42px">选择</th><th>群 ID</th><th>群世界</th><th>已有逐群计划</th></tr></thead><tbody>${selected.map(r=>`<tr><td><input type="checkbox" class="campaign-select" data-campaign-group="${esc(r.group_id)}"></td><td class="code">${esc(r.group_id)}<br><span class="muted">${num(r.player_count||0)} 玩家</span></td><td><span class="pill ${r.world_enabled?'good':'danger'}">${r.world_enabled?'运行':'关闭'}</span></td><td>${r.enabled?'✅ 已启用':'⏸ 未启用'} · ${num(r.total_sent||0)} 次</td></tr>`).join('')||'<tr><td colspan="4"><div class="empty">暂无已记录群聊。至少让目标群发送一次消息后才能加入群发目标。</div></td></tr>'}</tbody></table></div>
  </section>

  <section class="card"><div class="card-head"><div><div class="card-title">🔁 循环群发任务</div><div class="section-desc">循环任务持久化在插件数据库，AstrBot 重启后仍会继续。停止后不会继续发送。</div></div></div>
    <div class="stack" style="margin-top:12px">${campaigns.map(c=>{const targets=Array.isArray(c.targets)?c.targets:[];const targetIds=targets.map(t=>t.group_id).join('、');const running=!!broadcastState.running?.includes?.(c.id);return `<div class="list-item"><div class="list-head"><div><b>${esc(c.name||('循环群发 #'+c.id))}</b> <span class="pill ${c.enabled?'good':'muted'}">${c.enabled?(running?'运行中':'已启用'):'已停止'}</span><div class="source">任务 #${c.id} · ${targets.length} 群 · 群间隔 ${c.group_interval_seconds||1} 秒 · 循环间隔 ${c.loop_interval_minutes||1} 分钟</div><div class="sub" style="margin-top:4px">目标：${escText(targetIds||'—')}</div><div class="sub" style="margin-top:4px">${escText(c.message||'')}<br>累计发送 ${num(c.total_sent||0)}，失败 ${num(c.total_failed||0)}，完成 ${num(c.total_cycles||0)} 轮${c.next_run_at?` · 下轮 ${dt(c.next_run_at)}`:''}</div></div><div class="actions"><button class="small ${c.enabled?'danger':'primary'}" data-campaign-toggle="${c.id}">${c.enabled?'停止':'启动'}</button><button class="small danger" data-campaign-delete="${c.id}">删除</button></div></div></div>`}).join('')||'<div class="empty">暂无循环群发任务。选择群后切换到“循环发送”即可创建。</div>'}</div>
  </section>

  <section class="card"><div class="card-head"><div><div class="card-title">兼容：逐群发送计划</div><div class="section-desc">保留原 v1.11.2 的逐群定时模式，不受新群发任务影响。</div></div><div class="actions"><button class="ghost" id="broadcastSaveDefaults">保存默认参数</button></div></div>
    <div class="form-grid"><label style="grid-column:1/-1">默认消息<textarea id="broadcastComposerMessage" rows="3" maxlength="3000">${esc(cfg.group_broadcast_default_message||x.default_message||'')}</textarea></label><label>默认间隔（分钟）<input id="broadcastComposerInterval" type="number" min="1" max="10080" value="${Number(cfg.group_broadcast_default_interval_minutes||x.default_interval_minutes||120)}"></label></div>
    <div class="toolbar" style="margin-top:12px"><button class="ghost" id="broadcastSelectAll">全选</button><button class="ghost" id="broadcastSelectNone">全不选</button><button class="ghost" id="broadcastSelectEnabled">选择已启用</button><span class="notice" style="margin:0;flex:1">已选 <strong id="broadcastSelectedCount">0</strong> / ${groups.length} 群</span><button class="primary" id="broadcastBatchSave">保存到选中群</button><button class="primary" id="broadcastBatchSend">立即发送</button></div>
    <div class="table-wrap" style="margin-top:12px"><table class="table broadcast-table"><thead><tr><th style="width:40px">选择</th><th>群 ID</th><th>开启</th><th>间隔（分）</th><th>消息</th><th>累计</th><th>操作</th></tr></thead><tbody>${groups.map(r=>`<tr data-broadcast-row="${esc(r.group_id)}"><td><input type="checkbox" class="br-select" data-group-select="${esc(r.group_id)}"></td><td class="code">${esc(r.group_id)}</td><td><input type="checkbox" class="br-enabled" ${r.enabled?'checked':''}></td><td><input class="br-interval" type="number" min="1" max="10080" value="${Number(r.interval_minutes||120)}"></td><td><textarea class="br-message" rows="2" maxlength="3000">${esc(r.message||'')}</textarea></td><td>${num(r.total_sent)}</td><td><div class="actions"><button class="small primary br-save">保存</button><button class="small ghost br-send">立即发送</button></div></td></tr>`).join('')||'<tr><td colspan="7"><div class="empty">暂无记录群。</div></td></tr>'}</tbody></table></div>
  </section></div>`;
}
function renderMenuSettings(){
  const m=state._menu||{};
  return `<section class="card"><div class="card-head"><div><div class="card-title">🖼️ 菜单设置</div><div class="section-desc">独立配置 /帮助、/菜单、/世界菜单等命令。图片发送失败会自动发送一次文字帮助兜底。</div></div><span id="menuImageState" class="pill">读取中…</span></div>
    <div class="menu-image-config" style="display:grid;grid-template-columns:minmax(260px,1.2fr) minmax(280px,1fr);gap:18px;align-items:start;margin-top:16px">
      <div><img id="menuImagePreview" alt="群聊世界帮助菜单预览" style="display:block;width:100%;max-height:460px;object-fit:contain;border-radius:16px;background:#eef2ff;border:1px solid rgba(99,102,241,.18)"/><div class="hint" style="margin-top:8px">建议使用横向图片，单张不超过 5 MB。默认菜单图片已经内置。</div></div>
      <div class="stack">
        <div class="card-title" style="margin-bottom:4px">发送内容</div>
        <label style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="menuImageEnabled" checked> ✅ 发送菜单图片</label>
        <label style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="menuTextEnabled" checked> ✅ 发送文字帮助</label>

        <div class="card-title" style="margin-top:10px;margin-bottom:4px">发送顺序</div>
        <label style="display:flex;align-items:center;gap:8px"><input type="radio" name="menuSendOrder" id="menuImageFirstYes" value="image" checked> 🖼️ 先发图片，再发文字</label>
        <label style="display:flex;align-items:center;gap:8px"><input type="radio" name="menuSendOrder" id="menuImageFirstNo" value="text"> 📝 先发文字，再发图片</label>

        <div class="notice"><strong>失败兜底</strong><br>开启“发送文字帮助”时，图片实际发送失败会自动使用文字帮助兜底；关闭“发送文字帮助”后，插件不会发送任何文字。图片发送成功且开启文字时，不会重复发送文字。</div>
        <div class="notice"><strong>当前策略</strong><br>图片：${m.send_image?'开启':'关闭'} · 文字：${m.send_text?'开启':'关闭'} · 顺序：${m.first?'图片优先':'文字优先'}<br><span class="muted">此处保存后会同步到 AstrBot 原生“帮助菜单设置”分区。</span></div>
        <label>上传自定义菜单图片<input id="menuImageFile" type="file" accept="image/jpeg,image/png,image/webp"/></label>
        <div class="actions"><button class="primary" id="menuImageUpload">上传并启用</button><button class="ghost" id="menuImageSaveOptions">保存设置</button><button class="ghost" id="menuImageReset">恢复默认</button></div>
        <div class="notice">自定义图片只保存在插件本机数据目录，不会上传到 YSGL 云端。</div>
      </div>
    </div>
  </section>`;
}
async function loadMenu(){
  try{
    state._menu=await apiGet('menu/status',{include_image:1});
    const root=$('menuRoot'); if(root) root.innerHTML=renderMenuSettings();
    bindMenuImageControls(); renderMenuImageState();
  }catch(e){const root=$('menuRoot');if(root)root.innerHTML=`<section class="card"><div class="empty">帮助菜单设置加载失败：${esc(e.message||String(e))}</div></section>`;}
}
async function loadSystem(){
  try{
    let s;
    try{s=await apiGet('system/overview')}catch(first){try{s=await apiGet('ops')}catch(second){s=await apiGet('system')}}
    state._system=s||{};
    state._broadcastCampaigns={campaigns:Array.isArray(s?.campaigns)?s.campaigns:[],running:Array.isArray(s?.running)?s.running:[]};
    try{state._menu=await apiGet('menu/status',{include_image:1});}catch(menuErr){state._menu={enabled:true,first:true,path_available:false,message:menuErr.message||String(menuErr)};}
    renderSystem();attach();bindCampaignControls();updateBroadcastSelection();updateCampaignSelection();bindMenuImageControls();renderMenuImageState();
  }catch(e){
    toast('系统运维数据加载失败：'+(e.message||String(e)));
  }
}
function renderMenuImageState(){
  const m=state._menu||{};
  if($('menuImageState')){$('menuImageState').textContent=m.enabled?(m.first?'图片优先':'文字优先'):'已关闭';$('menuImageState').className='pill '+(m.enabled?'good':'muted');}
  if($('menuImageEnabled'))$('menuImageEnabled').checked=!!m.enabled;
  if($('menuImageFirstYes'))$('menuImageFirstYes').checked=!!m.first;if($('menuImageFirstNo'))$('menuImageFirstNo').checked=!m.first;
  if($('menuTextEnabled'))$('menuTextEnabled').checked=!!m.send_text;
  if($('menuImagePreview')){const src=m.image_data_url||'';$('menuImagePreview').src=src;$('menuImagePreview').style.opacity=src?'1':'0.25';}
}
function bindMenuImageControls(){
  $('menuImageUpload')?.addEventListener('click',async()=>{const file=$('menuImageFile')?.files?.[0];if(!file){toast('请先选择图片。');return}if(file.size>5*1024*1024){toast('图片不能超过 5 MB。');return}try{const r=await bridge.upload('menu/upload',file);const reader=new FileReader();reader.onload=()=>{state._menu={...state._menu,...r,image_data_url:String(reader.result||'')};renderMenuImageState();};reader.readAsDataURL(file);toast(r.message||'菜单图片已更新')}catch(e){toast(e.message||String(e))}});
  $('menuImageSaveOptions')?.addEventListener('click',async()=>{try{const section={image_enabled:!!$('menuImageEnabled')?.checked,text_enabled:!!$('menuTextEnabled')?.checked,image_first:!!$('menuImageFirstYes')?.checked};const r=await apiPost('settings/save',{changes:{help_menu_settings:section,help_menu_image_enabled:section.image_enabled,help_menu_text_enabled:section.text_enabled,help_menu_image_first:section.image_first}});state.settings=r.config||state.settings;state._menu={...state._menu,send_image:section.image_enabled,enabled:section.image_enabled,send_text:section.text_enabled,first:section.image_first};renderMenuImageState();toast('帮助菜单设置已保存')}catch(e){toast(e.message||String(e))}});
  $('menuImageReset')?.addEventListener('click',async()=>{if(!confirm('恢复默认菜单图片并启用图片优先？'))return;try{const r=await apiPost('menu/reset',{});state._menu={...state._menu,...r};const d=await apiGet('menu/status',{include_image:1});state._menu={...state._menu,...d};renderMenuImageState();toast(r.message||'已恢复默认')}catch(e){toast(e.message||String(e))}});
  $('menuImageFile')?.addEventListener('change',()=>{const file=$('menuImageFile')?.files?.[0];if(!file||!$('menuImagePreview'))return;const url=URL.createObjectURL(file);$('menuImagePreview').src=url;$('menuImagePreview').style.opacity='1';});
}

async function saveSystemSettings(){const changes={maintenance_auto_cleanup:!!$('sys_auto_cleanup')?.checked,maintenance_cleanup_time:String($('sys_cleanup_time')?.value||'04:30'),maintenance_cache_retention_days:Number.parseInt($('sys_retention')?.value||'7',10),group_broadcast_default_message:String($('broadcastComposerMessage')?.value||''),group_broadcast_default_interval_minutes:Number.parseInt($('broadcastComposerInterval')?.value||'120',10)};try{const r=await apiPost('settings/save',{changes});state.settings=r.config||state.settings;toast('系统默认参数已保存');await loadSystem();}catch(e){toast(e.message||String(e));}}
async function cleanupSystem(){if(!confirm('确认立即清理缓存？SQLite 主数据库不会被删除。'))return;try{const r=await apiPost('system/cleanup',{retention_days:Number.parseInt($('sys_retention')?.value||'7',10)});toast(`清理完成：${num(r.removed||0)} 个文件，释放 ${r.bytes||0} B`);await loadSystem();}catch(e){toast(e.message||String(e));}}
function selectedCampaignIds(){return Array.from(document.querySelectorAll('.campaign-select:checked')).map(x=>String(x.dataset.campaignGroup||'')).filter(Boolean)}
function updateCampaignSelection(){const n=selectedCampaignIds().length;if($('campaignSelectedCount'))$('campaignSelectedCount').textContent=String(n)}
function setCampaignSelection(mode){document.querySelectorAll('.campaign-select').forEach(x=>{x.checked=mode==='all' || (mode==='enabled' && !!x.closest('tr')?.querySelector('.pill.good'))});updateCampaignSelection()}
function bindCampaignControls(){const mode=$('campaignMode');if(mode){const update=()=>{const loop=mode.value==='loop';if($('campaignLoopBox'))$('campaignLoopBox').style.display=loop?'block':'none';if($('campaignNameBox'))$('campaignNameBox').style.display=loop?'block':'none';if($('campaignExecute'))$('campaignExecute').style.display=loop?'none':'inline-flex';if($('campaignStartLoop'))$('campaignStartLoop').style.display=loop?'inline-flex':'none'};mode.onchange=update;update()}
  $('campaignSelectAll')?.addEventListener('click',()=>setCampaignSelection('all'));$('campaignSelectNone')?.addEventListener('click',()=>setCampaignSelection('none'));$('campaignSelectEnabled')?.addEventListener('click',()=>setCampaignSelection('enabled'));document.querySelectorAll('.campaign-select').forEach(x=>x.addEventListener('change',updateCampaignSelection));
  $('campaignExecute')?.addEventListener('click',executeCampaignNow);$('campaignStartLoop')?.addEventListener('click',saveLoopCampaign);
  document.querySelectorAll('[data-campaign-toggle]').forEach(b=>b.onclick=async()=>{try{const id=Number(b.dataset.campaignToggle);const c=state._broadcastCampaigns.campaigns.find(x=>Number(x.id)===id);const r=await apiPost('broadcast/campaign/toggle',{campaign_id:id,enabled:!c?.enabled});toast(r.message||'操作成功');await loadSystem()}catch(e){toast(e.message||String(e))}});
  document.querySelectorAll('[data-campaign-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('确定删除这个循环群发任务？'))return;try{const r=await apiPost('broadcast/campaign/delete',{campaign_id:Number(b.dataset.campaignDelete)});toast(r.message||'已删除');await loadSystem()}catch(e){toast(e.message||String(e))}});
}
async function executeCampaignNow(){const group_ids=selectedCampaignIds();if(!group_ids.length){toast('请先选择要群发的群。');return}const message=String($('campaignMessage')?.value||'').trim();if(!message){toast('群发消息不能为空。');return}const group_interval_seconds=Math.max(1,Number.parseInt($('campaignGroupGap')?.value||'1',10));if(!confirm(`确定立即向 ${group_ids.length} 个群发送？群间隔 ${group_interval_seconds} 秒。`))return;try{const r=await apiPost('broadcast/batch_send_now',{group_ids,message,group_interval_seconds});toast(r.message||'立即群发已启动');}catch(e){toast(e.message||String(e))}}
async function saveLoopCampaign(){const group_ids=selectedCampaignIds();if(!group_ids.length){toast('请先选择循环发送的群。');return}const message=String($('campaignMessage')?.value||'').trim();if(!message){toast('群发消息不能为空。');return}const group_interval_seconds=Math.max(1,Number.parseInt($('campaignGroupGap')?.value||'1',10));const loop_interval_minutes=Math.max(1,Number.parseInt($('campaignLoopInterval')?.value||'1',10));const name=String($('campaignName')?.value||'循环群发').trim()||'循环群发';try{const r=await apiPost('broadcast/campaign/save',{name,message,group_ids,group_interval_seconds,loop_interval_minutes,enabled:true});toast(r.message||'循环群发已启动');await loadSystem()}catch(e){toast(e.message||String(e))}}
function selectedBroadcastIds(){return Array.from(document.querySelectorAll('.br-select:checked')).map(x=>String(x.dataset.groupSelect||'')).filter(Boolean)}
function updateBroadcastSelection(){const n=selectedBroadcastIds().length;if($('broadcastSelectedCount'))$('broadcastSelectedCount').textContent=String(n)}
function setBroadcastSelection(mode){document.querySelectorAll('.br-select').forEach(x=>{x.checked=mode==='all' || (mode==='enabled' && !!x.closest('tr')?.querySelector('.br-enabled')?.checked)});updateBroadcastSelection()}
async function saveBroadcastBatch(){const group_ids=selectedBroadcastIds();if(!group_ids.length){toast('请先选择要群发的群。');return}const message=String($('broadcastComposerMessage')?.value||'').trim();if(!message){toast('群发内容不能为空。');return}try{const r=await apiPost('broadcast/batch_save',{group_ids,message,interval_minutes:Number.parseInt($('broadcastComposerInterval')?.value||'120',10),enabled:true});toast(r.message||'已保存');await loadSystem();}catch(e){toast(e.message||String(e));}}
async function sendBroadcastBatch(){const group_ids=selectedBroadcastIds();if(!group_ids.length){toast('请先选择要群发的群。');return}const message=String($('broadcastComposerMessage')?.value||'').trim();if(!message){toast('群发内容不能为空。');return}const group_interval_seconds=1;if(!confirm(`确定立即向 ${group_ids.length} 个群发送？群间隔 ${group_interval_seconds} 秒。`))return;try{const r=await apiPost('broadcast/batch_send_now',{group_ids,message,group_interval_seconds});toast(r.message||'立即群发已启动');}catch(e){toast(e.message||String(e));}}
async function saveBroadcastRow(row){const gid=row.dataset.broadcastRow;try{const r=await apiPost('broadcast/save',{group_id:gid,enabled:!!row.querySelector('.br-enabled')?.checked,interval_minutes:Number.parseInt(row.querySelector('.br-interval')?.value||'120',10),message:String(row.querySelector('.br-message')?.value||'')});toast(r.message||'群发配置已保存');await loadSystem();}catch(e){toast(e.message||String(e));}}
async function sendBroadcastNow(row){const gid=row.dataset.broadcastRow;const msg=String(row.querySelector('.br-message')?.value||'').trim();if(!msg){toast('当前群消息为空');return}try{const r=await apiPost('broadcast/send_now',{group_id:gid,message:msg});toast(r.message||'已发送');await loadSystem();}catch(e){toast(e.message||String(e));}}

function renderSettings(){const groups=[['权限与访问',['owner_user_ids','admin_user_ids','session_admin_enabled','session_admin_allowed_actions','web_admin_usernames','web_admin_password']],['群与基础',['enabled','disabled_group_ids','group_overrides_json','default_new_player_coins','default_new_player_gems','max_stamina','stamina_regen_minutes','stamina_regen_amount','new_player_protection_hours']],['经济系统',['transfer_max_coins','profession_change_cost','profession_cooldown_days','checkin_min_coins','checkin_max_coins','checkin_streak_bonus_per_day','checkin_streak_bonus_cap','shop_enabled','shop_discount_percent','shop_catalog_json']],['探索 / 成长',['explore_enabled','explore_daily_limit','explore_stamina_cost','explore_deep_extra_cost','explore_danger_extra_cost','explore_reward_multiplier','explore_rare_bonus_percent','explore_danger_percent','monster_enabled','explore_monster_chance_percent','explore_monster_max_count','explore_monster_multi_chance_percent','monster_encounter_minutes','monster_reward_multiplier','monster_catalog_json','equipment_enabled','equipment_max_level','equipment_ore_cost','equipment_upgrade_base_rate','crafting_enabled','crafting_auto_enabled','crafting_max_batch','crafting_recipe_json','revive_equipment_hp_percent','skill_system_enabled','skill_slot_limit','skill_bond_enabled']],['NPC 系统',['npc_enabled','npc_chance_percent','npc_interval_minutes','npc_duration_minutes']],['AI 智能增强（总开关 + 子选项）',['ai_enabled','ai_npc_dialogue_enabled','ai_story_enabled','auto_battle_ai_enabled','ai_max_reply_chars','ai_npc_prompt']],['宠物 / 任务',['pet_enabled','pet_draw_cost','pet_draw_cooldown_seconds','pet_rarity_json','daily_tasks_enabled','daily_task_reward_multiplier','achievements_enabled']],['游戏 / 活动',['game_enabled','game_guess_reward','game_rps_win_reward','game_rps_draw_reward','game_bomb_safe_reward','fishing_enabled','fishing_stamina_cost','mining_enabled','mining_stamina_cost','work_enabled','work_cooldown_minutes']],['战斗 / 复活 / 决斗',['auto_battle_enabled','auto_battle_interval_seconds','auto_battle_max_turns','auto_battle_broadcast_every','auto_battle_ai_aggression','auto_battle_defense_threshold','auto_buy_revive_item','revive_item_cost','revive_hp_percent','respawn_countdown_seconds','respawn_hp_percent','duel_enabled','duel_queue_timeout_seconds','duel_match_rating_range','duel_turn_timeout_seconds','duel_rating_delta','duel_message_window_seconds','duel_ai_enhance','duel_ai_aggression']],['Boss / 世界',['boss_enabled','enable_auto_boss','boss_interval_hours','boss_duration_hours','boss_max_hp','boss_damage_base','boss_attack_cooldown_seconds','boss_top_reward','boss_reward_decay','boss_min_reward','boss_skill_chance_percent','boss_catalog_json','enable_auto_world_events','world_event_interval_minutes','world_event_chance','event_catalog_json']],['教程 / 数据',['tutorial_enabled','tutorial_auto_start','tutorial_allow_skip','tutorial_pages_json','proactive_tips_enabled','proactive_tip_interval_minutes','tip_catalog_json','data_retention_days','max_dashboard_rows']]];return `<section class="card"><div class="card-head"><div><div class="card-title">高级配置</div><div class="section-desc">优先使用本页管理复杂设置；AstrBot 原生配置页仍可作为底层配置入口。</div></div><div class="actions"><button class="ghost" id="reloadSettings">重新读取</button><button class="primary" id="saveSettings">保存全部修改</button></div></div><div class="notice"><strong>权限模型：</strong>世界超级管理员（owner_user_ids / Web 超管） ＞ 会话管理员 ＞ 玩家。默认会话管理员不允许执行高危经济、封禁和重置操作。</div>${groups.map(([title,keys])=>`<div class="form-section"><div class="card-title">${title}</div><div class="form-grid">${keys.filter(k=>state.schema[k] && !state.schema[k].invisible).map(renderField).join('')}</div></div>`).join('')}</section>`;}
function renderField(key){const sp=state.schema[key]||{}, val=state.settings[key]??sp.default??'';const desc=sp.description||key;const hint=sp.hint||'';let input='';if(sp.type==='bool'){input=`<label style="display:flex;align-items:center;gap:8px"><input data-setting="${esc(key)}" type="checkbox" ${val?'checked':''}/> <span>${esc(desc)}</span></label>`;return `<div class="field"><div>${input}</div><div class="hint">${esc(hint)}</div></div>`;}if(sp.type==='text'){input=`<textarea data-setting="${esc(key)}">${esc(val)}</textarea>`;}else if(sp.type==='int'||sp.type==='float'){input=`<input data-setting="${esc(key)}" type="number" value="${esc(val)}" ${sp.slider?`min="${sp.slider.min}" max="${sp.slider.max}" step="${sp.slider.step}"`:''}/>`;}else{input=`<input data-setting="${esc(key)}" type="${sp.secret?'password':'text'}" value="${esc(val==='********'?'':val)}" placeholder="${sp.secret?'留空表示不修改':desc}"/>`; }return `<div class="field"><label>${esc(desc)}</label>${input}<div class="hint">${esc(hint)}</div></div>`;}
function renderTasks(rows,summary,date){
  const statusLabel=r=>r.completed?'✅ 已完成':`⬜ ${r.progress||0}/${r.target||0}`;
  return `<section class="card"><div class="card-head"><div><div class="card-title">每日任务数据</div><div class="section-desc">日期：${esc(date||'')} · 这里读取的是玩家实际任务记录，不再只显示静态说明。</div></div><span class="pill ${summary.rows?'good':'muted'}">${num(summary.completed||0)} 项完成</span></div><div class="grid"><div class="metric"><div class="label">任务记录</div><div class="value">${num(summary.rows||rows.length)}</div></div><div class="metric"><div class="label">涉及玩家</div><div class="value">${num(summary.players||0)}</div></div><div class="metric"><div class="label">已完成</div><div class="value">${num(summary.completed||0)}</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>玩家</th><th>任务</th><th>进度</th><th>奖励</th><th>状态</th><th>群组记录</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.player_name||r.user_id)}</b><br><span class="code">${esc(r.user_id)}</span> · Lv.${r.player_level||1}</td><td>${esc(({checkin:'完成签到',explore:'完成探索',game:'完成小游戏'})[r.task_id]||r.task_id)}</td><td>${num(r.progress)}/${num(r.target)}</td><td>💰 ${num(r.reward_coins)} · ⭐ ${num(r.reward_exp)}</td><td>${statusLabel(r)}</td><td>${esc(r.member_groups||'—')}</td></tr>`).join('')||'<tr><td colspan="6"><div class="empty">暂无任务记录。玩家执行 /任务 后会产生数据。</div></td></tr>'}</tbody></table></div></section>`;
}
function renderTutorial(rows,counts){
  return `<section class="split"><div class="card"><div class="card-head"><div><div class="card-title">教程状态</div><div class="section-desc">展示玩家真实教程状态，不再只显示静态教程说明。</div></div><span class="pill good">完成 ${num(counts.completed||0)} 人</span></div><div class="grid"><div class="metric"><div class="label">进行中</div><div class="value">${num(counts.pending||0)}</div></div><div class="metric"><div class="label">已完成</div><div class="value">${num(counts.completed||0)}</div></div><div class="metric"><div class="label">已跳过</div><div class="value">${num(counts.skipped||0)}</div></div></div><div class="table-wrap"><table class="table"><thead><tr><th>玩家</th><th>教程状态</th><th>当前页</th><th>最近活跃</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.name||r.user_id)}</b><br><span class="code">${esc(r.user_id)}</span></td><td>${r.tutorial_status==='completed'?'✅ 已完成':(r.tutorial_status==='skipped'?'⏭️ 已跳过':'📖 进行中')}</td><td>${num(r.tutorial_step||0)}</td><td>${esc(r.last_seen_at||r.updated_at||'—')}</td></tr>`).join('')||'<tr><td colspan="4"><div class="empty">暂无教程数据。</div></td></tr>'}</tbody></table></div></div><div class="card"><div class="card-head"><div class="card-title">教程流程</div></div>${[1,2,3,4,5,6].map((n,i)=>`<div class="notice" style="margin-top:10px"><strong>${n}. ${['认识世界','经济与体力','探索世界','装备与宠物','小游戏与 Boss','社交与长期成长'][i]}</strong><br>${['/世界 /我的 /帮助','签到、任务、体力与经济流水','地图、普通/深度/危险探索','宠物、装备、强化','小游戏与世界 Boss','排行榜、转账、成就与长期成长'][i]}</div>`).join('')}<div class="notice" style="margin-top:12px"><strong>自定义教程</strong><br>在“高级配置 → 教程 / 数据”中修改 <span class="code">tutorial_pages_json</span>。玩家可使用 <span class="code">/教程</span>、<span class="code">/继续教程</span>、<span class="code">/跳过教程</span>、<span class="code">/教程 重开</span>。</div></div></section>`;
}
function renderCloud(){
  const cloud=(key,def='')=>state.settings?.[key]??def;
  const secret=String(cloud('cloud_api_key',''));
  const selected=Array.isArray(state._cloudSelected)?state._cloudSelected.map(String):[];
  const site=state._cloudSite||{};
  const announcements=Array.isArray(state._cloudAnnouncements)?state._cloudAnnouncements:[];
  return `<div class="cloud-doc-shell">
    <aside class="cloud-doc-nav">
      <div class="cloud-doc-kicker">ASTRBOT CLOUD</div>
      <div class="cloud-doc-title">云端玩法</div>
      <div class="cloud-doc-copy">像 AstrBot 插件配置文档一样，把连接、商品、JSON、公告和生效状态集中到一个清晰的配置面板。</div>
      <a href="#cloud-connect">连接与 Key</a>
      <a href="#cloud-products">商品目录</a>
      <a href="#cloud-json">社区 JSON</a>
      <a href="#cloud-announcements">网站公告</a>
      <a href="#cloud-effective">生效数据</a>
    </aside>
    <div class="cloud-doc-main">
      <section class="cloud-hero" id="cloud-connect">
        <div class="cloud-hero-copy">
          <div class="eyebrow">YSGL CLOUD PLAYGROUND</div>
          <h2>云端玩法配置</h2>
          <p>使用 ysgl 网站生成的 API Key，从云端读取已经审核发布的商品、Boss、教程和社区 JSON。网站账号与 AstrBot 本地玩家、管理员体系保持独立。</p>
          <div class="cloud-hero-badges"><span id="cloudHeroStatus" class="cloud-state">检查中…</span><span class="pill">默认站点：ysgl.bot.cd/astrbot</span></div>
        </div>
        <div class="cloud-hero-stat"><div class="cloud-hero-icon">☁</div><div><div class="muted">当前云端用户</div><strong id="cloudHeroUser">未连接</strong></div></div>
      </section>

      <section class="card cloud-doc-card">
        <div class="card-head"><div><div class="card-title">① 连接设置</div><div class="section-desc">对应 AstrBot 风格的插件配置区：填写 Key、控制同步和内容来源。</div></div><span class="pill">API Key</span></div>
        <div class="cloud-config-grid">
          <div class="field cloud-wide"><label>云端地址</label><input id="cloud_base_url_field" value="${esc(cloud('cloud_base_url','https://ysgl.bot.cd/astrbot'))}" placeholder="https://ysgl.bot.cd/astrbot"><div class="field-hint">不要填写 /api.php；插件会自动访问站点 API。</div></div>
          <div class="field cloud-wide"><label>API Key <span class="pill good">网站生成</span></label><input id="cloud_api_key_field" type="password" value="${secret==='********'?'':esc(secret)}" placeholder="gw_live_…"><div class="field-hint">Key 只保存在 AstrBot 本地配置中，不会上传到社区内容。</div></div>
          <div class="field"><label>自动同步间隔</label><div class="cloud-inline-input"><input id="cloud_sync_interval_field" type="number" min="5" max="1440" step="5" value="${esc(cloud('cloud_sync_interval_minutes',15))}"><span>分钟</span></div></div>
          <div class="field"><label>请求超时</label><div class="cloud-inline-input"><input id="cloud_timeout_field" type="number" min="3" max="60" value="${esc(cloud('cloud_timeout_seconds',12))}"><span>秒</span></div></div>
        </div>
        <div class="cloud-option-list">
          ${cloudSwitch('cloud_enabled','启用云端玩法',cloud('cloud_enabled',false))}
          ${cloudSwitch('cloud_auto_sync','自动同步目录',cloud('cloud_auto_sync',true))}
          ${cloudSwitch('cloud_use_official_content','获取官方内容',cloud('cloud_use_official_content',true))}
          ${cloudSwitch('cloud_use_community_content','获取其他用户公开内容',cloud('cloud_use_community_content',true))}
          ${cloudSwitch('cloud_use_own_content','获取当前 Key 自己发布的内容',cloud('cloud_use_own_content',true))}
          ${cloudSwitch('cloud_sync_packages','启用社区 JSON',cloud('cloud_sync_packages',true))}
        </div>
        <div class="actions cloud-action-row"><button class="primary" id="cloudSaveConfig">保存配置</button><button class="ghost" id="cloudTest">测试连接</button><button class="ghost" id="cloudSync">立即同步</button></div>
        <div id="cloudConnectionNotice" class="notice cloud-notice">正在读取云端连接状态…</div>
      </section>

      <section class="cloud-stat-grid">
        <div class="cloud-stat"><span>云端商品</span><strong id="cloudProductCount">—</strong><small>Key 可见的已发布商品</small></div>
        <div class="cloud-stat"><span>公开 JSON</span><strong id="cloudPackageCount">—</strong><small>可由你自定义勾选</small></div>
        <div class="cloud-stat"><span>当前生效 JSON</span><strong id="cloudSelectedCount">${num(selected.length)}</strong><small>只按 package_no 保存选择</small></div>
        <div class="cloud-stat"><span>网站公告</span><strong id="cloudAnnouncementCount">${num(announcements.length)}</strong><small>来自 ysgl 网站</small></div>
      </section>

      <section class="card cloud-doc-card" id="cloud-products">
        <div class="card-head"><div><div class="card-title">② 云端商品目录</div><div class="section-desc">商品通过网站审核后，由当前 API Key 获取。商品会合并到群聊世界商店，本地同编号商品优先。</div></div><span id="cloudProductCountBadge" class="pill">—</span></div>
        <div class="cloud-toolbar"><input id="cloudProductSearch" placeholder="搜索商品名称 / 编号 / 作者" value="${esc(state._cloudProductSearch||'')}"><button class="ghost small" id="cloudProductSearchBtn">筛选</button></div>
        <div id="cloudProducts" class="cloud-product-list"><div class="empty">正在读取商品目录…</div></div>
      </section>

      <section class="card cloud-doc-card" id="cloud-json">
        <div class="card-head"><div><div class="card-title">③ 社区 JSON 数据包</div><div class="section-desc">每个数据包都显示上传用户。你可以自由组合多个公开包；保存后插件会把选中包中的<strong>已识别安全数据</strong>合并到对应云端数据目录。</div></div><div class="actions"><span id="cloudPackageCountBadge" class="pill">—</span><span id="cloudSelectedCountBadge" class="pill good">已选 ${num(selected.length)}</span></div></div>
        <div class="cloud-package-controls">
          <input id="cloudPackageSearch" placeholder="搜索标题 / 包编号 / 上传用户" value="${esc(state._cloudPackageSearch||'')}">
          <select id="cloudPackageAuthor"><option value="">所有上传用户</option></select>
          <select id="cloudPackageCategory"><option value="">所有分类</option></select>
          <label class="cloud-filter-check"><input id="cloudOnlySelected" type="checkbox" ${state._cloudOnlySelected?'checked':''}>只看已选择</label>
          <button class="ghost small" id="cloudPackageSearchBtn">应用筛选</button>
        </div>
        <div class="actions cloud-package-actions"><button class="ghost small" id="cloudSelectAll">全选筛选结果</button><button class="ghost small" id="cloudClearSelection">清空选择</button><button class="primary small" id="cloudSaveSelection">保存选择并合并</button></div>
        <div id="cloudPackages" class="cloud-package-list"><div class="empty">正在读取公开 JSON…</div></div>
      </section>

      <section class="card cloud-doc-card" id="cloud-announcements">
        <div class="card-head"><div><div class="card-title">④ ysgl 网站公告</div><div class="section-desc">从云端网站公告接口读取最新公告；这里只显示，不修改网站内容。</div></div><button class="ghost small" id="cloudAnnouncementsRefresh">刷新公告</button></div>
        <div class="cloud-site-banner"><div class="cloud-site-dot"></div><div><strong id="cloudSiteName">${esc(site.name||'ysgl 云端玩法中心')}</strong><div id="cloudSiteAnnouncement" class="muted">${esc(site.announcement||'欢迎来到群聊世界云端玩法中心。')}</div></div><span id="cloudSiteVersion" class="pill">${esc(site.version||'—')}</span></div>
        <div id="cloudAnnouncements" class="cloud-announcement-list"><div class="empty">正在读取公告…</div></div>
      </section>

      <section class="card cloud-doc-card" id="cloud-effective">
        <div class="card-head"><div><div class="card-title">⑤ 当前生效数据</div><div class="section-desc">查看 JSON 选择合并后的安全数据统计。未识别字段不会执行，也不会覆盖本地管理员自定义配置。</div></div></div>
        <div class="cloud-effective-grid"><div><span>商品</span><b id="cloudEffectiveProducts">—</b></div><div><span>Boss</span><b id="cloudEffectiveBosses">—</b></div><div><span>怪物</span><b id="cloudEffectiveMonsters">—</b></div><div><span>NPC</span><b id="cloudEffectiveNpcs">—</b></div><div><span>教程页</span><b id="cloudEffectiveTutorials">—</b></div></div>
        <div class="actions cloud-action-row"><button class="ghost" id="cloudExport">导出当前云端数据</button><button class="ghost" id="cloudLocalExport">导出本地自定义数据</button></div>
      </section>
    </div>
  </div>`;
}
function cloudSwitch(key,label,checked){const note={cloud_enabled:'整个云端玩法总开关；关闭后不访问云端。',cloud_auto_sync:'按间隔自动同步已审核目录。',cloud_use_official_content:'获取网站管理员审核通过并发布的官方内容。',cloud_use_community_content:'获取其他网站用户审核通过并公开的 Boss / 商品 / 教程。',cloud_use_own_content:'获取当前 API Key 对应账号已经审核通过的内容。',cloud_sync_packages:'允许在下方自定义选择多个公开 JSON 数据包。'}[key]||'云端内容来源控制';return `<label class="cloud-switch"><span><b>${esc(label)}</b><small>${esc(note)}</small></span><span class="cloud-toggle"><input type="checkbox" data-cloud-key="${key}" ${checked?'checked':''}><i></i></span></label>`;}

function cloudFilteredPackages(){
  const q=String(state._cloudPackageSearch||'').trim().toLowerCase();
  const author=String(state._cloudPackageAuthor||'');
  const category=String(state._cloudPackageCategory||'');
  const selected=new Set((state._cloudSelected||[]).map(String));
  return (Array.isArray(state._cloudPackages)?state._cloudPackages:[]).filter(r=>{
    const no=String(r.package_no||r.id||'');
    const matchQ=!q || [r.title,r.description,r.owner_username,r.package_no,r.category].some(x=>String(x||'').toLowerCase().includes(q));
    const matchAuthor=!author || String(r.owner_username||'社区用户')===author;
    const matchCategory=!category || String(r.category||'其他')===category;
    const matchSelected=!state._cloudOnlySelected || selected.has(no);
    return matchQ&&matchAuthor&&matchCategory&&matchSelected;
  });
}
function rebuildCloudPackageFilters(){
  const list=Array.isArray(state._cloudPackages)?state._cloudPackages:[];
  const authors=[...new Set(list.map(x=>String(x.owner_username||'社区用户')).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh'));
  const cats=[...new Set(list.map(x=>String(x.category||'其他')).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh'));
  const a=$('cloudPackageAuthor'),c=$('cloudPackageCategory');
  if(a){a.innerHTML='<option value="">所有上传用户</option>'+authors.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');a.value=state._cloudPackageAuthor||'';}
  if(c){c.innerHTML='<option value="">所有分类</option>'+cats.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');c.value=state._cloudPackageCategory||'';}
}

async function loadCloud(){
  try{
    const s=await apiGet('cloud/portal');
    state._cloudAnnouncements=Array.isArray(s.announcements)?s.announcements:[];
    state._cloudSite=s.site&&typeof s.site==='object'?s.site:{};
    state._cloudPackages=Array.isArray(s.community_packages)?s.community_packages:[];
    state._cloudSelected=Array.isArray(s.selected_package_nos)?s.selected_package_nos.map(String):[];
    state._cloudProductCatalog=Array.isArray(s.products_catalog)?s.products_catalog:[];
    rebuildCloudPackageFilters();
    const configured=!!s.configured;
    const status=configured?(s.reachable?(s.enabled?'已连接':'Key 有效 · 云端未启用'):'连接失败'):'未配置 Key';
    if($('cloudHeroStatus')){$('cloudHeroStatus').textContent=status;$('cloudHeroStatus').className='cloud-state '+(s.reachable&&s.enabled?'ok':configured&&!s.reachable?'bad':'warn');}
    if($('cloudHeroUser'))$('cloudHeroUser').textContent=s.user?.username||'未连接';
    if($('cloudConnectionNotice'))$('cloudConnectionNotice').innerHTML=`<strong>${esc(s.message||'')}</strong>${s.user?`<br><span class="muted">当前 Key 用户：${esc(s.user.username||'用户')} · ${esc(s.user.role||'user')}</span>`:''}<br><span class="muted">地址：${esc(s.base_url||'—')} · 最后同步：${esc(s.last_sync_at||0)}</span>`;
    if($('cloudProductCount'))$('cloudProductCount').textContent=num(s.counts?.products||0);
    if($('cloudPackageCount'))$('cloudPackageCount').textContent=num(s.counts?.community_packages||0);
    if($('cloudSelectedCount'))$('cloudSelectedCount').textContent=num(state._cloudSelected.length);
    if($('cloudAnnouncementCount'))$('cloudAnnouncementCount').textContent=num(state._cloudAnnouncements.length);
    if($('cloudProductCountBadge'))$('cloudProductCountBadge').textContent=num(s.counts?.products||0)+' 件';
    if($('cloudPackageCountBadge'))$('cloudPackageCountBadge').textContent=num(s.counts?.community_packages||0)+' 个';
    if($('cloudSelectedCountBadge'))$('cloudSelectedCountBadge').textContent='已选 '+num(state._cloudSelected.length);
    renderCloudProducts(state._cloudProductCatalog);
    renderCloudPackages(state._cloudPackages);
    renderCloudAnnouncements(state._cloudAnnouncements,state._cloudSite);
    const ac=s.active_merge_counts||{};
    const set=(id,v)=>{if($(id))$(id).textContent=num(v||0)};
    set('cloudEffectiveProducts',ac.products);set('cloudEffectiveBosses',ac.bosses);set('cloudEffectiveMonsters',ac.monsters);set('cloudEffectiveNpcs',ac.npcs);set('cloudEffectiveTutorials',ac.tutorials);
    return s;
  }catch(e){
    if($('cloudHeroStatus')){ $('cloudHeroStatus').textContent='异常'; $('cloudHeroStatus').className='cloud-state bad'; }
    if($('cloudConnectionNotice'))$('cloudConnectionNotice').textContent=e.message||String(e);
    if($('cloudAnnouncements'))$('cloudAnnouncements').innerHTML=`<div class="empty">公告读取失败：${esc(e.message||String(e))}</div>`;
  }
}
function renderCloudProducts(rows){
  const root=$('cloudProducts'); if(!root)return;
  const q=String(state._cloudProductSearch||'').trim().toLowerCase();
  const list=(Array.isArray(rows)?rows:[]).filter(r=>!q||[r.name,r.item_no,r.owner_username,r.intro,r.description].some(x=>String(x||'').toLowerCase().includes(q))).slice(0,120);
  root.innerHTML=list.map(r=>{const badges=Array.isArray(r.badges)?r.badges.slice():[];if(r.official&&!badges.includes('官方'))badges.unshift('官方');if(r.quality&&!badges.includes('优质'))badges.push('优质');const badgeHtml=badges.map(b=>`<span class="pill ${b==='官方'?'good':'warn'}">${esc(b)}</span>`).join(' ');return `<article class="cloud-product-row"><div class="cloud-product-icon">◆</div><div class="package-main"><div class="package-title">${esc(r.name||'未命名商品')} <span class="pill good">已发布</span> ${badgeHtml}</div><div class="package-meta">编号 <span class="code">${esc(r.item_no||r.id||'—')}</span> · ${num(r.price||0)} 金币 · 作者 ${esc(r.owner_username||'官方')}</div><div class="package-desc">${esc(r.intro||r.description||'云端商品')}</div></div><div class="cloud-product-price"><b>${num(r.price||0)}</b><span>金币</span></div></article>`}).join('')||'<div class="empty">没有符合条件的已发布商品。</div>';
}
function renderCloudPackages(rows){
  const root=$('cloudPackages'); if(!root)return;
  const list=cloudFilteredPackages();
  const selected=new Set((state._cloudSelected||[]).map(String));
  if($('cloudPackageCountBadge'))$('cloudPackageCountBadge').textContent=num((rows||[]).length)+' 个';
  if($('cloudSelectedCountBadge'))$('cloudSelectedCountBadge').textContent='已选 '+num(selected.size);
  root.innerHTML=list.map(r=>{const no=String(r.package_no||r.id||'');const isSelected=selected.has(no);const badges=Array.isArray(r.badges)?r.badges.slice():[];if(r.official&&!badges.includes('官方'))badges.unshift('官方');if(r.quality&&!badges.includes('优质'))badges.push('优质');const badgeHtml=badges.map(b=>`<span class="pill ${b==='官方'?'good':'warn'}">${esc(b)}</span>`).join(' ');return `<article class="cloud-package-row ${isSelected?'is-active':''}"><label class="cloud-package-select"><input type="checkbox" data-cloud-package-select="${esc(no)}" ${isSelected?'checked':''}><span></span></label><div class="package-main"><div class="package-title">${esc(r.title||'未命名 JSON')} <span class="pill ${isSelected?'good':'warn'}">${isSelected?'已启用':'待选择'}</span> ${badgeHtml}</div><div class="package-meta"><span class="code">${esc(no)}</span> · 上传用户 <b>${esc(r.owner_username||'社区用户')}</b> · ${esc(r.category||'其他')}</div><div class="package-desc">${esc(r.description||'无介绍')}</div>${Array.isArray(r.tags)&&r.tags.length?`<div class="package-tags">${r.tags.map(t=>`<span class="pill">${esc(t)}</span>`).join('')}</div>`:''}</div><div class="package-actions"><button type="button" class="ghost small" data-cloud-view="${esc(r.id||no)}">预览 JSON</button></div></article>`}).join('')||'<div class="empty">暂无符合筛选条件的公开 JSON。</div>';
  root.querySelectorAll('[data-cloud-package-select]').forEach(el=>el.onchange=()=>{const v=String(el.dataset.cloudPackageSelect);const set=new Set((state._cloudSelected||[]).map(String));if(el.checked)set.add(v);else set.delete(v);state._cloudSelected=[...set].slice(0,50);renderCloudPackages(rows);});
  root.querySelectorAll('[data-cloud-view]').forEach(b=>b.onclick=async e=>{e.preventDefault();try{const r=await apiPost('cloud/package-preview',{package_id:Number(b.dataset.cloudView)||0,package_no:Number(b.dataset.cloudView)?'':String(b.dataset.cloudView)});showCloudJson(r.package||r)}catch(err){toast(err.message||String(err));}});
}
function renderCloudAnnouncements(rows,site){
  if($('cloudSiteName'))$('cloudSiteName').textContent=site?.name||'ysgl 云端玩法中心';
  if($('cloudSiteAnnouncement'))$('cloudSiteAnnouncement').textContent=site?.announcement||'欢迎来到群聊世界云端玩法中心。';
  if($('cloudSiteVersion'))$('cloudSiteVersion').textContent=site?.version||'—';
  const root=$('cloudAnnouncements');if(!root)return;
  const list=Array.isArray(rows)?rows:[];
  root.innerHTML=list.slice(0,20).map(r=>`<article class="cloud-announcement-row"><div class="cloud-announcement-icon">${r.level==='danger'?'!':r.level==='warning'?'⚠':r.level==='success'?'✓':'i'}</div><div><div class="package-title">${esc(r.title||'网站公告')} <span class="pill">${esc(r.level||'info')}</span></div><div class="package-desc">${esc(r.content||'')}</div><div class="package-meta">${esc(r.published_at||'')}</div></div></article>`).join('')||'<div class="empty">暂无网站公告。</div>';
}
function showCloudJson(pkg){const payload=pkg.payload||{};const txt=JSON.stringify(payload,null,2);$('cloudJsonModal')?.remove();const wrap=document.createElement('div');wrap.id='cloudJsonModal';wrap.className='modal hidden';wrap.innerHTML=`<div class="modal-card"><div class="modal-head"><div><div class="eyebrow">PUBLIC JSON</div><h2>${esc(pkg.title||'JSON 数据包')}</h2><p class="muted">上传用户：${esc(pkg.owner_username||'社区用户')} · 编号：${esc(pkg.package_no||pkg.id||'')}</p></div><button class="ghost" id="cloudJsonClose">关闭</button></div><pre class="json-preview">${esc(txt)}</pre><div class="modal-actions"><button class="ghost" id="cloudJsonClose2">关闭</button></div></div>`;document.body.appendChild(wrap);wrap.classList.remove('hidden');wrap.querySelector('#cloudJsonClose').onclick=()=>wrap.remove();wrap.querySelector('#cloudJsonClose2').onclick=()=>wrap.remove();wrap.onclick=e=>{if(e.target===wrap)wrap.remove()};}
function updateCloudSelection(selected){state._cloudSelected=Array.from(new Set((selected||[]).map(String))).filter(Boolean).slice(0,50);renderCloudPackages(state._cloudPackages||[]);}
async function saveCloudSelection(){
  const selected=Array.isArray(state._cloudSelected)?state._cloudSelected.map(String).slice(0,50):[];
  try{
    await apiPost('settings/save',{changes:{cloud_selected_package_nos:selected}});
    toast('公开 JSON 选择已保存，正在同步并合并…');
    const sync=await apiPost('cloud/sync',{});
    const applied=sync.data?.selected_packages?.length||0;
    toast(`已应用 ${num(applied)} 个 JSON 数据包`);
    await loadCloud();
  }catch(e){toast(e.message||String(e));}
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
  check('gf_npc_enabled',g.npc_enabled);set('gf_npc_chance',g.npc_chance_percent??10);set('gf_npc_interval',g.npc_interval_minutes??120);
  const modal=$('groupModal'); if(modal)modal.dataset.group=String(groupId);
  modal?.classList.remove('hidden');
}
async function saveGroupSettings(){
  const modal=$('groupModal'); const groupId=modal?.dataset.group||'';
  if(!groupId){toast('未选择群聊');return;}
  const val=id=>$(id)?.value||''; const checked=id=>!!$(id)?.checked;
  try{
    const r=await apiPost('group/action',{group_id:groupId,action:'save_settings',settings:{world_weather:val('gf_weather').trim(),world_location:val('gf_location').trim(),world_event_enabled:checked('gf_event_enabled')?1:0,explore_enabled:checked('gf_explore_enabled')?1:0,monster_enabled:checked('gf_monster_enabled')?1:0,monster_chance_percent:Number(val('gf_monster_chance')),monster_max_count:Number(val('gf_monster_max')),monster_multi_chance_percent:Number(val('gf_monster_multi')),npc_enabled:checked('gf_npc_enabled')?1:0,npc_chance_percent:Number(val('gf_npc_chance')),npc_interval_minutes:Number(val('gf_npc_interval'))}});
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
function openImport(){ $('importModal')?.classList.remove('hidden'); $('importHint').textContent=''; if($('importConfig'))$('importConfig').checked=true; }
function closeImport(){ $('importModal')?.classList.add('hidden'); }
async function importData(){
  let text=$('importText')?.value.trim()||'';
  const file=$('importFile')?.files?.[0];
  if(!text && file){ text=await file.text(); }
  if(!text){toast('请选择 JSON 文件或粘贴 JSON 数据');return;}
  const hint=$('importHint'); if(hint)hint.textContent='正在验证并导入，请勿重复点击。';
  try{
    const snapshot=JSON.parse(text);
    const r=await apiPost('data/import',{snapshot,import_config:$('importConfig')?.checked!==false});
    toast(r.message||'数据导入成功');
    if(hint)hint.textContent=`完成。自动备份：${r.backup||'已创建'}`;
    $('importModal')?.classList.add('hidden');
    await loadCore();
  }catch(e){ if(hint)hint.textContent=e.message||String(e); toast(e.message||String(e)); }
}
function attach(){
  $('menuQuickBtn')?.addEventListener('click',()=>{state.tab='menu';renderNav();render();});
  const bind=(id,event,fn)=>{const el=$(id);if(el)el[event]=fn;};
  bind('playerModalClose','onclick',closePlayerEditor); if($('playerModal'))$('playerModal').onclick=e=>{if(e.target.id==='playerModal')closePlayerEditor();};
  bind('groupModalClose','onclick',closeGroupEditor); if($('groupModal'))$('groupModal').onclick=e=>{if(e.target.id==='groupModal')closeGroupEditor();};
  bind('importModalClose','onclick',closeImport); bind('importCancel','onclick',closeImport); if($('importModal'))$('importModal').onclick=e=>{if(e.target.id==='importModal')closeImport();};
  bind('importBtn','onclick',openImport);
  bind('exportBtn','onclick',exportData);
  bind('overviewRefresh','onclick',()=>loadCore());
  bind('refreshBtn','onclick',()=>loadCore());
  bind('cloudSaveConfig','onclick',()=>saveCloudConfig()); bind('cloudTest','onclick',async()=>{try{await saveCloudConfig({silent:true,refresh:false});const r=await apiGet('cloud/test');toast(r.message||'连接正常');await loadCloud();}catch(e){toast(e.message||String(e));}}); bind('cloudSync','onclick',async()=>{try{await saveCloudConfig({silent:true,refresh:false});const r=await apiPost('cloud/sync',{});toast('云端目录同步完成');await loadCloud();}catch(e){toast(e.message||String(e));}}); bind('cloudSelectAll','onclick',()=>updateCloudSelection(cloudFilteredPackages().map(x=>x.package_no||x.id))); bind('cloudClearSelection','onclick',()=>updateCloudSelection([])); bind('cloudSaveSelection','onclick',saveCloudSelection); bind('cloudExport','onclick',async()=>{try{const r=await bridge.download('cloud/export',gparams({}),'group-world-cloud-package.json');toast(`导出完成：${r.filename||'group-world-cloud-package.json'}`);}catch(e){toast(e.message||String(e));}}); bind('cloudLocalExport','onclick',async()=>{try{const r=await bridge.download('cloud/local-export',gparams({}),'group-world-local-custom-package.json');toast(`导出完成：${r.filename||'group-world-local-custom-package.json'}`);}catch(e){toast(e.message||String(e));}}); bind('cloudPackageSearchBtn','onclick',()=>{state._cloudPackageSearch=$('cloudPackageSearch')?.value||'';state._cloudPackageAuthor=$('cloudPackageAuthor')?.value||'';state._cloudPackageCategory=$('cloudPackageCategory')?.value||'';state._cloudOnlySelected=!!$('cloudOnlySelected')?.checked;renderCloudPackages(state._cloudPackages);}); bind('cloudPackageSearch','onkeydown',e=>{if(e.key==='Enter')$('cloudPackageSearchBtn')?.click();}); bind('cloudProductSearchBtn','onclick',()=>{state._cloudProductSearch=$('cloudProductSearch')?.value||'';renderCloudProducts(state._cloudProductCatalog);}); bind('cloudProductSearch','onkeydown',e=>{if(e.key==='Enter')$('cloudProductSearchBtn')?.click();}); bind('cloudAnnouncementsRefresh','onclick',async()=>{try{const r=await apiGet('cloud/announcements');state._cloudAnnouncements=r.announcements||[];renderCloudAnnouncements(state._cloudAnnouncements,state._cloudSite);toast('网站公告已刷新');}catch(e){toast(e.message||String(e));}});
  bind('importRun','onclick',importData);
  bind('loginBtn','onclick',login);
  bind('loginPassword','onkeydown',e=>{if(e.key==='Enter')login();});
  bind('reloadSettings','onclick',async()=>{try{const d=await apiGet('settings');state.settings=d.config;state.schema=d.schema;render();toast('配置已重新读取');}catch(e){toast(e.message||String(e));}});
  bind('saveSettings','onclick',saveSettings);
  bind('systemRefresh','onclick',loadSystem); bind('systemCleanup','onclick',cleanupSystem); bind('broadcastSaveDefaults','onclick',saveSystemSettings); bind('broadcastSelectAll','onclick',()=>setBroadcastSelection('all')); bind('broadcastSelectNone','onclick',()=>setBroadcastSelection('none')); bind('broadcastSelectEnabled','onclick',()=>setBroadcastSelection('enabled')); bind('broadcastBatchSave','onclick',saveBroadcastBatch); bind('broadcastBatchSend','onclick',sendBroadcastBatch); document.querySelectorAll('.br-select').forEach(x=>x.addEventListener('change',updateBroadcastSelection)); document.querySelectorAll('[data-broadcast-row]').forEach(row=>{row.querySelector('.br-save')?.addEventListener('click',()=>saveBroadcastRow(row));row.querySelector('.br-send')?.addEventListener('click',()=>sendBroadcastNow(row));});
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
async function saveCloudConfig(options={}){
  const silent=!!options.silent, refresh=options.refresh!==false;
  const changes={
    cloud_base_url:String($('cloud_base_url_field')?.value||'').trim()||'https://ysgl.bot.cd/astrbot',
    cloud_api_key:String($('cloud_api_key_field')?.value||''),
    cloud_sync_interval_minutes:Number.parseInt($('cloud_sync_interval_field')?.value||'15',10),
    cloud_timeout_seconds:Number.parseInt($('cloud_timeout_field')?.value||'12',10),
    cloud_selected_package_nos:Array.isArray(state._cloudSelected)?state._cloudSelected.map(String).slice(0,50):[],
  };
  document.querySelectorAll('[data-cloud-key]').forEach(el=>{changes[el.dataset.cloudKey]=!!el.checked});
  if(changes.cloud_api_key==='********')delete changes.cloud_api_key;
  try{const r=await apiPost('settings/save',{changes});state.settings=r.config||state.settings;if(!silent)toast('云端配置已保存');if(refresh)await loadCloud();return r;}catch(e){if(!silent)toast(e.message||String(e));throw e;}
}
async function saveSettings(){const changes={};document.querySelectorAll('[data-setting]').forEach(el=>{const key=el.dataset.setting,sp=state.schema[key]||{};if(sp.type==='bool')changes[key]=el.checked;else if(sp.type==='int')changes[key]=Number.parseInt(el.value||'0',10);else if(sp.type==='float')changes[key]=Number.parseFloat(el.value||'0');else if(sp.type==='text')changes[key]=el.value;else if(sp.secret&&el.value===''){}else changes[key]=el.value;});try{const r=await apiPost('settings/save',{changes});state.settings=r.config||state.settings;toast('全部配置保存成功');render();}catch(e){toast(e.message||String(e));}}
async function exportData(){try{const r=await bridge.download('data/export',gparams({group_id:state.groupId||''}),'group-world-export.json');toast(`导出已生成：${r.filename||'group-world-export.json'}`);}catch(e){toast(e.message||String(e));}}
boot().catch(e=>showLogin(e.message||String(e)));

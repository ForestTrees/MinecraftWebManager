const state = { socket: null, loggedOut: false, history: [], historyIndex: -1, draft: '', activeView: 'console', chartRange: '1h', chartData: null };
const $ = (id) => document.getElementById(id);

const VIEW_TITLES = { console: '实时控制台', players: '玩家管理', world: 'World', performance: '服务器状态' };

// server.properties: zh-CN labels; keys not listed fall back to the raw key name.
const PROPERTY_LABELS = {
  'accepts-transfers': '接受跨服传送', 'allow-flight': '允许飞行',
  'allow-nether': '允许下界', 'broadcast-console-to-ops': '控制台命令广播给 OP',
  'broadcast-rcon-to-ops': 'RCON 命令广播给 OP', 'bug-report-link': '问题反馈链接',
  'chat-spam-threshold-seconds': '聊天刷屏阈值（秒）', 'command-spam-threshold-seconds': '命令刷屏阈值（秒）',
  'difficulty': '游戏难度', 'enable-code-of-conduct': '启用行为准则',
  'enable-command-block': '启用命令方块', 'enable-jmx-monitoring': '启用 JMX 监控',
  'enable-query': '启用 Query 协议', 'enable-rcon': '启用 RCON', 'enable-status': '响应服务器列表状态',
  'enforce-secure-profile': '强制安全聊天签名', 'enforce-whitelist': '强制白名单',
  'entity-broadcast-range-percentage': '实体广播范围（%）', 'force-gamemode': '强制默认游戏模式',
  'function-permission-level': '函数权限等级', 'gamemode': '默认游戏模式',
  'generate-structures': '生成建筑结构', 'generator-settings': '世界生成器设置',
  'hardcore': '极限模式', 'hide-online-players': '隐藏在线玩家列表',
  'initial-disabled-packs': '初始禁用数据包', 'initial-enabled-packs': '初始启用数据包',
  'level-name': '世界名称', 'level-seed': '世界种子', 'level-type': '世界类型',
  'log-ips': '日志记录玩家 IP', 'max-chained-neighbor-updates': '最大连锁更新数',
  'max-players': '最大玩家数', 'max-tick-time': '单 Tick 最大耗时（毫秒）',
  'max-world-size': '世界半径上限', 'motd': '服务器标语（MOTD）',
  'network-compression-threshold': '网络压缩阈值', 'online-mode': '正版验证',
  'op-permission-level': 'OP 权限等级', 'pause-when-empty-seconds': '无人时暂停（秒）',
  'player-idle-timeout': '玩家挂机超时（分钟）', 'prevent-proxy-connections': '阻止代理连接',
  'pvp': '允许 PVP', 'query.port': 'Query 端口', 'rate-limit': '数据包速率限制',
  'rcon.password': 'RCON 密码', 'rcon.port': 'RCON 端口',
  'region-file-compression': '区域文件压缩方式', 'require-resource-pack': '强制使用资源包',
  'resource-pack': '资源包地址', 'resource-pack-id': '资源包 ID',
  'resource-pack-prompt': '资源包提示语', 'resource-pack-sha1': '资源包 SHA1',
  'server-ip': '监听 IP', 'server-port': '服务器端口', 'simulation-distance': '模拟距离',
  'spawn-monsters': '生成怪物', 'spawn-protection': '出生点保护半径',
  'status-heartbeat-interval': '状态心跳间隔', 'sync-chunk-writes': '同步写入区块',
  'text-filtering-config': '文本过滤配置', 'text-filtering-version': '文本过滤版本',
  'use-native-transport': '使用原生网络传输', 'view-distance': '视距（区块）',
  'white-list': '启用白名单',
  'management-server-enabled': '启用管理服务器', 'management-server-host': '管理服务器地址',
  'management-server-port': '管理服务器端口', 'management-server-secret': '管理服务器密钥',
  'management-server-tls-enabled': '管理服务器启用 TLS',
  'management-server-tls-keystore': '管理服务器 TLS 证书库',
  'management-server-tls-keystore-password': '管理服务器 TLS 证书库密码',
  'management-server-allowed-origins': '管理服务器允许来源',
};
const PROPERTY_OPTIONS = {
  difficulty: ['peaceful', 'easy', 'normal', 'hard'],
  gamemode: ['survival', 'creative', 'adventure', 'spectator'],
  'region-file-compression': ['deflate', 'lz4', 'none'],
};

const BASE_COMMAND_SUGGESTIONS = [
  '!!MCDR status', '!!MCDR reload plugin', '!!MCDR reload config', '!!MCDR reload permission', '!!MCDR reload all',
  '!!MCDR permission list', '!!MCDR permission set ', '!!MCDR plugin list', '!!MCDR plugin reload ', '!!MCDR check_update',
  '!!help',
  'list', 'say ', 'stop', 'whitelist add ', 'whitelist remove ', 'whitelist list', 'op ', 'deop ',
  'gamemode survival ', 'gamemode creative ', 'gamemode adventure ', 'gamemode spectator ',
  'time set day', 'time set night', 'weather clear', 'weather rain',
  'difficulty peaceful', 'difficulty easy', 'difficulty normal', 'difficulty hard',
  'kick ', 'ban ', 'pardon ', 'tp ', 'give ', 'save-all',
];
let commandSuggestionPool = [...BASE_COMMAND_SUGGESTIONS];
const suggestionState = { items: [], activeIndex: -1 };

const actionButtons = {
  start: document.querySelector('[data-action="start"]'),
  restart: document.querySelector('[data-action="restart"]'),
  stop: document.querySelector('[data-action="stop"]'),
};

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) return logout();
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || response.statusText);
  return response.json();
}

/* ---------- formatting helpers ---------- */
function formatBytes(value) { if (value == null) return '--'; const units = ['B','KiB','MiB','GiB','TiB']; let i=0; while(value >= 1024 && i < units.length-1) { value /= 1024; i++; } return `${value.toFixed(i ? 1 : 0)} ${units[i]}`; }
function formatDuration(seconds) {
  if (seconds == null) return '--';
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  return h > 0 ? `${h}时${m}分${s}秒` : m > 0 ? `${m}分${s}秒` : `${s}秒`;
}
function formatDateTime(epochSeconds) {
  if (!epochSeconds) return '--';
  const d = new Date(epochSeconds * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  const sameYear = d.getFullYear() === new Date().getFullYear();
  const date = sameYear ? `${pad(d.getMonth() + 1)}-${pad(d.getDate())}` : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function escapeHtml(text) { const node = document.createElement('span'); node.textContent = String(text); return node.innerHTML; }

/* ---------- Minecraft § color/format codes ---------- */
const MC_COLORS = {
  '0': '#000000', '1': '#0000AA', '2': '#00AA00', '3': '#00AAAA',
  '4': '#AA0000', '5': '#AA00AA', '6': '#FFAA00', '7': '#AAAAAA',
  '8': '#555555', '9': '#5555FF', a: '#55FF55', b: '#55FFFF',
  c: '#FF5555', d: '#FF55FF', e: '#FFFF55', f: '#FFFFFF',
};
function formatMinecraftText(text) {
  const segments = String(text).split(/§([0-9a-fk-or])/i);
  let html = '';
  let open = false;
  let style = {};
  const close = () => { if (open) { html += '</span>'; open = false; } };
  segments.forEach((segment, index) => {
    if (index % 2 === 0) { html += escapeHtml(segment); return; }
    close();
    const code = segment.toLowerCase();
    if (code === 'r') style = {};
    else if (code === 'l') style = { ...style, bold: true };
    else if (code === 'o') style = { ...style, italic: true };
    else if (code === 'n') style = { ...style, underline: true };
    else if (code === 'm') style = { ...style, strikethrough: true };
    else if (MC_COLORS[code]) style = { color: MC_COLORS[code] };
    const declarations = [];
    if (style.color) declarations.push(`color:${style.color}`);
    if (style.bold) declarations.push('font-weight:700');
    if (style.italic) declarations.push('font-style:italic');
    const decorations = [style.underline && 'underline', style.strikethrough && 'line-through'].filter(Boolean);
    if (decorations.length) declarations.push(`text-decoration:${decorations.join(' ')}`);
    if (declarations.length) { html += `<span style="${declarations.join(';')}">`; open = true; }
  });
  close();
  return html;
}

const consoleLines = [];
const CONSOLE_MAX_LINES = 2000;
let consoleRenderScheduled = false;
function appendConsole(data) {
  const consoleEl = $('console');
  const line = data.raw || data.content || '';
  consoleLines.push(formatMinecraftText(line));
  if (consoleLines.length > CONSOLE_MAX_LINES) consoleLines.splice(0, consoleLines.length - CONSOLE_MAX_LINES);
  // Re-render the whole backlog on every line is wasteful during chat floods; batch
  // the writes into the next animation frame instead.
  if (consoleRenderScheduled) return;
  consoleRenderScheduled = true;
  requestAnimationFrame(() => {
    consoleRenderScheduled = false;
    consoleEl.innerHTML = consoleLines.join('\n');
    consoleEl.scrollTop = consoleEl.scrollHeight;
  });
}

/* ---------- toast + confirm modal ---------- */
function showToast(message, { type = 'info' } = {}) {
  let stack = document.getElementById('toast-stack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toast-stack';
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const toast = document.createElement('div');
  toast.className = type === 'error' ? 'toast error' : 'toast';
  toast.textContent = message;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

function confirmDialog(message, { title = '确认操作', confirmText = '确认' } = {}) {
  return new Promise((resolve) => {
    const overlay = $('confirm-modal');
    const confirmButton = $('confirm-modal-confirm');
    const cancelButton = $('confirm-modal-cancel');
    $('confirm-modal-title').textContent = title;
    $('confirm-modal-message').textContent = message;
    confirmButton.textContent = confirmText;
    overlay.hidden = false;
    const cleanup = (result) => {
      overlay.hidden = true;
      confirmButton.removeEventListener('click', onConfirm);
      cancelButton.removeEventListener('click', onCancel);
      overlay.removeEventListener('mousedown', onOverlayClick);
      document.removeEventListener('keydown', onKeydown);
      resolve(result);
    };
    const onConfirm = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onOverlayClick = (event) => { if (event.target === overlay) cleanup(false); };
    const onKeydown = (event) => { if (event.key === 'Escape') cleanup(false); };
    confirmButton.addEventListener('click', onConfirm);
    cancelButton.addEventListener('click', onCancel);
    overlay.addEventListener('mousedown', onOverlayClick);
    document.addEventListener('keydown', onKeydown);
  });
}

/* ---------- overview + views ---------- */
function updateServerActionButtons(data) {
  const running = !!data.running;
  if (actionButtons.start) actionButtons.start.disabled = running;
  if (actionButtons.restart) actionButtons.restart.disabled = !running;
  if (actionButtons.stop) actionButtons.stop.disabled = !running;
}

function updateOverviewStatus(data) {
  const statusClass = data.running ? (data.startup ? 'running' : 'starting') : 'stopped';
  $('ov-server-dot').className = `status-dot ${statusClass}`;
  $('ov-server-state-text').textContent = data.running ? (data.startup ? '运行中' : '启动中') : '已停止';
  $('ov-server-version').textContent = data.minecraft_version || '版本未知';
  $('ov-player-count').textContent = data.player_count;
  $('ov-player-list').textContent = data.players.join(', ') || '暂无在线玩家';
  $('ov-uptime').textContent = formatDuration(data.uptime_seconds);
  $('ov-mc-version').textContent = data.minecraft_version || '--';
  $('ov-mcdr-version').textContent = data.mcdr_version ? `MCDR ${data.mcdr_version}` : 'MCDR --';
  updateServerActionButtons(data);
}
function updateOverviewMetrics(data) {
  const tick = data.tick || {};
  $('ov-tick').textContent = tick.tps != null || tick.mspt != null
    ? `${tick.tps != null ? tick.tps.toFixed(1) : '--'} / ${tick.mspt != null ? tick.mspt.toFixed(1) + 'ms' : '--'}`
    : '--';
  $('ov-tps-hint').textContent = tick.available ? '来自 RCON tick query' : 'RCON 未启用/无数据';
  updatePerformanceTab(data);
}

async function refreshOverview() {
  try {
    updateOverviewStatus(await api('/api/server/status'));
    updateOverviewMetrics(await api('/api/performance'));
    const { plugins } = await api('/api/plugins');
    renderPlugins(plugins);
    commandSuggestionPool = [...BASE_COMMAND_SUGGESTIONS, ...plugins.map((p) => `!!MCDR plugin reload ${p.id}`)];
  } catch (error) { console.warn(error); }
}

function updatePerformanceTab(data) {
  // CPU / memory / network are shown as live charts instead of cards.
  const system = data.system, tick = data.tick || {};
  $('perf-swap').textContent = `${formatBytes(system.swap_used)} / ${formatBytes(system.swap_total)}`;
  $('perf-disk').textContent = `${formatBytes(system.disk_used)} / ${formatBytes(system.disk_total)}`;
  $('perf-load').textContent = system.load_average ? system.load_average.map((v) => v.toFixed(2)).join(' / ') : '不支持';
  $('perf-tps').textContent = tick.tps != null ? tick.tps.toFixed(1) : '--';
  $('perf-mspt').textContent = tick.mspt != null ? `${tick.mspt.toFixed(1)} ms` : '--';
  $('perf-tick-raw').innerHTML = formatMinecraftText(tick.raw || (tick.available ? '(空响应)' : 'RCON 未启用或未安装 tick 查询支持（如 Carpet）'));
}

/* ---------- resource charts ---------- */
const RANGE_SPANS = { '10m': 600, '30m': 1800, '1h': 3600, '6h': 6 * 3600, '12h': 12 * 3600, '1d': 86400, '3d': 3 * 86400, '7d': 7 * 86400 };
const CHART_POINTS = 300;

function formatRate(value) { return `${formatBytes(value)}/s`; }
function formatPercent(value) { return `${value.toFixed(0)}%`; }

function latest(values) {
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (values[i] != null && isFinite(values[i])) return values[i];
  }
  return null;
}
function legendHtml(entries) {
  return entries.map(({ label, color, value }) =>
    `<span class="key"><span class="swatch" style="background:${color}"></span>${escapeHtml(label)} <span class="now">${escapeHtml(value)}</span></span>`).join('');
}

function drawCharts() {
  const data = state.chartData;
  if (!data || !window.MWMChart) return;
  const span = RANGE_SPANS[state.chartRange] || 3600;
  const shared = { timestamps: data.t, spanSeconds: span };

  const cpuNow = latest(data.cpu);
  MWMChart.render($('chart-cpu'), {
    ...shared,
    yMax: 100,
    formatValue: formatPercent,
    series: [{ label: '总 CPU', color: 'var(--chart-1)', values: data.cpu }],
  });
  $('legend-cpu').innerHTML = legendHtml([
    { label: '总 CPU', color: 'var(--chart-1)', value: cpuNow != null ? `${cpuNow.toFixed(1)}%` : '--' },
  ]);

  const memNow = latest(data.mem_used);
  const mcMemNow = latest(data.mc_mem);
  MWMChart.render($('chart-memory'), {
    ...shared,
    formatValue: formatBytes,
    series: [
      { label: '系统已用', color: 'var(--chart-2)', values: data.mem_used },
      { label: 'Minecraft', color: 'var(--chart-1)', values: data.mc_mem },
    ],
  });
  $('legend-memory').innerHTML = legendHtml([
    { label: '系统已用', color: 'var(--chart-2)', value: memNow != null ? formatBytes(memNow) : '--' },
    { label: 'Minecraft', color: 'var(--chart-1)', value: mcMemNow != null ? formatBytes(mcMemNow) : '--' },
  ]);

  const rxNow = latest(data.net_rx);
  const txNow = latest(data.net_tx);
  MWMChart.render($('chart-network'), {
    ...shared,
    formatValue: formatRate,
    series: [
      { label: '接收', color: 'var(--chart-3)', values: data.net_rx },
      { label: '发送', color: 'var(--chart-4)', values: data.net_tx },
    ],
  });
  $('legend-network').innerHTML = legendHtml([
    { label: '接收', color: 'var(--chart-3)', value: rxNow != null ? formatRate(rxNow) : '--' },
    { label: '发送', color: 'var(--chart-4)', value: txNow != null ? formatRate(txNow) : '--' },
  ]);

  const coverage = data.first_sample_at
    ? `历史起点 ${new Date(data.first_sample_at * 1000).toLocaleString('zh-CN')}，采样间隔 1 秒；曲线按 ${Math.round(data.resolution_seconds)} 秒聚合。历史保存在内存中，插件重载或 MCDR 重启后会清空。`
    : '尚未采集到数据。';
  $('chart-coverage').textContent = coverage;
}

async function refreshCharts() {
  try {
    state.chartData = await api(`/api/metrics/history?range=${encodeURIComponent(state.chartRange)}&points=${CHART_POINTS}`);
    drawCharts();
  } catch (error) { console.warn(error); }
}

document.querySelectorAll('#range-picker button').forEach((button) => button.addEventListener('click', () => {
  state.chartRange = button.dataset.range;
  document.querySelectorAll('#range-picker button').forEach((el) => el.classList.toggle('active', el === button));
  refreshCharts();
}));
window.addEventListener('resize', () => { if (state.activeView === 'performance') drawCharts(); });

/* ---------- players roster + moderation ---------- */
const ACTION_LABELS = {
  op: '设为 OP', deop: '取消 OP', kick: '踢出', ban: '封禁', pardon: '解封',
  ban_ip: '封禁 IP', pardon_ip: '解封 IP',
  whitelist_add: '加入白名单', whitelist_remove: '移出白名单',
  whitelist_on: '开启白名单', whitelist_off: '关闭白名单', whitelist_reload: '重载白名单',
};

async function performPlayerAction(action, target, reason) {
  const label = ACTION_LABELS[action] || action;
  const subject = target ? `${label} ${target}` : label;
  const confirmed = await confirmDialog(`确认要${subject}吗？`, { title: label, confirmText: label });
  if (!confirmed) return;
  try {
    const response = await api('/api/players/action', {
      method: 'POST',
      body: JSON.stringify({ action, target: target || null, reason: reason || null }),
    });
    showToast(response.result ? `${label}：${response.result}` : `${label} 已提交`);
    refreshRoster();
  } catch (error) {
    showToast(`${label}失败：${error.message}`, { type: 'error' });
  }
}

// online first, then OP, then by name — matches how an admin scans the list
function sortRoster(players) {
  return players.slice().sort((a, b) => {
    if (a.online !== b.online) return a.online ? -1 : 1;
    if (a.op !== b.op) return a.op ? -1 : 1;
    return String(a.name || a.uuid).localeCompare(String(b.name || b.uuid), 'zh-CN');
  });
}

function rosterActionsHtml(player) {
  if (!player.name) return '<span class="na">无名称</span>';
  const name = escapeHtml(player.name);
  const buttons = [];
  buttons.push(`<button class="link-btn" data-player-action="${player.op ? 'deop' : 'op'}" data-target="${name}">${player.op ? '取消 OP' : '设为 OP'}</button>`);
  if (player.online) buttons.push(`<button class="link-btn" data-player-action="kick" data-target="${name}">踢出</button>`);
  buttons.push(`<button class="link-btn danger" data-player-action="${player.banned ? 'pardon' : 'ban'}" data-target="${name}">${player.banned ? '解封' : '封禁'}</button>`);
  buttons.push(`<button class="link-btn danger" data-player-action="ban_ip" data-target="${escapeHtml(player.ip || player.name)}">封 IP</button>`);
  buttons.push(`<button class="link-btn" data-player-action="${player.whitelisted ? 'whitelist_remove' : 'whitelist_add'}" data-target="${name}">${player.whitelisted ? '移出白名单' : '加入白名单'}</button>`);
  return `<div class="row-actions">${buttons.join('')}</div>`;
}

function renderRoster(players) {
  if (!players.length) {
    $('players-table-body').innerHTML = '<tr><td colspan="10">暂无玩家记录</td></tr>';
    return;
  }
  $('players-table-body').innerHTML = sortRoster(players).map((p) => {
    const marks = [];
    if (p.whitelisted) marks.push('<span class="tag muted">白名单</span>');
    if (p.banned) marks.push(`<span class="tag danger" title="${escapeHtml(p.ban_reason || '')}">已封禁</span>`);
    if (!p.has_played) marks.push('<span class="tag muted">未进入过</span>');
    // online but no recorded join time -> recovered after a plugin reload
    const onlineDuration = p.joined_at ? formatDuration(p.online_seconds) : '<span class="na" title="插件重载后无法得知加入时间">在线中</span>';
    const lastSeen = p.online ? '<span class="tag">在线</span>' : (p.last_seen ? formatDateTime(p.last_seen) : '<span class="na">未知</span>');
    return `
      <tr>
        <td>${p.op ? '<span class="tag op">OP</span> ' : ''}<span class="player-name">${escapeHtml(p.name || '(未知)')}</span></td>
        <td>${p.online ? '<span class="tag">在线</span>' : '<span class="tag muted">离线</span>'}</td>
        <td>${marks.join(' ') || '<span class="na">--</span>'}</td>
        <td class="mono">${escapeHtml(p.ip || (p.online ? '未知' : '--'))}</td>
        <td>${p.online ? onlineDuration : '--'}</td>
        <td>${lastSeen}</td>
        <td>${escapeHtml(p.dimension || '--')}</td>
        <td class="mono">${p.position ? p.position.join(', ') : '--'}</td>
        <td class="mono">${escapeHtml(p.uuid || '--')}</td>
        <td>${rosterActionsHtml(p)}</td>
      </tr>`;
  }).join('');
}

// One fetch feeds both the roster table and the access-control lists.
async function refreshRoster() {
  try {
    const data = await api('/api/players/roster');
    renderRoster(data.players || []);
    renderAccess(data);
  } catch (error) {
    $('players-table-body').innerHTML = `<tr><td colspan="10">读取失败：${escapeHtml(error.message)}</td></tr>`;
  }
}

function accessListHtml(entries, describe, removeAction, targetOf) {
  if (!entries.length) return '<li class="empty">暂无记录</li>';
  return entries.map((entry) => {
    const target = targetOf(entry);
    return `<li>
      <div class="access-entry">${describe(entry)}</div>
      ${target ? `<button class="link-btn danger" data-player-action="${removeAction}" data-target="${escapeHtml(target)}">移除</button>` : ''}
    </li>`;
  }).join('');
}

function renderAccess(data) {
    const enabled = !!data.whitelist_enabled;
    const toggle = $('whitelist-toggle');
    // Only sync the control when the user is not mid-interaction with it.
    if (document.activeElement !== toggle) toggle.checked = enabled;
    $('whitelist-state').textContent = `白名单：${enabled ? '已开启' : '已关闭'}`;
    $('whitelist-hint').textContent = enabled
      ? (data.whitelist_enforced ? '仅名单内玩家可进入，且已强制踢出名单外在线玩家' : '仅名单内玩家可进入服务器')
      : '当前任何玩家都可以进入服务器';

    $('access-whitelist').innerHTML = accessListHtml(
      data.whitelist || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || '(未知)')}</span><span class="access-meta mono">${escapeHtml(e.uuid || '')}</span>`,
      'whitelist_remove', (e) => e.name);
    $('access-ops').innerHTML = accessListHtml(
      data.ops || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || '(未知)')}</span><span class="access-meta">等级 ${escapeHtml(e.level ?? '-')}</span>`,
      'deop', (e) => e.name);
    $('access-bans').innerHTML = accessListHtml(
      data.banned_players || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || '(未知)')}</span><span class="access-meta">${escapeHtml(e.reason || '无理由')}</span>`,
      'pardon', (e) => e.name);
    $('access-ip-bans').innerHTML = accessListHtml(
      data.banned_ips || [],
      (e) => `<span class="player-name mono">${escapeHtml(e.ip || '')}</span><span class="access-meta">${escapeHtml(e.reason || '无理由')}</span>`,
      'pardon_ip', (e) => e.ip);
}

// Action buttons are re-rendered constantly, so listen on the document instead of per button.
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-player-action]');
  if (!button) return;
  performPlayerAction(button.dataset.playerAction, button.dataset.target || null, button.dataset.reason || null);
});
document.querySelectorAll('[data-access-action]').forEach((button) => button.addEventListener('click', () => {
  performPlayerAction(button.dataset.accessAction, null, null);
}));
$('whitelist-toggle').addEventListener('change', async (event) => {
  const wanted = event.target.checked;
  // Revert optimistically-flipped state; refreshRoster() re-syncs from the server.
  event.target.checked = !wanted;
  await performPlayerAction(wanted ? 'whitelist_on' : 'whitelist_off', null, null);
});
document.querySelectorAll('[data-add-form]').forEach((form) => form.addEventListener('submit', (event) => {
  event.preventDefault();
  const target = form.querySelector('[name="target"]').value.trim();
  const reasonField = form.querySelector('[name="reason"]');
  if (!target) return;
  performPlayerAction(form.dataset.addForm, target, reasonField ? reasonField.value.trim() : null);
  form.reset();
}));

/* ---------- server.properties editor ---------- */
const propertiesState = { entries: [], dirty: new Map() };

function propertyControl(entry) {
  const key = entry.key;
  const id = `prop-${key.replace(/[^A-Za-z0-9_-]/g, '_')}`;
  if (entry.sensitive) {
    const hint = entry.has_value ? '已设置，留空保持不变' : '未设置';
    return `<input id="${id}" class="field" type="password" data-prop="${escapeHtml(key)}" placeholder="${hint}" autocomplete="new-password" />`;
  }
  if (entry.value === 'true' || entry.value === 'false') {
    return `<label class="switch"><input id="${id}" type="checkbox" data-prop="${escapeHtml(key)}" ${entry.value === 'true' ? 'checked' : ''} /><span>${entry.value === 'true' ? '启用' : '停用'}</span></label>`;
  }
  const options = PROPERTY_OPTIONS[key];
  if (options) {
    return `<select id="${id}" class="field" data-prop="${escapeHtml(key)}">${options
      .map((o) => `<option value="${escapeHtml(o)}"${o === entry.value ? ' selected' : ''}>${escapeHtml(o)}</option>`)
      .join('')}</select>`;
  }
  if (/^-?\d+$/.test(entry.value)) {
    return `<input id="${id}" class="field" type="number" data-prop="${escapeHtml(key)}" value="${escapeHtml(entry.value)}" />`;
  }
  return `<input id="${id}" class="field" data-prop="${escapeHtml(key)}" value="${escapeHtml(entry.value)}" />`;
}

function renderProperties() {
  const filter = ($('properties-filter').value || '').trim().toLowerCase();
  const rows = propertiesState.entries.filter((entry) => {
    if (!filter) return true;
    const label = PROPERTY_LABELS[entry.key] || '';
    return entry.key.toLowerCase().includes(filter) || label.toLowerCase().includes(filter);
  });
  if (!rows.length) {
    $('properties-list').innerHTML = '<p class="hint">没有匹配的配置项。</p>';
    return;
  }
  $('properties-list').innerHTML = rows.map((entry) => `
    <div class="property-row${propertiesState.dirty.has(entry.key) ? ' dirty' : ''}">
      <div class="property-label">
        <span class="property-name">${escapeHtml(PROPERTY_LABELS[entry.key] || entry.key)}</span>
        <span class="property-key mono">${escapeHtml(entry.key)}</span>
      </div>
      <div class="property-control">${propertyControl(entry)}</div>
    </div>`).join('');
}

function markDirty(key, value) {
  const original = propertiesState.entries.find((e) => e.key === key);
  const isSensitive = original && original.sensitive;
  if (!isSensitive && original && String(original.value) === String(value)) {
    propertiesState.dirty.delete(key);
  } else if (isSensitive && value === '') {
    propertiesState.dirty.delete(key);
  } else {
    propertiesState.dirty.set(key, String(value));
  }
  $('properties-save').disabled = propertiesState.dirty.size === 0;
  $('properties-save').textContent = propertiesState.dirty.size
    ? `保存修改 (${propertiesState.dirty.size})` : '保存修改';
}

$('properties-list').addEventListener('input', (event) => {
  const field = event.target.closest('[data-prop]');
  if (!field) return;
  const value = field.type === 'checkbox' ? field.checked : field.value;
  markDirty(field.dataset.prop, value);
  if (field.type === 'checkbox') {
    const text = field.parentElement.querySelector('span');
    if (text) text.textContent = field.checked ? '启用' : '停用';
  }
});
$('properties-list').addEventListener('change', (event) => {
  const field = event.target.closest('select[data-prop]');
  if (field) markDirty(field.dataset.prop, field.value);
});
$('properties-filter').addEventListener('input', renderProperties);

$('properties-save').addEventListener('click', async () => {
  if (!propertiesState.dirty.size) return;
  const changes = Object.fromEntries(propertiesState.dirty);
  const confirmed = await confirmDialog(
    `将修改 ${Object.keys(changes).length} 项配置，需要重启服务端才会生效。确认保存吗？`,
    { title: '保存服务器配置', confirmText: '保存' });
  if (!confirmed) return;
  try {
    const result = await api('/api/server/properties', { method: 'POST', body: JSON.stringify({ changes }) });
    propertiesState.dirty.clear();
    $('properties-save').disabled = true;
    $('properties-save').textContent = '保存修改';
    const ignored = result.ignored && result.ignored.length ? `，已忽略未知项：${result.ignored.join(', ')}` : '';
    showToast(`已保存 ${result.applied.length} 项${ignored}；重启服务端后生效`);
    refreshProperties();
  } catch (error) {
    showToast(`保存失败：${error.message}`, { type: 'error' });
  }
});

async function refreshProperties() {
  try {
    const data = await api('/api/server/properties');
    propertiesState.entries = data.entries || [];
    propertiesState.dirty.clear();
    $('properties-save').disabled = true;
    $('properties-save').textContent = '保存修改';
    renderProperties();
  } catch (error) {
    $('properties-list').innerHTML = `<p class="hint">读取失败：${escapeHtml(error.message)}</p>`;
  }
}

/* ---------- plugins + mods ---------- */
function renderPlugins(plugins) {
  $('plugins-count').textContent = `${plugins.length} 个`;
  $('plugins').innerHTML = plugins.map((p) => {
    // Reloading this very plugin would stop the web server mid-request; that flow
    // only works from the MCDR console.
    const reloadButton = p.self
      ? '<span class="na">当前插件</span>'
      : `<button class="link-btn row-hover-action" data-reload-plugin="${escapeHtml(p.id)}" type="button">重新加载</button>`;
    return `
    <li class="entry-row">
      <div>
        <div class="p-name">${escapeHtml(p.name || p.id)}</div>
        <div class="p-meta">${escapeHtml(p.id)} · ${escapeHtml(p.version || 'unknown')}</div>
      </div>
      ${reloadButton}
    </li>`;
  }).join('') || '<li>无已加载插件</li>';
}

async function refreshMods() {
  try {
    const { mods } = await api('/api/mods');
    $('mods-count').textContent = `${mods.length} 个`;
    $('mods').innerHTML = mods.map((m) => `
      <li class="entry-row">
        <div>
          <div class="p-name">${escapeHtml(m.name || m.file)}</div>
          <div class="p-meta">${escapeHtml(m.id || m.file)}${m.version ? ' · ' + escapeHtml(m.version) : ''}</div>
        </div>
      </li>`).join('') || '<li>mods 目录为空</li>';
  } catch (error) {
    $('mods').innerHTML = `<li>读取失败：${escapeHtml(error.message)}</li>`;
  }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-reload-plugin]');
  if (!button) return;
  const pluginId = button.dataset.reloadPlugin;
  const confirmed = await confirmDialog(`确认要重新加载插件 ${pluginId} 吗？`, { title: '重载插件', confirmText: '重载' });
  if (!confirmed) return;
  try {
    const result = await api('/api/plugins/reload', { method: 'POST', body: JSON.stringify({ plugin_id: pluginId }) });
    showToast(result.accepted ? `插件 ${pluginId} 已重载` : `插件 ${pluginId} 重载未生效`, { type: result.accepted ? 'info' : 'error' });
    refreshOverview();
  } catch (error) {
    showToast(`重载失败：${error.message}`, { type: 'error' });
  }
});

/* ---------- player management sub-tabs ---------- */
document.querySelectorAll('#player-subtabs button').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('#player-subtabs button').forEach((el) => el.classList.toggle('active', el === button));
  document.querySelectorAll('[data-subview]').forEach((el) => el.classList.toggle('active', el.dataset.subview === button.dataset.subtab));
}));

// Seed / level name live in the overview strip, so this refreshes with the overview
// rather than only while a particular view is open.
async function refreshWorld() {
  try {
    const data = await api('/api/world');
    $('ov-seed').textContent = data.seed || '--';
    $('ov-level-name').textContent = data.level_name
      ? `${data.level_name}${data.difficulty ? ' · ' + data.difficulty : ''}`
      : '--';
  } catch (error) { console.warn(error); }
}

function switchView(view) {
  state.activeView = view;
  $('view-title').textContent = VIEW_TITLES[view] || '控制中心';
  document.querySelectorAll('.nav-item').forEach((button) => button.classList.toggle('active', button.dataset.view === view));
  document.querySelectorAll('.view').forEach((panel) => panel.classList.toggle('active', panel.dataset.viewPanel === view));
  if (view === 'players') refreshRoster();
  if (view === 'world') { refreshProperties(); refreshMods(); }
  // charts can only measure themselves once their panel is visible
  if (view === 'performance') refreshCharts();
}
document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));

/* ---------- realtime ---------- */
function setConnection(text, cls) {
  $('connection-text').textContent = text;
  $('connection').className = `badge ${cls}`;
}
function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  // The session cookie is sent automatically on the same-origin handshake.
  state.socket = new WebSocket(`${protocol}://${location.host}/ws/events`);
  state.socket.onopen = () => {
    setConnection('实时已连接', 'live');
    // The server replays its console backlog on every (re)connect; reset first so a
    // reconnect repopulates from that backlog instead of duplicating existing lines.
    consoleLines.length = 0;
    $('console').textContent = '';
  };
  state.socket.onclose = () => {
    setConnection('实时已断开', 'down');
    if (!state.loggedOut) setTimeout(connectSocket, 2000);
  };
  state.socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    if (event.type === 'console') appendConsole(event.data);
    if (event.type === 'player' || event.type === 'status') {
      refreshOverview();
      if (state.activeView === 'players') refreshRoster();
    }
  };
}
async function logout() {
  state.loggedOut = true;
  state.socket?.close();
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch { /* session may already be gone */ }
  location.href = '/';
}
$('logout').addEventListener('click', logout);

/* ---------- command suggestions (only after typing; never steal history keys) ---------- */
function renderSuggestions(items) {
  suggestionState.items = items;
  suggestionState.activeIndex = -1;
  const list = $('command-suggestions');
  if (!items.length) { list.hidden = true; list.innerHTML = ''; return; }
  list.innerHTML = items.map((value, index) => `<li data-index="${index}">${escapeHtml(value)}</li>`).join('');
  list.hidden = false;
}
function hideSuggestions() {
  suggestionState.items = [];
  suggestionState.activeIndex = -1;
  $('command-suggestions').hidden = true;
}
function highlightSuggestion(index) {
  [...$('command-suggestions').children].forEach((el, i) => el.classList.toggle('active', i === index));
  suggestionState.activeIndex = index;
}
$('command').addEventListener('input', () => {
  const value = $('command').value.trim().toLowerCase();
  if (!value) { hideSuggestions(); return; }
  renderSuggestions(commandSuggestionPool.filter((item) => item.toLowerCase().includes(value)).slice(0, 8));
});
$('command-suggestions').addEventListener('mousedown', (event) => {
  const item = event.target.closest('li');
  if (!item) return;
  event.preventDefault();
  $('command').value = suggestionState.items[Number(item.dataset.index)];
  hideSuggestions();
  $('command').focus();
});
$('command').addEventListener('blur', () => setTimeout(hideSuggestions, 150));

$('command-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  hideSuggestions();
  const command = $('command').value.trim();
  if (!command) return;
  try {
    const response = await api('/api/commands', { method: 'POST', body: JSON.stringify({ command, transport: $('transport').value }) });
    $('command-result').innerHTML = formatMinecraftText(response.result ?? '命令已提交；输出将出现在控制台。');
    state.history.push(command);
    state.historyIndex = state.history.length;
    $('command').value = '';
  } catch (error) {
    $('command-result').textContent = `发送失败：${error.message}`;
  }
});
$('command').addEventListener('keydown', (event) => {
  const hasSuggestions = suggestionState.items.length > 0;
  if (hasSuggestions && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
    event.preventDefault();
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    const count = suggestionState.items.length;
    highlightSuggestion((suggestionState.activeIndex + delta + count) % count);
    return;
  }
  if (hasSuggestions && event.key === 'Enter' && suggestionState.activeIndex >= 0) {
    event.preventDefault();
    $('command').value = suggestionState.items[suggestionState.activeIndex];
    hideSuggestions();
    return;
  }
  if (hasSuggestions && event.key === 'Escape') { hideSuggestions(); return; }
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
  if (!state.history.length) return;
  event.preventDefault();
  if (event.key === 'ArrowUp') {
    if (state.historyIndex === state.history.length) state.draft = $('command').value;
    state.historyIndex = Math.max(0, state.historyIndex - 1);
  } else {
    state.historyIndex = Math.min(state.history.length, state.historyIndex + 1);
  }
  $('command').value = state.historyIndex === state.history.length ? state.draft : state.history[state.historyIndex];
});

/* ---------- server actions ---------- */
document.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', async () => {
  const action = button.dataset.action;
  const verb = action === 'stop' ? '停止' : action === 'restart' ? '重启' : '启动';
  const confirmed = await confirmDialog(`确认要${verb}服务端吗？`, { title: `${verb}服务端`, confirmText: verb });
  if (!confirmed) return;
  try {
    await api('/api/server/actions', { method: 'POST', body: JSON.stringify({ action }) });
    refreshOverview();
  } catch (error) {
    showToast(error.message, { type: 'error' });
  }
}));

refreshOverview();
refreshWorld();
connectSocket();
setInterval(() => {
  refreshOverview();
  refreshWorld(); // seed / level name live in the always-visible overview strip
  if (state.activeView === 'players') refreshRoster();
}, 10000);
// Charts poll once a second, but only while their view is on screen. This endpoint
// reads the in-memory ring buffer, so it never touches RCON or MCDR's TaskExecutor.
setInterval(() => {
  if (state.activeView === 'performance') refreshCharts();
}, 1000);

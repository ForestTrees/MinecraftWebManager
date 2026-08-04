const state = { socket: null, loggedOut: false, history: [], historyIndex: -1, draft: '', activeView: 'console', chartRange: '1h', chartData: null, botsExpanded: false, conn: 'connecting' };
const $ = (id) => document.getElementById(id);
const T = (key, params) => (window.MWMI18N ? window.MWMI18N.t(key, params) : key);

function viewTitle(view) {
  const keys = { console: 'nav_console', players: 'nav_players', world: 'nav_world', performance: 'nav_performance' };
  return T(keys[view] || 'crumb');
}

// server.properties: zh-CN labels; keys not listed fall back to the raw key name.
const PROPERTY_LABELS_ZH = {
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
const PROPERTY_LABELS_EN = {
  'accepts-transfers': 'Accept server transfers', 'allow-flight': 'Allow flight',
  'allow-nether': 'Allow Nether', 'broadcast-console-to-ops': 'Broadcast console to OPs',
  'broadcast-rcon-to-ops': 'Broadcast RCON to OPs', 'bug-report-link': 'Bug report link',
  'chat-spam-threshold-seconds': 'Chat spam threshold (s)', 'command-spam-threshold-seconds': 'Command spam threshold (s)',
  'difficulty': 'Difficulty', 'enable-code-of-conduct': 'Enable code of conduct',
  'enable-command-block': 'Enable command blocks', 'enable-jmx-monitoring': 'Enable JMX monitoring',
  'enable-query': 'Enable query protocol', 'enable-rcon': 'Enable RCON', 'enable-status': 'Respond to server list',
  'enforce-secure-profile': 'Enforce secure chat signatures', 'enforce-whitelist': 'Enforce whitelist',
  'entity-broadcast-range-percentage': 'Entity broadcast range (%)', 'force-gamemode': 'Force default gamemode',
  'function-permission-level': 'Function permission level', 'gamemode': 'Default gamemode',
  'generate-structures': 'Generate structures', 'generator-settings': 'World generator settings',
  'hardcore': 'Hardcore', 'hide-online-players': 'Hide online players',
  'initial-disabled-packs': 'Initial disabled packs', 'initial-enabled-packs': 'Initial enabled packs',
  'level-name': 'World name', 'level-seed': 'World seed', 'level-type': 'World type',
  'log-ips': 'Log player IPs', 'max-chained-neighbor-updates': 'Max chained neighbor updates',
  'max-players': 'Max players', 'max-tick-time': 'Max tick time (ms)',
  'max-world-size': 'Max world radius', 'motd': 'Server MOTD',
  'network-compression-threshold': 'Network compression threshold', 'online-mode': 'Online mode',
  'op-permission-level': 'OP permission level', 'pause-when-empty-seconds': 'Pause when empty (s)',
  'player-idle-timeout': 'Player idle timeout (min)', 'prevent-proxy-connections': 'Prevent proxy connections',
  'pvp': 'Allow PvP', 'query.port': 'Query port', 'rate-limit': 'Packet rate limit',
  'rcon.password': 'RCON password', 'rcon.port': 'RCON port',
  'region-file-compression': 'Region file compression', 'require-resource-pack': 'Require resource pack',
  'resource-pack': 'Resource pack URL', 'resource-pack-id': 'Resource pack ID',
  'resource-pack-prompt': 'Resource pack prompt', 'resource-pack-sha1': 'Resource pack SHA1',
  'server-ip': 'Listen IP', 'server-port': 'Server port', 'simulation-distance': 'Simulation distance',
  'spawn-monsters': 'Spawn monsters', 'spawn-protection': 'Spawn protection radius',
  'status-heartbeat-interval': 'Status heartbeat interval', 'sync-chunk-writes': 'Sync chunk writes',
  'text-filtering-config': 'Text filtering config', 'text-filtering-version': 'Text filtering version',
  'use-native-transport': 'Use native transport', 'view-distance': 'View distance (chunks)',
  'white-list': 'Enable whitelist',
  'management-server-enabled': 'Enable management server', 'management-server-host': 'Management server host',
  'management-server-port': 'Management server port', 'management-server-secret': 'Management server secret',
  'management-server-tls-enabled': 'Management server TLS',
  'management-server-tls-keystore': 'Management server TLS keystore',
  'management-server-tls-keystore-password': 'Management server TLS keystore password',
  'management-server-allowed-origins': 'Management server allowed origins',
};
function propertyLabel(key) {
  const dict = (MWMI18N.getLang() === 'zh' ? PROPERTY_LABELS_ZH : PROPERTY_LABELS_EN);
  return dict[key] || key;
}
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
  return h > 0 ? T('dur_hms', { h, m, s }) : m > 0 ? T('dur_ms', { m, s }) : T('dur_s', { s });
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

function confirmDialog(message, { title = T('confirm_title'), confirmText = T('confirm_ok') } = {}) {
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
  $('ov-server-state-text').textContent = data.running ? (data.startup ? T('ov_state_running') : T('ov_state_starting')) : T('ov_state_stopped');
  $('ov-server-version').textContent = data.minecraft_version || T('ov_version_unknown');
  $('ov-player-count').textContent = data.player_count;
  $('ov-player-list').textContent = data.players.join(', ') || T('ov_no_players');
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
  $('ov-tps-hint').textContent = tick.available ? T('ov_tps_from_rcon') : T('ov_tps_unavailable');
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
  $('perf-load').textContent = system.load_average ? system.load_average.map((v) => v.toFixed(2)).join(' / ') : T('perf_unsupported');
  $('perf-tps').textContent = tick.tps != null ? tick.tps.toFixed(1) : '--';
  $('perf-mspt').textContent = tick.mspt != null ? `${tick.mspt.toFixed(1)} ms` : '--';
  $('perf-tick-raw').innerHTML = formatMinecraftText(tick.raw || (tick.available ? T('tick_empty') : T('tick_unavailable')));
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
    series: [{ label: T('chart_cpu_total'), color: 'var(--chart-1)', values: data.cpu }],
  });
  $('legend-cpu').innerHTML = legendHtml([
    { label: T('chart_cpu_total'), color: 'var(--chart-1)', value: cpuNow != null ? `${cpuNow.toFixed(1)}%` : '--' },
  ]);

  const memNow = latest(data.mem_used);
  const mcMemNow = latest(data.mc_mem);
  MWMChart.render($('chart-memory'), {
    ...shared,
    formatValue: formatBytes,
    series: [
      { label: T('chart_mem_system'), color: 'var(--chart-2)', values: data.mem_used },
      { label: T('chart_mem_mc'), color: 'var(--chart-1)', values: data.mc_mem },
    ],
  });
  $('legend-memory').innerHTML = legendHtml([
    { label: T('chart_mem_system'), color: 'var(--chart-2)', value: memNow != null ? formatBytes(memNow) : '--' },
    { label: T('chart_mem_mc'), color: 'var(--chart-1)', value: mcMemNow != null ? formatBytes(mcMemNow) : '--' },
  ]);

  const rxNow = latest(data.net_rx);
  const txNow = latest(data.net_tx);
  MWMChart.render($('chart-network'), {
    ...shared,
    formatValue: formatRate,
    series: [
      { label: T('chart_rx'), color: 'var(--chart-3)', values: data.net_rx },
      { label: T('chart_tx'), color: 'var(--chart-4)', values: data.net_tx },
    ],
  });
  $('legend-network').innerHTML = legendHtml([
    { label: T('chart_rx'), color: 'var(--chart-3)', value: rxNow != null ? formatRate(rxNow) : '--' },
    { label: T('chart_tx'), color: 'var(--chart-4)', value: txNow != null ? formatRate(txNow) : '--' },
  ]);

  const locale = MWMI18N.getLang() === 'zh' ? 'zh-CN' : 'en-US';
  const coverage = data.first_sample_at
    ? T('coverage_yes', { date: new Date(data.first_sample_at * 1000).toLocaleString(locale), n: Math.round(data.resolution_seconds) })
    : T('coverage_no');
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
const ACTION_LABEL_KEYS = {
  op: 'action_op', deop: 'action_deop', kick: 'action_kick', ban: 'action_ban', pardon: 'action_pardon',
  ban_ip: 'action_ban_ip', pardon_ip: 'action_pardon_ip',
  whitelist_add: 'action_whitelist_add', whitelist_remove: 'action_whitelist_remove',
  whitelist_on: 'action_whitelist_on', whitelist_off: 'action_whitelist_off', whitelist_reload: 'action_whitelist_reload',
};
function actionLabel(action) {
  return T(ACTION_LABEL_KEYS[action] || '') || action;
}

async function performPlayerAction(action, target, reason) {
  const label = actionLabel(action);
  const subject = target ? `${label} ${target}` : label;
  const confirmed = await confirmDialog(T('confirm_action', { subject }), { title: label, confirmText: label });
  if (!confirmed) return;
  try {
    const response = await api('/api/players/action', {
      method: 'POST',
      body: JSON.stringify({ action, target: target || null, reason: reason || null }),
    });
    showToast(response.result ? T('toast_result', { label, result: response.result }) : T('toast_submitted', { label }));
    refreshRoster();
  } catch (error) {
    showToast(T('toast_failed', { label, error: error.message }), { type: 'error' });
  }
}

// online first, then OP, then by name — matches how an admin scans the list
function sortRoster(players) {
  return players.slice().sort((a, b) => {
    if (a.online !== b.online) return a.online ? -1 : 1;
    if (a.op !== b.op) return a.op ? -1 : 1;
    return String(a.name || a.uuid).localeCompare(String(b.name || b.uuid), MWMI18N.getLang() === 'zh' ? 'zh-CN' : 'en');
  });
}

function rosterActionsHtml(player, isBot = false) {
  if (!player.name) return `<span class="na">${T('roster_no_name')}</span>`;
  const name = escapeHtml(player.name);
  const buttons = [];
  if (!isBot) {
    // Bots are not real accounts: OP / whitelist management is meaningless for them.
    buttons.push(`<button class="link-btn" data-player-action="${player.op ? 'deop' : 'op'}" data-target="${name}">${player.op ? T('action_deop') : T('action_op')}</button>`);
    buttons.push(`<button class="link-btn" data-player-action="${player.whitelisted ? 'whitelist_remove' : 'whitelist_add'}" data-target="${name}">${player.whitelisted ? T('action_whitelist_remove') : T('action_whitelist_add')}</button>`);
  }
  if (player.online) buttons.push(`<button class="link-btn" data-player-action="kick" data-target="${name}">${T('action_kick')}</button>`);
  buttons.push(`<button class="link-btn danger" data-player-action="${player.banned ? 'pardon' : 'ban'}" data-target="${name}">${player.banned ? T('action_pardon') : T('action_ban')}</button>`);
  buttons.push(`<button class="link-btn danger" data-player-action="ban_ip" data-target="${escapeHtml(player.ip || player.name)}">${T('action_ban_ip')}</button>`);
  if (!isBot) {
    buttons.push(`<button class="link-btn" data-bot-flag="1" data-target="${name}">${T('mark_bot')}</button>`);
  } else {
    buttons.push(`<button class="link-btn" data-bot-flag="0" data-target="${name}">${T('unmark_bot')}</button>`);
  }
  return `<div class="row-actions">${buttons.join('')}</div>`;
}

function botSourceHint(p) {
  return p.bot_source === 'manual' ? T('bot_source_manual') : (p.bot_source === 'pattern' ? T('bot_source_pattern') : T('bot_source_uuid'));
}

function rosterRowHtml(p, isBot) {
  const marks = [];
  if (p.whitelisted) marks.push(`<span class="tag muted">${T('tag_whitelisted')}</span>`);
  if (p.banned) marks.push(`<span class="tag danger" title="${escapeHtml(p.ban_reason || '')}">${T('tag_banned')}</span>`);
  if (!p.has_played) marks.push(`<span class="tag muted">${T('tag_never_played')}</span>`);
  // online but no recorded join time -> recovered after a plugin reload
  const onlineDuration = p.joined_at ? formatDuration(p.online_seconds) : `<span class="na" title="${T('title_join_unknown')}">${T('online_recovered')}</span>`;
  const lastSeen = p.online ? `<span class="tag">${T('status_online')}</span>` : (p.last_seen ? formatDateTime(p.last_seen) : `<span class="na">${T('unknown')}</span>`);
  // The bot table shows the detection source as a hover hint instead of a tags column.
  const nameTitle = isBot && p.bot_source ? ` title="${escapeHtml(botSourceHint(p))}"` : '';
  const playerCell = `<td>${p.op ? '<span class="tag op">OP</span> ' : ''}<span class="player-name"${nameTitle}>${escapeHtml(p.name || T('unknown_name'))}</span></td>`;
  const statusCell = `<td>${p.online ? `<span class="tag">${T('status_online')}</span>` : `<span class="tag muted">${T('status_offline')}</span>`}</td>`;
  const sessionCell = `<td>${p.online ? onlineDuration : '--'}</td>`;
  const lastSeenCell = `<td>${lastSeen}</td>`;
  const dimensionCell = `<td>${escapeHtml(p.dimension || '--')}</td>`;
  const positionCell = `<td class="mono">${p.position ? p.position.join(', ') : '--'}</td>`;
  const uuidCell = `<td class="mono">${escapeHtml(p.uuid || '--')}</td>`;
  const actionsCell = `<td>${rosterActionsHtml(p, isBot)}</td>`;
  if (isBot) {
    // Bot table deliberately omits IP and tags columns.
    return `<tr>${playerCell}${statusCell}${sessionCell}${lastSeenCell}${dimensionCell}${positionCell}${uuidCell}${actionsCell}</tr>`;
  }
  const tagsCell = `<td>${marks.join(' ') || '<span class="na">--</span>'}</td>`;
  const ipCell = `<td class="mono">${escapeHtml(p.ip || (p.online ? T('unknown') : '--'))}</td>`;
  return `<tr>${playerCell}${statusCell}${tagsCell}${ipCell}${sessionCell}${lastSeenCell}${dimensionCell}${positionCell}${uuidCell}${actionsCell}</tr>`;
}

function renderRoster(players) {
  const bots = players.filter((p) => p.is_bot);
  const humans = players.filter((p) => !p.is_bot);
  const emptyRow = `<tr><td colspan="10">${T('roster_no_players')}</td></tr>`;
  if (!players.length) {
    $('players-table-body').innerHTML = emptyRow;
    $('bots-section-head').hidden = true;
    $('bots-table-scroll').hidden = true;
    $('players-bots-body').innerHTML = '';
    return;
  }
  $('players-table-body').innerHTML = humans.length
    ? sortRoster(humans).map((p) => rosterRowHtml(p, false)).join('')
    : emptyRow;
  if (bots.length) {
    $('bots-section-head').hidden = false;
    $('bots-count').textContent = bots.length;
    $('players-bots-body').innerHTML = sortRoster(bots).map((p) => rosterRowHtml(p, true)).join('');
    $('bots-table-scroll').hidden = !state.botsExpanded;
    $('bots-chevron').textContent = state.botsExpanded ? '▾' : '▸';
    $('bots-toggle-hint').textContent = state.botsExpanded ? T('bots_collapse') : T('bots_expand');
  } else {
    $('bots-section-head').hidden = true;
    $('bots-table-scroll').hidden = true;
    $('players-bots-body').innerHTML = '';
  }
}

// One fetch feeds both the roster table and the access-control lists.
async function refreshRoster() {
  try {
    const data = await api('/api/players/roster');
    renderRoster(data.players || []);
    renderAccess(data);
  } catch (error) {
    $('players-table-body').innerHTML = `<tr><td colspan="10">${escapeHtml(T('load_failed', { error: error.message }))}</td></tr>`;
    $('bots-section-head').hidden = true;
    $('bots-table-scroll').hidden = true;
    $('players-bots-body').innerHTML = '';
  }
}

function accessListHtml(entries, describe, removeAction, targetOf) {
  if (!entries.length) return `<li class="empty">${T('no_entries')}</li>`;
  return entries.map((entry) => {
    const target = targetOf(entry);
    return `<li>
      <div class="access-entry">${describe(entry)}</div>
      ${target ? `<button class="link-btn danger" data-player-action="${removeAction}" data-target="${escapeHtml(target)}">${T('remove')}</button>` : ''}
    </li>`;
  }).join('');
}

function renderAccess(data) {
    const enabled = !!data.whitelist_enabled;
    const toggle = $('whitelist-toggle');
    // Only sync the control when the user is not mid-interaction with it.
    if (document.activeElement !== toggle) toggle.checked = enabled;
    $('whitelist-state').textContent = enabled ? T('whitelist_enabled') : T('whitelist_disabled');
    $('whitelist-hint').textContent = enabled
      ? (data.whitelist_enforced ? T('wl_enforced_hint') : T('wl_plain_hint'))
      : T('wl_off_hint');

    $('access-whitelist').innerHTML = accessListHtml(
      data.whitelist || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || T('unknown_name'))}</span><span class="access-meta mono">${escapeHtml(e.uuid || '')}</span>`,
      'whitelist_remove', (e) => e.name);
    $('access-ops').innerHTML = accessListHtml(
      data.ops || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || T('unknown_name'))}</span><span class="access-meta">${escapeHtml(T('level_prefix', { level: e.level ?? '-' }))}</span>`,
      'deop', (e) => e.name);
    $('access-bans').innerHTML = accessListHtml(
      data.banned_players || [],
      (e) => `<span class="player-name">${escapeHtml(e.name || T('unknown_name'))}</span><span class="access-meta">${escapeHtml(e.reason || T('no_reason'))}</span>`,
      'pardon', (e) => e.name);
    $('access-ip-bans').innerHTML = accessListHtml(
      data.banned_ips || [],
      (e) => `<span class="player-name mono">${escapeHtml(e.ip || '')}</span><span class="access-meta">${escapeHtml(e.reason || T('no_reason'))}</span>`,
      'pardon_ip', (e) => e.ip);
}

// Action buttons are re-rendered constantly, so listen on the document instead of per button.
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-player-action]');
  if (!button) return;
  performPlayerAction(button.dataset.playerAction, button.dataset.target || null, button.dataset.reason || null);
});
document.addEventListener('click', (event) => {
  if (event.target.closest('#bots-toggle')) {
    state.botsExpanded = !state.botsExpanded;
    $('bots-table-scroll').hidden = !state.botsExpanded;
    $('bots-chevron').textContent = state.botsExpanded ? '▾' : '▸';
    $('bots-toggle-hint').textContent = state.botsExpanded ? T('bots_collapse') : T('bots_expand');
  }
  const flagButton = event.target.closest('[data-bot-flag]');
  if (flagButton) setBotFlag(flagButton.dataset.target || '', flagButton.dataset.botFlag === '1');
});
async function setBotFlag(target, isBot) {
  const verb = isBot ? T('mark_bot') : T('unmark_bot_verb');
  const note = isBot ? T('bot_flag_confirm_yes') : T('bot_flag_confirm_no');
  const confirmed = await confirmDialog(
    `${T('bot_flag_confirm', { verb, target })}${note}`,
    { title: verb, confirmText: verb }
  );
  if (!confirmed) return;
  try {
    await api('/api/players/bot', { method: 'POST', body: JSON.stringify({ name: target, is_bot: isBot }) });
    showToast(T('bot_flag_done', { verb, target }));
    refreshRoster();
  } catch (error) {
    showToast(T('bot_flag_failed', { verb, error: error.message }), { type: 'error' });
  }
}
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
    const hint = entry.has_value ? T('prop_set_hint') : T('prop_not_set');
    return `<input id="${id}" class="field" type="password" data-prop="${escapeHtml(key)}" placeholder="${hint}" autocomplete="new-password" />`;
  }
  if (entry.value === 'true' || entry.value === 'false') {
    return `<label class="switch"><input id="${id}" type="checkbox" data-prop="${escapeHtml(key)}" ${entry.value === 'true' ? 'checked' : ''} /><span>${entry.value === 'true' ? T('prop_on') : T('prop_off')}</span></label>`;
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
    const label = propertyLabel(entry.key) || '';
    return entry.key.toLowerCase().includes(filter) || label.toLowerCase().includes(filter);
  });
  if (!rows.length) {
    $('properties-list').innerHTML = `<p class="hint">${T('prop_none')}</p>`;
    return;
  }
  $('properties-list').innerHTML = rows.map((entry) => `
    <div class="property-row${propertiesState.dirty.has(entry.key) ? ' dirty' : ''}">
      <div class="property-label">
        <span class="property-name">${escapeHtml(propertyLabel(entry.key))}</span>
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
    ? T('save_count', { n: propertiesState.dirty.size }) : T('save_changes');
}

$('properties-list').addEventListener('input', (event) => {
  const field = event.target.closest('[data-prop]');
  if (!field) return;
  const value = field.type === 'checkbox' ? field.checked : field.value;
  markDirty(field.dataset.prop, value);
  if (field.type === 'checkbox') {
    const text = field.parentElement.querySelector('span');
    if (text) text.textContent = field.checked ? T('prop_on') : T('prop_off');
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
    T('save_confirm', { n: Object.keys(changes).length }),
    { title: T('save_title'), confirmText: T('save') });
  if (!confirmed) return;
  try {
    const result = await api('/api/server/properties', { method: 'POST', body: JSON.stringify({ changes }) });
    propertiesState.dirty.clear();
    $('properties-save').disabled = true;
    $('properties-save').textContent = T('save_changes');
    const ignored = result.ignored && result.ignored.length ? T('saved_ignored', { list: result.ignored.join(', ') }) : '';
    showToast(T('saved_ok', { n: result.applied.length, ignored }));
    refreshProperties();
  } catch (error) {
    showToast(T('save_failed', { error: error.message }), { type: 'error' });
  }
});

async function refreshProperties() {
  try {
    const data = await api('/api/server/properties');
    propertiesState.entries = data.entries || [];
    propertiesState.dirty.clear();
    $('properties-save').disabled = true;
    $('properties-save').textContent = T('save_changes');
    renderProperties();
  } catch (error) {
    $('properties-list').innerHTML = `<p class="hint">${escapeHtml(T('load_failed', { error: error.message }))}</p>`;
  }
}

/* ---------- plugins + mods ---------- */
function renderPlugins(plugins) {
  $('plugins-count').textContent = T('plugins_count', { n: plugins.length });
  $('plugins').innerHTML = plugins.map((p) => {
    // Reloading this very plugin would stop the web server mid-request; that flow
    // only works from the MCDR console.
    const reloadButton = p.self
      ? `<span class="na">${T('current_plugin')}</span>`
      : `<button class="link-btn row-hover-action" data-reload-plugin="${escapeHtml(p.id)}" type="button">${T('reload')}</button>`;
    return `
    <li class="entry-row">
      <div>
        <div class="p-name">${escapeHtml(p.name || p.id)}</div>
        <div class="p-meta">${escapeHtml(p.id)} · ${escapeHtml(p.version || 'unknown')}</div>
      </div>
      ${reloadButton}
    </li>`;
  }).join('') || `<li>${T('no_plugins')}</li>`;
}

async function refreshMods() {
  try {
    const { mods } = await api('/api/mods');
    $('mods-count').textContent = T('plugins_count', { n: mods.length });
    $('mods').innerHTML = mods.map((m) => `
      <li class="entry-row">
        <div>
          <div class="p-name">${escapeHtml(m.name || m.file)}</div>
          <div class="p-meta">${escapeHtml(m.id || m.file)}${m.version ? ' · ' + escapeHtml(m.version) : ''}</div>
        </div>
      </li>`).join('') || `<li>${T('no_mods')}</li>`;
  } catch (error) {
    $('mods').innerHTML = `<li>${escapeHtml(T('load_failed', { error: error.message }))}</li>`;
  }
}

document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-reload-plugin]');
  if (!button) return;
  const pluginId = button.dataset.reloadPlugin;
  const confirmed = await confirmDialog(T('reload_plugin_confirm', { id: pluginId }), { title: T('reload_plugin_title'), confirmText: T('reload') });
  if (!confirmed) return;
  try {
    const result = await api('/api/plugins/reload', { method: 'POST', body: JSON.stringify({ plugin_id: pluginId }) });
    showToast(result.accepted ? T('plugin_reloaded', { id: pluginId }) : T('plugin_reload_noop', { id: pluginId }), { type: result.accepted ? 'info' : 'error' });
    refreshOverview();
  } catch (error) {
    showToast(T('reload_failed', { error: error.message }), { type: 'error' });
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
  $('view-title').textContent = viewTitle(view);
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
function refreshConnectionText() {
  const cls = state.conn === 'live' ? 'live' : state.conn === 'down' ? 'down' : '';
  setConnection(T(`conn_${state.conn}`), cls);
}
function connectSocket() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  // The session cookie is sent automatically on the same-origin handshake.
  state.socket = new WebSocket(`${protocol}://${location.host}/ws/events`);
  state.socket.onopen = () => {
    state.conn = 'live';
    refreshConnectionText();
    // The server replays its console backlog on every (re)connect; reset first so a
    // reconnect repopulates from that backlog instead of duplicating existing lines.
    consoleLines.length = 0;
    $('console').textContent = '';
  };
  state.socket.onclose = () => {
    state.conn = 'down';
    refreshConnectionText();
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
    $('command-result').innerHTML = formatMinecraftText(response.result ?? T('cmd_submitted'));
    state.history.push(command);
    state.historyIndex = state.history.length;
    $('command').value = '';
  } catch (error) {
    $('command-result').textContent = T('cmd_failed', { error: error.message });
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
  const verb = action === 'stop' ? T('action_stop') : action === 'restart' ? T('action_restart') : T('action_start');
  const confirmed = await confirmDialog(T('action_confirm', { verb }), { title: T('action_title', { verb }), confirmText: verb });
  if (!confirmed) return;
  try {
    await api('/api/server/actions', { method: 'POST', body: JSON.stringify({ action }) });
    refreshOverview();
  } catch (error) {
    showToast(error.message, { type: 'error' });
  }
}));

$('view-title').textContent = viewTitle('console');
refreshConnectionText();
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

// Re-render everything when the language is switched so generated strings follow.
document.addEventListener('mwm:langchange', () => {
  $('view-title').textContent = viewTitle(state.activeView);
  refreshConnectionText();
  refreshOverview();
  refreshWorld();
  if (state.activeView === 'players') refreshRoster();
  if (state.activeView === 'world') { refreshProperties(); refreshMods(); }
  if (state.activeView === 'performance') refreshCharts();
});

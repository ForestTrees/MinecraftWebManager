# Minecraft Web Manager

**English** | [中文](README.md)

An [MCDReforged](https://github.com/Fallen-Breath/MCDReforged) plugin that serves a password-protected web dashboard for your Minecraft server: a live console, player management, an online `server.properties` editor and resource usage charts.

The dashboard is hosted by the plugin itself — no separate web server, no CDN, no frontend build step.

- **Version**: 0.1.0
- **Requires**: MCDReforged `>=2.15.0`, Python 3.10+
- **Python packages**: `fastapi`, `uvicorn[standard]`, `psutil`

> Note: the dashboard UI is currently available in Chinese only.

---

## Features

### Live console

- Server output and in-game chat streamed over WebSocket; the last 1000 lines are replayed on (re)connect
- Send both Minecraft commands and MCDR commands (`!!` prefix) — replies to things like `!!MCDR status` show up in the web console too
- Choose the command channel: **console** (writes to the server's stdin) or **RCON** (returns the server's reply text)
- `↑` / `↓` browse command history; suggestions appear as you type
- One-click start / stop / restart of the server
- Always-visible overview strip: run state, player count, TPS/MSPT, uptime, Minecraft and MCDR versions, world name and seed

### Player management

- **Roster**: every player that has ever joined, merged from `usercache.json`, `ops.json`, `whitelist.json`, the ban lists and player save files — showing online state, IP, session length, last seen, dimension, coordinates and UUID. Online players and operators are pinned to the top
- Per-player actions: op / deop, kick, ban, ban IP, add to / remove from whitelist
- **Whitelist**: toggle enforcement, reload the list, add and remove entries
- **Operators**: view, grant and revoke OP
- **Bans**: ban and pardon players and IPs, with an optional reason

### World

- **Server settings**: view and edit `server.properties` in the browser. Enumerated settings (difficulty, gamemode, …) render as dropdowns and the list is filterable. Saving rewrites only the keys you changed — comments and ordering are preserved
- **Loaded plugins**: all MCDR plugins with versions, each individually reloadable
- **Loaded mods**: Fabric mods in the server's `mods/` folder, named from their `fabric.mod.json`

### Server status

- TPS, MSPT, swap, disk and system load at a glance
- Line charts for CPU usage, memory usage and live network throughput, over 10m / 30m / 1h / 6h / 12h / 1d / 3d / 7d
- Whole-host and Minecraft-process series are plotted separately. Samples are kept at 1-second resolution for the last hour and as 1-minute averages for the last 7 days

### Other

- Light / dark / follow-system themes, remembered across visits
- Responsive layout that works on a phone browser

---

## Installation

### 1. Drop in the plugin

Place this repository under MCDR's `plugins/` directory:

```
MCDR root/
├── plugins/
│   └── MinecraftWebManager/
│       ├── mcdreforged.plugin.json
│       └── minecraft_web_manager/
├── server/
└── config.yml
```

Alternatively, zip `mcdreforged.plugin.json` together with `minecraft_web_manager/`, rename the archive to `.mcdr` and drop that into `plugins/`. `mcdreforged.plugin.json` must sit at the root level of the archive.

### 2. Install the Python dependencies

```bash
pip install -r requirements.txt
```

Make sure you are using the same Python environment that runs MCDR — activate its virtualenv first if it has one.

### 3. Load the plugin

In the MCDR console:

```
!!MCDR reload plugin minecraft_web_manager
```

### 4. Grab the bootstrap password

On first load the plugin generates a one-time password and prints it to the MCDR log at `WARNING` level:

```
[Minecraft Web Manager] Minecraft Web Manager bootstrap password: xxxxxxxxxxxxxxxxxxxxxxxx
```

**Save it right away** — this is the only time it appears in plain text. Then open:

```
http://127.0.0.1:8088
```

The default username is `admin`; the password is the string above.

---

## Configuration

The config file lives at `config/minecraft_web_manager/config.json` inside MCDR's working directory. **Reload the plugin for changes to take effect.**

| Key | Default | Description |
| --- | --- | --- |
| `host` | `127.0.0.1` | Listen address. Local-only by default; set `0.0.0.0` to reach it from other machines |
| `port` | `8088` | Listen port |
| `username` | `admin` | Login username |
| `password.salt` / `password.hash` | generated | PBKDF2 salt and hash. The password itself is never stored |
| `token_secret` | generated | Signing key for login tokens. Clearing it invalidates every active session immediately |

Login tokens are valid for 8 hours and live in the browser's `sessionStorage`, so closing the tab ends the session.

### Forgot the password

Set both `password.salt` and `password.hash` to empty strings:

```json
"password": { "salt": "", "hash": "" }
```

Save, then run `!!MCDR reload plugin minecraft_web_manager` — a fresh one-time password is printed to the MCDR log again.

---

## About RCON

Most features work without RCON, but these depend on it:

- TPS / MSPT readings (via `tick query`)
- Coordinates and dimension in the player roster
- Seeing command reply text — only the RCON channel returns it
- Recovering the online-player list after a plugin reload

Enabling it requires configuring **both sides** with a matching port and password:

- Minecraft side: `enable-rcon`, `rcon.port` and `rcon.password` in `server/server.properties`
- MCDR side: the `rcon` section of MCDR's `config.yml`

The three Minecraft-side settings can be edited right from the dashboard under **World → 服务器配置** (server settings); the server must be restarted afterwards.

---

## Security notes

The dashboard has full control over your server — arbitrary commands, bans, config changes — so expose it carefully:

- It **listens on `127.0.0.1` by default**. Keeping that and connecting through an SSH tunnel is the safest way to reach it remotely
- If you must expose it publicly, put it behind a reverse proxy such as Nginx or Caddy with HTTPS enabled. The plugin **does not provide TLS**; over plain HTTP your password and token travel in the clear
- A reverse proxy must forward WebSocket upgrades, otherwise the live console cannot connect:

  ```nginx
  location /ws/ {
      proxy_pass http://127.0.0.1:8088;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
  }
  ```

- Sensitive settings such as `rcon.password` are shown blank and never sent to the browser; submitting an empty value leaves them unchanged

---

## Known limitations

- **Resource history is kept in memory**, so it resets to empty whenever MCDR restarts or the plugin is reloaded
- **World seed, name and difficulty are read from save files**, which only update when the server writes them to disk — they can lag reality by minutes
- **Player IPs and UUIDs are parsed from server output**, so unusual log formats may prevent capture. Players recovered after a plugin reload have no join time or IP
- **Only Fabric mods are identified** (via `fabric.mod.json`); Forge / NeoForge mods are listed by filename only
- **Ping is unavailable on vanilla servers** and therefore not shown
- The dashboard UI is currently Chinese-only

---

## Feedback

Issues and suggestions are welcome at [Issues](https://github.com/ForestTrees/MinecraftWebManager/issues).

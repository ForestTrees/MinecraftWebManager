# Minecraft Web Manager

[English](README_en.md) | **中文**

一个 [MCDReforged](https://github.com/Fallen-Breath/MCDReforged) 插件，为 Minecraft 服务器提供带登录鉴权的网页管理面板：实时控制台、玩家管理、`server.properties` 在线编辑与资源占用图表。

面板由插件自身托管，无需另外部署 Web 服务器，也不依赖任何 CDN 或前端构建工具。

- **版本**：1.0.0
- **依赖**：MCDReforged `>=2.15.0`、Python 3.10+
- **Python 库**：`fastapi`、`uvicorn[standard]`、`psutil`

---

## 功能

### 实时控制台

- 通过 WebSocket 实时推送服务器输出与玩家聊天，重连后自动补全最近 1000 行历史
- 支持发送 Minecraft 命令与 MCDR 命令（`!!` 开头），`!!MCDR status` 之类的回复也会显示在网页控制台里
- 命令通道可切换 **console**（写入服务端标准输入）或 **RCON**（能拿到服务器的回复文本）
- 输入框支持 `↑` / `↓` 翻查历史命令、输入时给出命令补全建议
- 一键 启动 / 停止 / 重启 服务器
- 顶部常驻概览：运行状态、在线人数、TPS/MSPT、运行时长、Minecraft 与 MCDR 版本、世界名与种子

### 玩家管理

- **玩家列表**：汇总所有进过本服的玩家（来自 `usercache.json`、`ops.json`、`whitelist.json`、封禁名单与玩家存档），显示在线状态、IP、在线时长、最后在线时间、所在维度与坐标、UUID；在线玩家与 OP 置顶。识别出的 Carpet 假人（bot）单独放在「假人管理」表格中（仍在玩家管理页签下），可折叠、可行内手动标记/取消标记；假人表精简为玩家、状态、在线时长、最后在线、维度、坐标、UUID 与操作（不含 IP/标记列）
- 对单个玩家直接执行：设为 / 取消 OP、踢出、封禁、封禁 IP、加入 / 移出白名单
- **白名单**：开关白名单、重载名单、增删成员
- **管理员**：查看与增删 OP
- **封禁**：封禁 / 解封玩家与 IP，可填写理由

### World

- **服务器配置**：在线查看与修改 `server.properties`，配置项带中文/英文说明并以响应式卡片网格展示，枚举项（难度、游戏模式等）以下拉框呈现，支持关键字筛选，工具栏显示配置总数与已修改数量；保存时只改动你修改过的项，注释与顺序原样保留，已修改卡片高亮
- **已加载插件 / 已加载 Mod**：常驻在页面右侧栏（无需滚动到底部），插件可单独重载，Mod 读取服务端 `mods/` 目录下 Fabric mod 的 `fabric.mod.json` 显示名称与版本

### 服务器状态

- TPS、MSPT、Swap、磁盘、系统负载概览
- CPU 占用、内存占用、网络实时速度三张折线图，时间范围可选 10m / 30m / 1h / 6h / 12h / 1d / 3d / 7d
- 区分「整机」与「Minecraft 进程」两条曲线，1 秒精度采样保留最近 1 小时，1 分钟均值保留最近 7 天

### 其它

- 浅色 / 暗黑 / 跟随系统三种配色，自动记忆
- 界面语言自动跟随浏览器（简体 / 繁体中文 → 中文，其余 → 英文），也可在页面右上角 / 侧边栏手动切换并记忆
- 响应式布局，手机浏览器可用

---

## 安装

### 1. 放置插件

把本仓库放进 MCDR 的 `plugins/` 目录，形成如下结构：

```
MCDR 根目录/
├── plugins/
│   └── MinecraftWebManager/
│       ├── mcdreforged.plugin.json
│       └── minecraft_web_manager/
├── server/
└── config.yml
```

也可以把 `mcdreforged.plugin.json` 与 `minecraft_web_manager/` 一起打包成 zip、后缀改为 `.mcdr`，直接放进 `plugins/`。注意 `mcdreforged.plugin.json` 必须位于压缩包的根层级。

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

请确保使用的是运行 MCDR 的那个 Python 环境。若 MCDR 装在虚拟环境里，先激活它再执行上面的命令。

### 3. 加载插件

在 MCDR 控制台执行：

```
!!MCDR reload plugin minecraft_web_manager
```

### 4. 取得初始密码

首次加载时插件会生成一次性密码并打印到 MCDR 日志（`WARNING` 级别）：

```
[Minecraft Web Manager] Minecraft Web Manager bootstrap password: xxxxxxxxxxxxxxxxxxxxxxxx
```

**请立刻保存这串密码**，它只会明文出现这一次。随后用浏览器访问：

```
http://127.0.0.1:8088
```

默认用户名 `admin`，密码即上面那串。

---

## 配置

配置文件位于 MCDR 工作目录下的 `config/minecraft_web_manager/config.json`，**修改后需要重载插件才会生效**。

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1` | 面板监听地址。默认仅本机可访问，改成 `0.0.0.0` 才能从其它机器打开 |
| `port` | `8088` | 面板监听端口 |
| `username` | `admin` | 登录用户名 |
| `password.salt` / `password.hash` | 自动生成 | 密码的 PBKDF2 盐值与哈希，密码本身不会存盘 |
| `token_secret` | 自动生成 | 登录令牌的签名密钥，清空它会让所有已登录会话立即失效 |
| `token_ttl_seconds` | `2592000`（30 天） | 登录会话有效期（秒）；活跃使用时到期前会自动续期 |
| `bot_names` | `[]` | 手动标记为假人的玩家名列表（小写）；自动识别之外的手动兜底 |
| `not_bot_names` | `[]` | 反向名单（小写）：即使匹配名称规则或离线 UUID 也强制视为真人；行内「取消标记」会自动写入 |
| `bot_name_patterns` | `["(?i)^bot[_-]"]` | 假人名称正则列表；默认只对**不在 usercache 中**的玩家生效，避免误伤同名真玩家 |
| `bot_name_patterns_apply_to_all` | `false` | 设为 `true` 时名称规则对所有玩家生效（适用于假人也写入 usercache 的服务端）；同名真玩家请加入 `not_bot_names` |

登录后浏览器会获得一个 **HttpOnly + SameSite=Strict 的会话 cookie**（脚本读不到、跨站请求不会携带），有效期默认 30 天；只要期间有活跃操作，到期时间会自动顺延，因此普通使用不需要反复登录。点「退出登录」会立刻注销；清空 `token_secret` 同样会让所有会话立即失效。

### 忘记密码怎么办

把配置文件里的 `password.salt` 和 `password.hash` 都改成空字符串 `""`：

```json
"password": { "salt": "", "hash": "" }
```

保存后执行 `!!MCDR reload plugin minecraft_web_manager`，新的一次性密码会重新打印在 MCDR 日志里。

---

## 关于 RCON

插件的大部分功能不需要 RCON，但以下内容依赖它：

- TPS / MSPT 读数（通过 `tick query`）
- 玩家列表里的坐标与维度
- 控制台切换到 RCON 通道后才能看到命令的回复文本
- 插件重载后恢复在线玩家列表

启用方式是 **两处都要配**，且端口与密码必须一致：

- Minecraft 端：`server/server.properties` 里的 `enable-rcon`、`rcon.port`、`rcon.password`
- MCDR 端：MCDR 的 `config.yml` 里的 `rcon` 段

其中 Minecraft 端的三项可以直接在面板的「World → 服务器配置」里改（改完需重启服务器）。

---

## 安全建议

面板拥有服务器的完整控制权（执行任意命令、封禁玩家、改配置），请谨慎暴露：

- **默认只监听 `127.0.0.1`**，从外网访问建议保留这一设置，通过 SSH 隧道或内网穿透连接
- 若确实要开放到公网，请把它放在 Nginx / Caddy 等反向代理之后并启用 HTTPS。插件自身**不提供 TLS**，明文 HTTP 会让密码和令牌在链路上裸奔
- 登录接口内置简单的失败限流（同一来源 60 秒内最多尝试 10 次），并且面板的 API 文档默认关闭；会话 cookie 使用 `HttpOnly` 与 `SameSite=Strict`，页面脚本无法读取令牌
- 反向代理需要额外转发 WebSocket，否则实时控制台无法连接：

  ```nginx
  location /ws/ {
      proxy_pass http://127.0.0.1:8088;
      proxy_http_version 1.1;
      proxy_set_header Upgrade $http_upgrade;
      proxy_set_header Connection "upgrade";
  }
  ```

- `rcon.password` 等敏感配置项在面板里只显示为空，不会下发到浏览器；留空提交表示保持原值不变

---

## 已知限制

- **资源占用历史存在内存中**，MCDR 重启或插件重载后会清零，重新从头累积
- **世界种子、名称、难度读自存档文件**，只在服务器存盘时更新，可能比实际状态滞后几分钟
- **玩家的 IP 与 UUID 靠解析服务器输出获得**，如果服务端日志格式特殊可能抓不到；插件重载后恢复的在线玩家没有加入时间与 IP
- **Mod 列表只识别 Fabric**（读取 `fabric.mod.json`），Forge / NeoForge mod 只会列出文件名
- **假人识别**：经典 Carpet 假人按离线 UUID 精确识别；TIS/AMS/RMS 等扩展的假人可能是 Mojang 查询 UUID 或随机 UUID（v4），只能靠名称规则 + usercache 信号识别。名称规则默认只对无 usercache 记录的玩家生效，误判时可用 `not_bot_names` 或行内「取消标记」强制修正
- **Ping 值在原版服务端下无法获取**，列表中不展示
- 面板界面目前支持简体中文与英文

---

## 反馈

欢迎通过 [Issues](https://github.com/ForestTrees/MinecraftWebManager/issues) 提交问题与建议。

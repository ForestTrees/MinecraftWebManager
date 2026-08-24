# Demo panel

This development-only harness runs the real Web Manager UI with an in-memory
MCDR/ Minecraft bridge. It is useful for checking layout and interactions
without starting a Minecraft server.

From the repository root:

```bash
./.venv/bin/python tools/demo_panel.py
```

Use `--always-update` when repeatedly testing the sidebar update button; the
prompt will return after each simulated reload:

```bash
./.venv/bin/python tools/demo_panel.py --always-update
```

Open `http://127.0.0.1:18088/` in a browser and log in with:

- Username: `admin`
- Password: `demo-password-123`

The demo intentionally reports a `minecraft_web_manager` update so the sidebar
update prompt can be inspected. Confirming it simulates the panel's brief
disconnect and automatic reload; no real plugin file is changed. Stop it with
`Ctrl-C`.

"""The Bombadil app kit: everything that turns a folder of QML into a native app.

  context.py   which app is running and where its files live
  engine.py    a QML engine with the kit, the OS style and the native types
  native/      Python objects apps use from QML (System, Store files, Vault, Command, ...)
  runtime.py   `bombadil-app run`: the window, hot reload, the bar, saved state
  check.py     `bombadil-app check`: load an app offscreen, report errors, screenshot it
  placement.py slide app windows in and out (Hyprland special workspaces)
  tools.py     the app tools os-mcp gives the agent
  cli.py       the `bombadil-app` command

Only engine.py, runtime.py, check.py and native/ import PySide6, so the rest of Bombadil
(agentd, os-mcp) never pays for Qt.
"""

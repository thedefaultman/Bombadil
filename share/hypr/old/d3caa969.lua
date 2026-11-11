-- Bombadil session. The shell is the Quickshell bar; the agent daemon starts with it.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 1 })
-- A QEMU or virtio guest reports the size of the host window it started in (often 640x480) as
-- the preferred mode and never moves off it. 1920x1080 is always in the virtual display's list.
hl.monitor({ output = "Virtual-1", mode = "1920x1080@60", position = "auto", scale = 1 })

hl.on("hyprland.start", function()
    hl.exec_cmd("agentd")
    hl.exec_cmd("bombadil-shell")
    hl.exec_cmd("mako")
    -- First boot happens in the pill: agentd asks which AI and signs in through the browser panel.
end)

hl.config({
    general = {
        gaps_in = 6,
        gaps_out = 12,
        border_size = 1,
        col = {
            active_border = "rgba(d97757ee)",
            inactive_border = "rgba(2a2f36ee)",
        },
        layout = "dwindle",
    },
    decoration = {
        rounding = 12,
        blur = { enabled = true, size = 6, passes = 2 },
    },
    animations = { enabled = true },
    misc = {
        disable_hyprland_logo = true,
        disable_splash_rendering = true,
        background_color = 0x101214,
    },
    input = { kb_layout = "us", follow_mouse = 1 },
})

hl.curve("ease", { type = "bezier", points = { {0.16, 1}, {0.3, 1} } })
hl.animation({ leaf = "windows", enabled = true, speed = 4, bezier = "ease", style = "slide" })
hl.animation({ leaf = "specialWorkspace", enabled = true, speed = 5, bezier = "ease", style = "slidevert" })
hl.animation({ leaf = "fade", enabled = true, speed = 4, bezier = "ease" })

-- Panels are special workspaces; os-mcp toggles them. They also have keys.
hl.bind("SUPER + B", hl.dsp.workspace.toggle_special("browser"))
hl.bind("SUPER + T", hl.dsp.workspace.toggle_special("terminal"))
hl.bind("SUPER + F", hl.dsp.workspace.toggle_special("files"))
hl.bind("SUPER + Q", hl.dsp.window.close())
hl.bind("SUPER + Return", hl.dsp.exec_cmd("foot"))
-- Tap Super alone: the pill takes the keyboard (a second tap gives it back). It fires on
-- release, and Hyprland drops it when another key, a click or a drag happened meanwhile.
hl.bind("SUPER + SUPER_L", hl.dsp.exec_cmd("bombadil pill"), { release = true })
hl.bind("SUPER + SUPER_R", hl.dsp.exec_cmd("bombadil pill"), { release = true })
-- Alt+Space does the same where Super never arrives: a VM window on Windows keeps the Windows key
-- for its Start menu.
hl.bind("ALT + space", hl.dsp.exec_cmd("bombadil pill"))
-- Stop from anywhere: ends the running turn and everything it started, sudo'd commands too.
hl.bind("SUPER + Escape", hl.dsp.exec_cmd("bombadil stop"))
-- If the bar itself hangs: start it again.
hl.bind("SUPER + CTRL + Escape", hl.dsp.exec_cmd("pkill -x quickshell; bombadil-shell"))

-- Generated apps float, centered, so they appear as a card over the desktop.
hl.window_rule({
    name = "bombadil-apps",
    match = { class = "^(bombadil-app-.*)$" },
    float = true,
    center = true,
    size = "540 660",
})

-- Panel apps open straight into their panel; os-mcp (hypr.py) only launches and toggles.
hl.window_rule({ name = "panel-browser", match = { class = "^(bombadil-browser)$" }, workspace = "special:browser silent" })
hl.window_rule({ name = "panel-terminal", match = { class = "^(bombadil-terminal)$" }, workspace = "special:terminal silent" })
hl.window_rule({ name = "panel-files", match = { class = "^(org.gnome.Nautilus)$" }, workspace = "special:files silent" })
-- The details drawer: a turn's commands and output (click the line above the pill), history, Wi-Fi.
hl.window_rule({ name = "panel-details", match = { class = "^(bombadil-details)$" }, workspace = "special:details silent" })

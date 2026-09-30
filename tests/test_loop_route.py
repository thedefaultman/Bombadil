import pytest

from bombadil.loop import route

OS = "mcp__bombadil-os__"


def bash(command):
    return {"kind": "tool", "name": "Bash", "input": {"command": command}}


def tool(tool_name, /, **inputs):
    return {"kind": "tool", "name": tool_name, "input": inputs}


def topics(*commands):
    return route.route([bash(c) for c in commands])


# -- programs to topics --

@pytest.mark.parametrize("command, expected", [
    ("free -h", ["memory"]),
    ("ps aux --sort=-%mem | head", ["processes", "memory"]),
    ("ps aux --sort=-%cpu | head", ["processes", "cpu"]),
    ("top -bn1 | head -15", ["processes"]),
    ("uptime", ["cpu"]),
    ("df -h /", ["disk"]),
    ("sensors", ["temperature"]),
    ("upower -i /org/freedesktop/UPower/devices/battery_BAT0", ["battery"]),
    ("acpi -b", ["battery"]),
    ("grim shot.png", ["screen"]),
    ("date", ["time"]),
    ("notify-send hi", ["notify"]),
    ("crontab -l", ["timers"]),
    ("dmesg | tail", ["logs"]),
])
def test_programs_say_what_was_looked_at(command, expected):
    assert topics(command) == expected


@pytest.mark.parametrize("command, expected", [
    ("ping -c 1 1.1.1.1", ["network", "network:reach"]),
    ("ss -tlnp", ["network", "network:ports"]),
    ("ip -4 addr show", ["network", "network:addr"]),
    ("nmcli dev wifi list", ["network", "network:wifi"]),
    ("nmcli general status", ["network", "network:reach"]),
    ("nmcli connection show", ["network", "network:link"]),
    ("speedtest-cli", ["network", "network:speed"]),
    ("curl -s ifconfig.me", ["network", "network:addr"]),
    ("curl -s https://icanhazip.com", ["network", "network:addr"]),
    ("curl -s https://1.1.1.1", ["network", "network:reach"]),
])
def test_network_questions_are_told_apart(command, expected):
    assert topics(command) == expected


def test_asking_about_ports_is_not_asking_about_the_wifi():
    assert set(topics("ss -tlnp")) != set(topics("nmcli general status"))
    assert set(topics("ping -c1 8.8.8.8")) == set(topics("nmcli general status"))   # both: is it up


@pytest.mark.parametrize("command, expected", [
    ("wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.3", ["sound", "sound:volume"]),
    ("wpctl set-mute @DEFAULT_AUDIO_SINK@ 1", ["sound", "sound:volume"]),
    ("wpctl set-default 52", ["sound", "sound:output"]),
    ("pactl set-default-sink alsa_output", ["sound", "sound:output"]),
    ("playerctl next", ["sound", "sound:next"]),
    ("playerctl metadata title", ["sound", "sound:metadata"]),
    ("brightnessctl set 30%", ["display", "display:brightness"]),
    ("gammastep -O 3500", ["display", "display:color"]),
    ("hyprctl monitors", ["display", "display:monitors"]),
    ("hyprctl clients", ["desktop", "desktop:clients"]),
    ("bluetoothctl power on", ["bluetooth", "bluetooth:power"]),
    ("bluetoothctl devices", ["bluetooth", "bluetooth:show"]),
])
def test_sound_display_and_bluetooth_say_which_setting(command, expected):
    assert topics(command) == expected


@pytest.mark.parametrize("command, expected", [
    ("git status --short", ["git", "git:status"]),
    ("git -C ~/src/site diff --stat", ["git", "git:diff"]),
    ("git", ["git"]),
    ("gh pr list", ["git", "git:pr"]),
    ("docker ps -a", ["docker", "docker:ps"]),
    ("docker compose up -d", ["docker", "docker:compose-up"]),
    ("docker-compose logs web", ["docker", "docker:compose-logs"]),
    ("docker restart postgres", ["docker", "docker:restart"]),
    ("systemctl status sshd", ["services", "services:sshd"]),
    ("sudo systemctl restart NetworkManager.service", ["services", "services:networkmanager"]),
    ("journalctl -u sshd --no-pager", ["services", "services:sshd"]),
    ("systemctl list-timers", ["timers"]),
    ("systemctl enable --now backup.timer", ["timers"]),
])
def test_subcommands_and_units_are_kept(command, expected):
    assert topics(command) == expected


@pytest.mark.parametrize("command, expected", [
    ("sudo pacman -S --noconfirm ffmpeg", ["packages", "packages:ffmpeg"]),
    ("pacman -S docker docker-compose", ["packages", "packages:docker", "packages:docker-compose"]),
    ("sudo pacman -Syu --noconfirm", ["packages"]),
    ("yay -S --needed spotify", ["packages", "packages:spotify"]),
    ("pip install requests", ["packages", "packages:requests"]),
    ("python3 -m pip install --user rich", ["packages", "packages:rich"]),
    ("npm install left-pad", ["packages", "packages:left-pad"]),
    ("flatpak install flathub org.gimp.GIMP", ["packages", "packages:org.gimp.gimp"]),
    ("sudo pacman -Rns libreoffice-fresh", ["packages", "packages:libreoffice-fresh"]),
])
def test_package_names_are_kept(command, expected):
    assert topics(command) == expected


def test_different_programs_are_different_routes():
    assert set(topics("sudo pacman -S ffmpeg")) != set(topics("sudo pacman -S docker"))


def test_killing_names_what_was_killed():
    assert topics("pkill firefox") == ["processes", "processes:firefox"]
    assert topics("pkill -9 firefox") == topics("killall firefox")
    assert topics("kill 1234") == ["processes"]
    assert set(topics("pkill firefox")) != set(topics("pkill gammastep"))


# -- how a command is read --

def test_pipes_lists_and_sudo_are_looked_through():
    assert topics("sudo -n free -h | head -2") == ["memory"]
    assert topics("cd /tmp && free -h && df -h") == ["memory", "disk"]
    assert topics("FOO=1 /usr/bin/free -m") == ["memory"]
    assert topics("env LC_ALL=C df -h") == ["disk"]
    assert topics("timeout 5 ping -c 1 1.1.1.1") == ["network", "network:reach"]
    assert topics("bash -c 'free -h; uptime'") == ["memory", "cpu"]


def test_shell_noise_is_not_a_topic():
    assert topics("echo hello", "true", "sleep 2", "cd ~", "python3 - <<'EOF'\nprint(1)\nEOF") == []


def test_an_unknown_program_names_itself():
    assert topics("wl-paste") == ["run:wl-paste"]


def test_each_topic_once_in_the_order_first_touched():
    assert topics("free -h", "df -h", "free -m", "uptime") == ["memory", "disk", "cpu"]


def test_a_long_sleep_is_a_timer_and_a_short_one_is_not():
    assert topics("(sleep 1200; notify-send Stretch) &") == ["timers"]   # the notice only helps the timer
    assert topics("sleep 5; free -h") == ["memory"]
    assert topics("systemd-run --user --on-active=25m notify-send Done") == ["timers"]


def test_scripts_are_named_by_their_file(home):
    assert topics("python ~/bin/batch_status.py") == ["script:batch_status.py"]
    assert topics("python /tmp/scratch.py") == []                      # the agent's own scratch
    assert topics("bash -lc 'sh ~/bin/backup.sh'") == ["script:backup.sh"]


# -- files --

def test_files_are_told_by_where_they_are(home):
    assert topics("ls ~/Downloads") == ["files:downloads", "kind:list"]
    assert topics("mv ~/Downloads/a.pdf ~/Documents/") == ["files:downloads", "files:documents"]
    assert topics("find ~/Documents -iname '*tax*.pdf'") == ["files:documents", "kind:search"]
    assert topics("cat ~/jobs/batch.log") == ["files:jobs", "kind:read"]
    assert topics("tail -n 5 /var/log/pacman.log") == ["logs"]
    assert topics("cat /proc/meminfo") == ["memory"]
    assert topics("ls /etc/ssh") == ["files:etc", "kind:list"]
    assert topics("ls ~") == ["files:home", "kind:list"]
    assert topics("du -h --max-depth=1 ~ | sort -h") == ["disk", "files:home"]
    assert topics("du -sh ~/*") == ["disk", "files:home"]


def test_a_redirect_says_where_it_wrote(home):
    assert topics("echo '2026-09-08,5,28' >> ~/Apps/runs/log.csv") == ["app:runs"]
    assert topics("echo hi > /dev/null") == []
    assert topics("echo hi > /tmp/x") == []


def test_cd_then_a_relative_command_is_where_it_went(home):
    assert topics("cd ~/Downloads && ls") == ["files:downloads", "kind:list"]
    assert topics("cd ~/Downloads && ls ~/Pictures") == ["files:pictures", "kind:list"]


def test_a_home_file_is_the_home_folder_not_a_folder_named_so(home):
    assert topics("cat ~/runs.csv") == ["files:home", "kind:read"]


def test_the_agents_file_tools_are_read_too(home):
    assert route.route([tool("Read", file_path="~/Apps/runs/main.qml")]) == ["app:runs", "kind:read"]
    assert route.route([tool("Edit", file_path="~/Apps/runs/main.qml")]) == ["app:runs"]
    assert route.route([tool("Glob", pattern="~/Documents/*.pdf")]) == ["files:documents", "kind:search"]
    assert route.route([tool("LS", path="~/Pictures")]) == ["files:pictures", "kind:list"]
    assert route.route([{"kind": "file_change", "changes": [{"path": "~/Apps/tracker/main.qml"}]}]) == ["app:tracker"]


# -- the web --

def test_a_url_names_its_host_and_the_weather_is_the_weather():
    assert topics("curl -s wttr.in/Lisbon?format=3") == ["weather"]
    assert route.route([tool("WebFetch", url="https://wttr.in/Porto")]) == ["weather"]
    assert route.route([tool("WebFetch", url="https://blog.rust-lang.org/async")]) == ["browser:blog.rust-lang.org"]
    assert route.route([tool("WebSearch", query="rust async book")]) == ["web"]
    assert topics("firefox https://example.org/") == ["browser:example.org"]
    assert topics("chromium") == ["browser"]


def test_a_url_with_a_login_in_it_never_leaks_the_login():
    user = "me"
    secret = "hunter" + "2"
    found = route.route([bash(f"curl -s https://{user}:{secret}@api.example.org/v1/items")])
    assert found == ["browser:api.example.org"]
    assert secret not in " ".join(found)


# -- the os tools --

def test_opening_an_app_or_a_panel_is_an_open():
    assert route.route([tool(f"{OS}open_app", name="passwords")]) == ["app:passwords", "opened"]
    assert route.route([tool(f"{OS}show_panel", name="browser")]) == ["panel:browser", "opened"]
    assert route.route([bash("bombadil-app show passwords")]) == ["app:passwords", "opened"]
    assert route.route([tool(f"{OS}hide_panel", name="browser")]) == ["panel:browser"]
    assert route.route([tool(f"{OS}close_app", name="passwords")]) == ["app:passwords"]


def test_opens_thing_needs_an_open_and_nothing_else():
    assert route.opens_thing(["app:passwords", "opened"]) == "app:passwords"
    assert route.opens_thing(["panel:browser", "opened"]) == "panel:browser"
    assert route.opens_thing(["app:runs"]) == ""                          # its files were read or written
    assert route.opens_thing(["app:runs", "kind:read"]) == ""
    assert route.opens_thing(["app:passwords", "opened", "memory"]) == ""
    assert route.opens_thing(["app:a", "app:b", "opened"]) == ""
    assert route.opens_thing([]) == ""


def test_what_only_helps_the_real_work_does_not_count():
    events = [tool(f"{OS}create_app", title="Moon Phase"), tool(f"{OS}screenshot"), tool(f"{OS}notify", text="done")]
    assert route.route(events) == ["app:moon-phase"]
    assert route.route([tool(f"{OS}screenshot")]) == ["screen"]           # alone, it is what was asked
    assert route.route([tool(f"{OS}app_guide"), tool(f"{OS}snapshot")]) == []      # the agent's own plumbing


def test_other_servers_tools_are_not_read():
    assert route.route([tool("mcp__github__list_issues", repo="x")]) == []


def test_codex_spellings_of_a_shell_command_are_read():
    assert route.route([{"kind": "tool", "name": "shell", "input": {"command": ["free", "-h"]}}]) == ["memory"]
    assert route.route([{"kind": "tool", "name": "exec_command", "input": {"cmd": "df -h"}}]) == ["disk"]


# -- private --

@pytest.mark.parametrize("command", [
    "ls ~/.ssh",
    "cat ~/.ssh/id_ed25519",
    "gpg --list-keys",
    "cat ~/src/site/.env",
    "pass show email/work",
    "cat ~/.aws/credentials",
    "nmcli -s -g 802-11-wireless-security.psk connection show home",
    "curl -H 'Authorization: token abc' https://api.example.org",
    "keepassxc-cli open ~/vault.kdbx",
])
def test_anything_near_a_secret_is_only_private(home, command):
    assert route.route([bash(command)]) == [route.PRIVATE]
    assert route.is_private(route.route([bash("free -h"), bash(command)]))


def test_private_steps_report_nothing_else(home):
    found = route.route([bash("free -h"), bash("cat ~/.ssh/config"), bash("df -h")])
    assert found == ["memory", route.PRIVATE, "disk"]
    assert not route.is_private(["memory"])


def test_the_words_of_a_fake_key_built_at_run_time_are_private(home):
    fake = "sk" + "-" + "x" * 24                     # built here so nothing credential-like sits in the file
    assert route.route([bash(f"export API_KEY={fake}; curl https://api.example.org")]) == [route.PRIVATE]
    assert route.route([tool("Write", file_path="~/.config/tool/token", content=fake)]) == [route.PRIVATE]


def test_a_password_apps_data_is_private_but_its_code_is_not(home):
    assert route.route([tool("Read", file_path="~/Apps/passwords/vault.json")]) == [route.PRIVATE]
    assert route.route([bash("cat ~/Apps/passwords/entries.db")]) == [route.PRIVATE]
    assert route.route([tool("Edit", file_path="~/Apps/passwords/main.qml", new_string="vault")]) == ["app:passwords"]
    assert route.route([tool("Read", file_path="~/Apps/runs/log.csv")]) == ["app:runs", "kind:read"]


def test_what_a_step_wrote_is_not_searched_for_secrets(home):
    # the app's own code may say "password"; only where a step was pointed can make it private
    assert route.route([tool("Write", file_path="~/Apps/notes/main.qml", content="Text { text: 'token' }")]) == [
        "app:notes"]


# -- robustness --

def test_odd_events_cost_only_themselves():
    events = [None, "x", 5, {"kind": "tool"}, {"kind": "tool", "name": None, "input": "free"},
              {"kind": "tool", "name": "Bash", "input": {"command": None}},
              {"kind": "text", "text": "free -h"}, bash("free -h")]
    assert route.route(events) == ["memory"]
    assert route.route(None) == []
    assert route.route([]) == []


def test_liveness_is_a_property_of_the_topic():
    assert route.is_live(["memory", "processes"])
    assert route.is_live(["weather"])
    assert not route.is_live(["files:downloads"])
    assert not route.is_live([])

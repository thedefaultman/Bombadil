# Test image for tests/desktop: Quickshell, sway (headless), grim, wtype, foot and zellij on Arch.
FROM archlinux:latest
RUN pacman-key --init && pacman -Sy --noconfirm archlinux-keyring && pacman -Syu --noconfirm \
    quickshell qt6-wayland qt6-declarative qt6-5compat qt6-svg sway hyprland foot grim wtype \
    python python-pip pyside6 python-pytest ttf-dejavu inter-font noto-fonts mesa procps-ng \
    wlr-randr jq socat sudo which zellij git && pacman -Scc --noconfirm

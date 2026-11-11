-- Bombadil's part comes from the system and follows its updates. Your own lines go below.
local ok = pcall(require, "/usr/share/bombadil/share/hypr/bombadil")
if not ok then
    -- Bombadil's part is not on this system (a restore point from before it existed): keep the old file working.
    local old = os.getenv("HOME") .. "/.config/hypr/hyprland.lua.before-split"
    local f = io.open(old, "r")
    if f then
        f:close()
        dofile(old)
    else
        -- Nothing to fall back to: still start the agent and the bar, so there is a pill to ask.
        hl.on("hyprland.start", function()
            hl.exec_cmd("systemctl --user import-environment && systemctl --user restart bombadil-agentd bombadil-shell")
        end)
    end
end

-- Your own lines go below. They run after Bombadil's, so a monitor or a bind here wins.

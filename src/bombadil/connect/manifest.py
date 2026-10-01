"""The Slack app Bombadil asks a person to make in their own workspace (docs/CONNECT.md, "Slack, through a
private internal app").

User scopes only and no bot user: the app reads and posts as the person, and only the person's own press posts.
Socket Mode, so Slack pushes new messages to the computer over a connection the computer opens (no public address,
no request URL, nothing listens). The setup recipe (`slack_setup.py`) opens `creation_url()`, which makes Slack's own
page show this manifest ready to create.
"""

import json
from urllib.parse import quote

APP_NAME = "Bombadil"
USER_SCOPES = ("channels:history", "groups:history", "im:history", "mpim:history",
               "channels:read", "groups:read", "im:read", "mpim:read", "users:read", "chat:write")
USER_EVENTS = ("message.channels", "message.groups", "message.im", "message.mpim")
# The tokens the person's app gives, and what each is for (the setup recipe takes both off Slack's own page).
APP_TOKEN_PREFIX = "xapp-"       # app-level token with connections:write: opens the socket
USER_TOKEN_PREFIX = "xoxp-"      # the person's own: reads, and posts when pressed


def manifest() -> dict:
    return {
        "display_information": {
            "name": APP_NAME,
            "description": "Brings the messages that are for you to Bombadil on your computer. It posts only when "
                           "you press Send there.",
            "background_color": "#101214",
        },
        "oauth_config": {"scopes": {"user": list(USER_SCOPES)}},
        "settings": {
            "event_subscriptions": {"user_events": list(USER_EVENTS)},
            "interactivity": {"is_enabled": False},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def creation_url() -> str:
    """Slack's own "create an app from this manifest" page."""
    body = json.dumps(manifest(), separators=(",", ":"))
    return "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(body, safe="")

"""Bombadil's mail: its own view of every account, with Thunderbird running unseen as the engine.

docs/MAIL.md is the contract. The service (service.py) owns mail.sock and mail.db, and everything
that can run without a real Thunderbird or a screen lives here; engine.py, tools.py and watch.py
are the parts that touch the outside."""

"""The self-improvement loop: Bombadil counts what you ask more than once, checks itself for bugs,
and tells you only when something waits for you. Design: /mnt/project-files/design/self-improvement-brief.md;
how it is built: docs/LOOP.md.

Everything here reads what the machine already writes, stays on the machine, and calls no model
unless a group is about to become an offer (refine.py, off until checked). Nothing here ever runs
on a turn's path: agentd calls it from a thread, and a loop that breaks costs a turn nothing.
"""

"""The brain: an index of this machine that nobody writes.

Files, folders, projects, apps, the pages you read, the machine's turns and the coding
sessions are things; every link between them is an event the OS witnessed (a save and who
made it, a download and the page it came from, a turn and the files it touched), so there
is nothing to tend and nothing to go stale. Plain files stay the truth: brain.db is a
cache beside them, and deleting it loses the view, not your data.

  watch.py     bombadil-brain-watch, the root service that sees every save with its writer
  service.py   bombadil-brain, the user service that keeps brain.db and answers on brain.sock
  store.py     brain.db itself; ingest.py turns what was witnessed into things and links
  focus.py     what Focus shows around one thing; this.py finds what "this" is on screen
"""

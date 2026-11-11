"""What is running inside a QML tree: its animations and timers.

Tips' rule is that nothing runs at rest, so the tests ask the tree instead of waiting to see
whether anything moves. Behaviors count too: they create their animation when a property changes,
and it is a child of the Behavior.
"""

from collections import Counter


def running_things(root):
    """Class names (QML type names stripped of Qt's suffixes) of every running Animation or Timer
    under `root`: a QObject, an item or a window. An empty list means nothing in it can change."""
    found, seen, todo = [], set(), [root]
    while todo:
        obj = todo.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        name = obj.metaObject().className()
        if ("Animation" in name or "Timer" in name) and obj.property("running") is True:
            found.append(name.split("_QMLTYPE")[0])
        todo.extend(obj.children())
        if hasattr(obj, "childItems"):
            todo.extend(obj.childItems())
        if hasattr(obj, "contentItem"):
            todo.append(obj.contentItem())
    return sorted(found)


def summary(root):
    return dict(Counter(running_things(root)))

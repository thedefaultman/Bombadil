"""The scripts bombadil-browserd runs in a page, and the only ones it ever runs.

The service has no hands: it does not run what a caller sends. Each script here is a fixed function; the caller's
data (a word to look for, a selector, a label) reaches it as one JSON literal at the end of the expression, with
every non-ASCII character escaped, so it can be nothing but a value. An expression is

    /*bombadil:<name>*/
    (function (a) { ... })
    (<json>)

so a test or a stand-in for Chromium can tell which script it was sent and read its arguments from the last line.

What the scripts do:

- `read`: the visible text of the page (`innerText`) or of one element, cut at `MAX_TEXT`.
- `find`: the visible button, link or input whose text, aria-label or placeholder matches, whole words
  preferred; its rectangle in the viewport.
- `point`: ONE element (`#bombadil-pointer`, `position: fixed`, `pointer-events: none`, above everything) that
  draws an orange ring where the target is, a chip with the label beside it and a pointer glyph. It measures the
  target again on every animation frame (a page that scrolls or resizes moves it along), and takes itself away
  when its time is up, when the page navigates (the address changes) and when told to. It never takes a click or
  a key, and it is built from DOM calls (text is `textContent`, never markup).
- `unpoint`: takes the pointer away.
- `probe`: the address and whether some words are on the page, for `wait`.
- `collect`: the page's text and the values of its fields, for `take`.

A target that is wholly outside the window is scrolled into view when it is pointed at: a pointer to something
nobody can see helps nobody, and a scroll changes nothing in the page.
"""

import json

MAX_TEXT = 200_000
POINTER_ID = "bombadil-pointer"
RING = "#d97757"
MAX_CANDIDATES = 2000
MAX_FIELD = 5000
MAX_FIELDS = 300

_LIB = r"""
  var CANDIDATES = 'button, a, input, textarea, select, summary, [role="button"], [role="link"], ' +
                   '[role="tab"], [role="menuitem"]';
  function vw() { return document.documentElement.clientWidth || window.innerWidth; }
  function vh() { return document.documentElement.clientHeight || window.innerHeight; }
  function norm(s) { return String(s == null ? '' : s).replace(/\s+/g, ' ').trim().toLowerCase(); }
  function esc(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
  function visibleRect(el) {
    var r = el.getBoundingClientRect();
    if (!(r.width >= 1 && r.height >= 1)) return null;
    var cs = window.getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || parseFloat(cs.opacity) === 0) return null;
    if (el.checkVisibility && !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true,
                                                   opacityProperty: true, visibilityProperty: true})) return null;
    return r;
  }
  function labelsOf(el) {
    var out = [], tag = el.tagName.toLowerCase(), i;
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
      var type = (el.type || '').toLowerCase();
      if (type === 'hidden') return [];
      if (type === 'button' || type === 'submit' || type === 'reset') out.push(el.value);
      out.push(el.placeholder);
      if (el.labels) for (i = 0; i < el.labels.length; i++) out.push(el.labels[i].innerText);
    } else {
      out.push(el.innerText != null ? el.innerText : el.textContent);
    }
    out.push(el.getAttribute('aria-label'));
    return out.map(norm).filter(Boolean);
  }
  function scorer(needle) {
    var n = norm(needle);
    var word = new RegExp('(^|[^\\p{L}\\p{N}_])' + esc(n) + '($|[^\\p{L}\\p{N}_])', 'u');
    return function (label) {
      if (label === n) return 3;
      if (word.test(label)) return 2;
      return label.indexOf(n) >= 0 ? 1 : 0;
    };
  }
  function findByText(needle) {
    var score = scorer(needle), nodes = document.querySelectorAll(CANDIDATES), best = null;
    for (var i = 0; i < nodes.length && i < MAX; i++) {
      var el = nodes[i], r = visibleRect(el);
      if (!r) continue;
      var labs = labelsOf(el), s = 0, len = 1e9;
      for (var j = 0; j < labs.length; j++) {
        var v = score(labs[j]);
        if (v > s) { s = v; len = labs[j].length; }
        else if (v === s && v > 0 && labs[j].length < len) len = labs[j].length;
      }
      if (!s) continue;
      var inView = (r.bottom > 0 && r.right > 0 && r.top < vh() && r.left < vw()) ? 1 : 0;
      if (!best || s > best.s || (s === best.s && (len < best.len || (len === best.len && inView > best.inView)))) {
        best = {el: el, s: s, len: len, inView: inView};
      }
    }
    return best ? best.el : null;
  }
  function findBySelector(sel) {
    var el = document.querySelector(sel);
    return el && visibleRect(el) ? el : null;
  }
"""

_READ = r"""
  var el;
  try { el = a.selector ? document.querySelector(a.selector) : document.body; }
  catch (e) { return {error: 'selector'}; }
  if (a.selector && !el) return {found: false, url: location.href, title: document.title, text: ''};
  var text = '';
  if (el) {
    var tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
      text = ((el.type || '').toLowerCase() === 'password') ? '' : (el.value || '');
    } else {
      text = el.innerText != null ? el.innerText : (el.textContent || '');
    }
  }
  return {found: true, url: location.href, title: document.title, text: String(text).slice(0, a.max)};
"""

_FIND = r"""
  var el;
  try { el = findByText(a.text); } catch (e) { return {error: 'text'}; }
  if (!el) return {found: false};
  var r = el.getBoundingClientRect();
  return {found: true, rect: {x: r.left, y: r.top, w: r.width, h: r.height}};
"""

_POINT = r"""
  if (a.selector) {
    try { document.querySelector(a.selector); } catch (e) { return {error: 'selector'}; }
  }
  var prev = window.__bombadilPointer;
  if (prev && prev.stop) prev.stop();
  var stale = document.getElementById(a.id);
  if (stale && stale.parentNode) stale.parentNode.removeChild(stale);
  function lookup() {
    try { return a.selector ? findBySelector(a.selector) : findByText(a.text); } catch (e) { return null; }
  }
  var el = lookup();
  if (!el) return {pointed: false};
  var first = el.getBoundingClientRect();
  if (first.bottom < 0 || first.top > vh() || first.right < 0 || first.left > vw()) {
    try { el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'}); } catch (e) {}
  }
  function css(node, props) {
    node.style.setProperty('all', 'initial', 'important');
    for (var k in props) node.style.setProperty(k, props[k], 'important');
  }
  var root = document.createElement('div');
  root.id = a.id;
  root.setAttribute('aria-hidden', 'true');
  css(root, {'position': 'fixed', 'display': 'block', 'box-sizing': 'border-box', 'pointer-events': 'none',
             'z-index': '2147483647', 'margin': '0', 'padding': '0', 'border-radius': '8px',
             'outline': '3px solid ' + a.ring, 'outline-offset': '3px',
             'box-shadow': '0 0 0 9px rgba(217,119,87,0.28), 0 0 28px rgba(217,119,87,0.6)',
             'visibility': 'hidden', 'left': '0px', 'top': '0px', 'width': '0px', 'height': '0px'});
  var chip = document.createElement('div');
  chip.textContent = a.label;
  css(chip, {'position': 'absolute', 'display': 'block', 'pointer-events': 'none', 'white-space': 'nowrap',
             'background': a.ring, 'color': '#ffffff', 'font': '600 13px/1 system-ui, sans-serif',
             'padding': '7px 11px', 'border-radius': '999px', 'box-shadow': '0 2px 10px rgba(0,0,0,0.35)',
             'left': '0px', 'top': '0px'});
  var NS = 'http://www.w3.org/2000/svg';
  var glyph = document.createElementNS(NS, 'svg');
  glyph.setAttribute('width', '24');
  glyph.setAttribute('height', '24');
  glyph.setAttribute('viewBox', '0 0 24 24');
  var path = document.createElementNS(NS, 'path');
  path.setAttribute('d', 'M3 2 L3 18 L7.5 13.8 L10.6 21 L14 19.5 L10.9 12.5 L17 12.5 Z');
  path.setAttribute('fill', a.ring);
  path.setAttribute('stroke', '#ffffff');
  path.setAttribute('stroke-width', '1.6');
  path.setAttribute('stroke-linejoin', 'round');
  glyph.appendChild(path);
  css(glyph, {'position': 'absolute', 'display': 'block', 'pointer-events': 'none', 'overflow': 'visible',
              'filter': 'drop-shadow(0 1px 3px rgba(0,0,0,0.45))', 'left': '0px', 'top': '0px'});
  root.appendChild(chip);
  root.appendChild(glyph);
  document.documentElement.appendChild(root);

  var inst = {dead: false, raf: 0, timer: 0};
  var href = location.href, until = Date.now() + a.ttl, looked = 0;
  var last = {l: NaN, t: NaN, w: NaN, h: NaN}, hidden = true;
  function set(node, name, value) { node.style.setProperty(name, value, 'important'); }
  function place() {
    var r = el.getBoundingClientRect();
    var off = !(r.width >= 1 && r.height >= 1) || r.bottom < 0 || r.top > vh() || r.right < 0 || r.left > vw();
    if (off) {
      if (!hidden) { set(root, 'visibility', 'hidden'); hidden = true; }
      return;
    }
    if (r.left !== last.l || r.top !== last.t || r.width !== last.w || r.height !== last.h) {
      last = {l: r.left, t: r.top, w: r.width, h: r.height};
      set(root, 'left', r.left + 'px');
      set(root, 'top', r.top + 'px');
      set(root, 'width', r.width + 'px');
      set(root, 'height', r.height + 'px');
      var cw = chip.offsetWidth, ch = chip.offsetHeight;
      var above = r.top - ch - 16 >= 4;
      var left = Math.max(4, Math.min(r.left, vw() - cw - 4)) - r.left;
      set(chip, 'left', left + 'px');
      set(chip, 'top', (above ? -(ch + 16) : r.height + 16) + 'px');
      set(glyph, 'left', (Math.max(6, r.width - 16) - 3) + 'px');
      set(glyph, 'top', (Math.max(6, r.height - 8) - 2) + 'px');
    }
    if (hidden) { set(root, 'visibility', 'visible'); hidden = false; }
  }
  inst.stop = function () {
    if (inst.dead) return;
    inst.dead = true;
    cancelAnimationFrame(inst.raf);
    clearTimeout(inst.timer);
    if (root.parentNode) root.parentNode.removeChild(root);
    if (window.__bombadilPointer === inst) window.__bombadilPointer = null;
  };
  function tick() {
    if (inst.dead) return;
    if (location.href !== href || Date.now() >= until) { inst.stop(); return; }
    if (!el.isConnected) {
      // the page drew the element again (a framework does): find the new one, at most four times a second
      var now = Date.now(), again = null;
      if (now - looked > 250) { looked = now; again = lookup(); }
      if (again) el = again;
      else {
        if (!hidden) { set(root, 'visibility', 'hidden'); hidden = true; }
        inst.raf = requestAnimationFrame(tick);
        return;
      }
    }
    place();
    inst.raf = requestAnimationFrame(tick);
  }
  window.__bombadilPointer = inst;
  place();
  inst.raf = requestAnimationFrame(tick);
  inst.timer = setTimeout(inst.stop, a.ttl);
  return {pointed: true};
"""

_UNPOINT = r"""
  var had = false, p = window.__bombadilPointer;
  if (p && p.stop) { p.stop(); had = true; }
  var e = document.getElementById(a.id);
  if (e && e.parentNode) { e.parentNode.removeChild(e); had = true; }
  return {removed: had};
"""

_PROBE = r"""
  var has = null;
  if (a.text) {
    var t = document.body ? document.body.innerText : '';
    has = norm(t).indexOf(norm(a.text)) >= 0;
  }
  return {url: location.href, title: document.title, has: has, ready: document.readyState};
"""

_COLLECT = r"""
  var out = [], t = document.body ? document.body.innerText : '';
  out.push(String(t).slice(0, a.max));
  var fields = document.querySelectorAll('input, textarea, [data-clipboard-text]');
  for (var i = 0; i < fields.length && i < a.fields; i++) {
    var f = fields[i], v = f.value;
    if (typeof v !== 'string' || !v) v = f.getAttribute('data-clipboard-text');
    if (v && v.length <= a.field_max) out.push(v);
  }
  return {chunks: out};
"""

_BODIES = {"read": _READ, "find": _FIND, "point": _POINT, "unpoint": _UNPOINT, "probe": _PROBE,
           "collect": _COLLECT}
_CONSTANTS = f"  var MAX = {MAX_CANDIDATES};\n"

# name -> the whole function, as sent to the page. The service sends these and nothing else.
SCRIPTS = {name: "(function (a) {\n" + _CONSTANTS + _LIB + body + "})" for name, body in _BODIES.items()}


def expression(name: str, args: dict) -> str:
    """The expression that runs script `name` on `args` (JSON-able data). ASCII only, so the data cannot be
    anything but a literal."""
    return f"/*bombadil:{name}*/\n{SCRIPTS[name]}\n({json.dumps(args, ensure_ascii=True, allow_nan=False)})"


def parse(expr: str) -> tuple[str, dict, str]:
    """(name, args, function) of an expression made by `expression`: for stand-ins for Chromium and tests."""
    head, _, rest = expr.partition("\n")
    name = head.removeprefix("/*bombadil:").removesuffix("*/")
    function, _, tail = rest.rpartition("\n")
    return name, json.loads(tail[1:-1]), function

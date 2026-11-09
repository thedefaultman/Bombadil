// What the add-on's files are, read as text: the permissions it asks for, the calls it may make, and the
// ways it could be used to do more than it is for.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../../share/mail/extension");
const files = readdirSync(root).sort();
const scripts = files.filter(f => f.endsWith(".js"));
const text = name => readFileSync(join(root, name), "utf8");
const manifest = JSON.parse(text("manifest.json"));

// Comments are not code: what a comment says about a call is not a call.
const code = name =>
  text(name)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "")
    .replace(/\s\/\/ .*$/gm, "");

test("the add-on is a manifest and scripts, nothing else, and nothing needs building", () => {
  assert.deepEqual(files.filter(f => !f.endsWith(".js")), ["manifest.json"]);
  for (const f of files) {
    assert.ok(statSync(join(root, f)).isFile());
    assert.ok(statSync(join(root, f)).size < 80_000, `${f} is large`);
  }
  assert.ok(!files.includes("package.json"));
});

test("the manifest: this id, Thunderbird 153 and up, a module for a background page, and a strict policy", () => {
  assert.equal(manifest.manifest_version, 2);
  assert.equal(manifest.browser_specific_settings.gecko.id, "bombadil-mail@bombadil.local");
  assert.equal(manifest.browser_specific_settings.gecko.strict_min_version, "153.0");
  assert.deepEqual(manifest.background, { scripts: ["background.js"], type: "module", persistent: true });
  assert.ok(files.includes("background.js"));
  const policy = manifest.content_security_policy;
  assert.match(policy, /default-src 'none'/);
  assert.match(policy, /script-src 'self'/);
  assert.match(policy, /connect-src 'none'/);
  assert.doesNotMatch(policy, /unsafe-|https?:|\*/);
  assert.ok(!("web_accessible_resources" in manifest));
  assert.ok(!("content_scripts" in manifest));
  assert.ok(!("experiment_apis" in manifest), "no code with more rights than the WebExtension APIs");
});

test("the permissions are exactly these, no host permission among them, and sending is optional", () => {
  assert.deepEqual([...manifest.permissions].sort(), [
    "accountsRead",
    "addressBooks",
    "compose",
    "compose.send",
    "messagesMove",
    "messagesRead",
    "messagesUpdate",
    "nativeMessaging",
  ]);
  assert.deepEqual(manifest.optional_permissions, ["messages.send"]);
  for (const p of [...manifest.permissions, ...manifest.optional_permissions]) {
    assert.doesNotMatch(p, /[*:/]|all_urls|^tabs$|webRequest|downloads|cookies|storage|messagesDelete|messagesTags|sensitiveDataUpload|accountsFolders|accountsIdentities|compose\.save|messagesImport|messagesModify/i, p);
  }
});

test("every import is a file of the add-on, and every file is reached from the background page", () => {
  const seen = new Set(["background.js"]);
  const queue = ["background.js"];
  while (queue.length) {
    const name = queue.pop();
    for (const [, spec] of code(name).matchAll(/^\s*(?:import|export)\b[^;]*?\bfrom\s+"([^"]+)"/gm)) {
      assert.match(spec, /^\.\/[a-z]+\.js$/, `${name} imports ${spec}`);
      assert.ok(files.includes(spec.slice(2)), `${name} imports a file that is not there: ${spec}`);
      if (!seen.has(spec.slice(2))) {
        seen.add(spec.slice(2));
        queue.push(spec.slice(2));
      }
    }
  }
  assert.deepEqual([...seen].sort(), scripts);
});

const FORBIDDEN = [
  [/\bfetch\s*\(/, "a network call"],
  [/\bXMLHttpRequest\b/, "a network call"],
  [/\bWebSocket\b/, "a network call"],
  [/\bEventSource\b/, "a network call"],
  [/\bsendBeacon\b/, "a network call"],
  [/\bimportScripts\b/, "loading code"],
  [/\bimport\s*\(/, "loading code"],
  [/\beval\s*\(/, "running text as code"],
  [/\bnew\s+Function\b/, "running text as code"],
  [/\bFunction\s*\(/, "running text as code"],
  [/\binnerHTML\b|\bouterHTML\b|\binsertAdjacentHTML\b|document\.write/, "putting mail into a page"],
  [/\bdocument\./, "a page"],
  [/\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|\bstorage\.(local|sync)/, "keeping mail on disk"],
  [/\bmessages\.(delete|import|modify|setTags|empty)\b|\.deleteMessage|\.emptyTrash/, "something that loses or changes mail"],
  [/\b(tabs|windows)\.create\b|\btabs\.update\b|\bopenTab\b|\bbrowser\.windows\.create/, "opening a window"],
  [/\bpermissions\.(request|remove)\b/, "asking for more"],
  [/\bsendNativeMessage\b/, "a second way to the host"],
  [/\bmessenger\.(downloads|cookies|webRequest|identity|menus|notifications|clipboard)\b/, "something it has no permission for"],
  [/\bprocess\.|\brequire\s*\(/, "Node"],
];

test("no network, no code loaded or built from text, no page, no file or storage, nothing that deletes", () => {
  for (const name of scripts) {
    const source = code(name);
    for (const [pattern, what] of FORBIDDEN) {
      assert.doesNotMatch(source, pattern, `${name} has ${what}`);
    }
    assert.doesNotMatch(source, /https?:\/\/[^\s"'`)]*[a-z0-9]\.[a-z]/i, `${name} has a web address in its code`);
  }
});

test("only the send module sends, and only through the one function that holds a send to its deadline", () => {
  for (const name of scripts) {
    const source = code(name);
    const uses = [...source.matchAll(/\b(?:messages|compose)\.sendMessage\b/g)];
    if (name !== "sending.js") {
      assert.equal(uses.length, 0, `${name} sends`);
    }
  }
  const source = code("sending.js");
  const direct = [...source.matchAll(/\bmessages\.sendMessage\(/g)];
  const window = [...source.matchAll(/\bcompose\.sendMessage\(/g)];
  assert.equal(direct.length, 1);
  assert.equal(window.length, 1);
  for (const call of [...direct, ...window]) {
    const before = source.slice(Math.max(0, call.index - 120), call.index);
    assert.match(before, /commit\(\(\) =>\s*(this\.)?(messenger\.)?$/, "a send is made inside commit, which checks the time and watches the answer");
  }
  assert.equal([...source.matchAll(/\bcommit\(\(\)/g)].length, 2);
  assert.equal([...source.matchAll(/\bsendMessage\b/g)].length, 3 + [...source.matchAll(/sendMessage failed/g)].length, "only those two calls, and the words about them");
  assert.ok(!/\bsetInterval\b/.test(source));
});

test("nothing starts a send but a request: no listener in the add-on does", () => {
  for (const name of scripts.filter(f => f !== "engine.js")) {
    assert.doesNotMatch(code(name), /\bsending\.send\b|\bSending\b.*\.send\(/, name);
  }
  const events = code("events.js");
  assert.doesNotMatch(events, /compose|sendMessage|beginNew|beginReply|beginForward/);
  const link = code("link.js");
  assert.doesNotMatch(link, /compose|sendMessage/);
  const compose = scripts.filter(f => /\bcompose\.(begin|send|set)/.test(code(f)));
  assert.deepEqual(compose, ["sending.js"]);
  const listeners = scripts.filter(f => /onBeforeSend|onAfterSend|onComposeStateChanged|onIdentityChanged/.test(code(f)));
  assert.deepEqual(listeners, [], "nothing watches or changes a compose window but the send that opened it");
});

test("the native port is opened in one place, by name", () => {
  const found = scripts.filter(f => /connectNative/.test(code(f)));
  assert.deepEqual(found, ["link.js"]);
  assert.match(code("link.js"), /const NAME = "bombadil_mail"/);
});

test("no file has a raw line or paragraph separator, which a tool can put into a regular expression by mistake", () => {
  for (const name of files) {
    assert.ok(!/[\u2028\u2029]/.test(text(name)), name);
  }
});

test("addresses in the add-on are for examples only, and there is no key, token or password in it", () => {
  for (const name of files) {
    const source = text(name);
    assert.doesNotMatch(source, /(?:password|passwd|secret|token|api[_-]?key)\s*[:=]\s*["'][^"']{4,}/i, name);
    for (const [address] of source.matchAll(/[\w.+-]+@[\w-]+\.[\w.-]+/g)) {
      assert.match(address, /@([\w-]+\.)*(example\.(test|org|com|net|de)|bombadil\.local|invalid|localhost)$|^\w+@b\.example$/i, `${name}: ${address}`);
    }
  }
});

test("every module opens with a comment that says what it is for", () => {
  for (const name of scripts) {
    assert.match(text(name), /^\/\*\n \* \S/, name);
  }
});

/*
 * Bombadil Mail Lab extension: background.js
 *
 * A deliberately dumb bridge. It opens ONE native-messaging connection to the
 * host "bombadil_mail" and then executes whatever messenger.* call the host
 * asks for, returning the (JSON-ised) result. That lets the Python lab
 * (lab_bridge.py) exercise any MailExtension API on a real Thunderbird without
 * rebuilding the XPI for every experiment.
 *
 * Wire format (JSON objects, one per native-messaging frame):
 *   host -> ext  {id, op:"call",   path:"messages.query", args:[{...}]}
 *                {id, op:"listen", event:"messages.onNewMailReceived"}
 *                {id, op:"unlisten", event:"..."}
 *                {id, op:"stash", key, idx, b64}                 (chunked upload, see fromHost)
 *                {id, op:"echo",   payload:"..."}          (host->ext size tests)
 *                {id, op:"gen",    bytes:N}                (ext->host size tests)
 *                {id, op:"ping"}
 *                {id, op:"eval",   code:"..."}             (async function body, lab only)
 *   ext  -> host {id, ok:true,  result:...} | {id, ok:false, error:"..."}
 *                {event:"messages.onNewMailReceived", args:[...], t:ms}
 *                {hello:true, ...}   (sent once on connect)
 *
 * Arguments: {__file:true,name,type,b64|stash} becomes a File, {__date:"<ISO>"} a Date (messages.query's fromDate/toDate
 * hang forever when given a string or number: pass a Date).
 *
 * Blob/File results (messages.getRaw, getAttachmentFile) are returned as
 *   {__blob:true, name, type, size, b64}.
 */
"use strict";

const HOST = "bombadil_mail";
const T0 = Date.now();
let port = null;
const listeners = new Map();
// Chunked uploads from the host (host->ext frames are capped at 1 MB): {op:"stash",key,idx,b64} x N, then reference
// the assembled bytes as {__file:true, stash:key, name, type} in any call argument.
const stash = new Map();

function log(...a) {
  console.log("[bombadil-lab]", ...a);
}

async function blobToB64(blob) {
  const buf = new Uint8Array(await blob.arrayBuffer());
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) {
    s += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
  }
  return btoa(s);
}

async function jsonable(v) {
  if (v instanceof Blob) {
    return {
      __blob: true,
      name: v.name || null,
      type: v.type,
      size: v.size,
      b64: await blobToB64(v),
    };
  }
  if (v instanceof Date) {
    return v.toISOString();
  }
  if (Array.isArray(v)) {
    return Promise.all(v.map(jsonable));
  }
  if (v && typeof v == "object") {
    const o = {};
    for (const [k, x] of Object.entries(v)) {
      o[k] = await jsonable(x);
    }
    return o;
  }
  return v;
}

// Arguments coming from the host may describe a File to create (for
// compose.addAttachment / messages.import): {__file:true, name, type, b64}.
function fromHost(v) {
  if (Array.isArray(v)) {
    return v.map(fromHost);
  }
  if (v && typeof v == "object") {
    if (v.__date) {
      return new Date(v.__date);
    }
    if (v.__file) {
      const bin = atob(v.stash ? stash.get(v.stash).join("") : v.b64 || "");
      const u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) {
        u8[i] = bin.charCodeAt(i);
      }
      return new File([u8], v.name || "file", { type: v.type || "" });
    }
    const o = {};
    for (const [k, x] of Object.entries(v)) {
      o[k] = fromHost(x);
    }
    return o;
  }
  return v;
}

function resolvePath(path) {
  const parts = path.split(".");
  let obj = messenger;
  for (const p of parts.slice(0, -1)) {
    obj = obj[p];
    if (obj === undefined) {
      throw new Error(`no such namespace: ${path}`);
    }
  }
  const fn = obj[parts[parts.length - 1]];
  if (fn === undefined) {
    throw new Error(`no such API: messenger.${path}`);
  }
  return [obj, fn];
}

function send(msg) {
  port.postMessage(msg);
}

async function handle(msg) {
  const { id, op } = msg;
  try {
    let result;
    switch (op) {
      case "ping":
        result = { pong: true, uptimeMs: Date.now() - T0 };
        break;
      case "hello":
        result = {
          version: messenger.runtime.getManifest().version,
          startedAtMs: T0,
          accounts: await messenger.accounts.list(),
        };
        break;
      case "call": {
        const [obj, fn] = resolvePath(msg.path);
        result = await fn.apply(obj, fromHost(msg.args || []));
        break;
      }
      case "listen": {
        if (!listeners.has(msg.event)) {
          const [, ev] = resolvePath(msg.event);
          const cb = (...args) => {
            jsonable(args).then(a =>
              send({ event: msg.event, args: a, t: Date.now() })
            );
          };
          ev.addListener(cb);
          listeners.set(msg.event, [ev, cb]);
        }
        result = { listening: msg.event };
        break;
      }
      case "unlisten": {
        const l = listeners.get(msg.event);
        if (l) {
          l[0].removeListener(l[1]);
          listeners.delete(msg.event);
        }
        result = { unlistened: msg.event };
        break;
      }
      case "stash": {
        const arr = stash.get(msg.key) || [];
        arr[msg.idx] = msg.b64;
        stash.set(msg.key, arr);
        result = { chunks: arr.length };
        break;
      }
      case "stash_drop":
        stash.delete(msg.key);
        result = { dropped: msg.key };
        break;
      case "echo":
        result = { len: msg.payload.length };
        break;
      case "gen":
        result = { payload: "x".repeat(msg.bytes) };
        break;
      case "eval": {
        const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
        result = await new AsyncFunction("messenger", "args", msg.code)(
          messenger,
          msg.args || []
        );
        break;
      }
      default:
        throw new Error(`unknown op ${op}`);
    }
    send({ id, ok: true, result: await jsonable(result) });
  } catch (e) {
    send({ id, ok: false, error: String((e && e.message) || e) });
  }
}

let reconnectTimer = null;
function connect() {
  log("connectNative", HOST);
  port = messenger.runtime.connectNative(HOST);
  port.onMessage.addListener(m => {
    handle(m);
  });
  port.onDisconnect.addListener(p => {
    const err = p.error ? p.error.message : messenger.runtime.lastError;
    log("native port disconnected", err);
    port = null;
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 2000);
  });
  messenger.accounts.list().then(
    accounts => {
      send({
        hello: true,
        version: messenger.runtime.getManifest().version,
        startedAtMs: T0,
        accounts,
      });
    },
    e => send({ hello: true, error: String(e) })
  );
}

connect();

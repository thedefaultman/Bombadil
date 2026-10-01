/*
 * Files going through the add-on, both ways, in pieces.
 *
 * A frame to the add-on may not be more than 1 MiB (Thunderbird closes the port on one), so an attachment
 * the person is sending arrives before the `send` that names it, as `blob` requests of at most 384 KiB each,
 * in order. They wait here, in the `Stash`, and the send takes them. A frame from the add-on may be far
 * larger, but an attachment the person is saving still goes out in the same pieces, as `blob` events, so the
 * service writes it to disk as it arrives and nothing holds a whole file but Thunderbird's own.
 *
 * The stash is bounded in every direction: a file is at most 100 MiB, a transfer's pieces must come in
 * order and in the size the service sends, at most 64 transfers wait at a time and 256 MiB in all, and one
 * that is not taken within ten minutes of its last piece is forgotten. Only a send takes pieces out, and it
 * forgets what it was told about when it is over, however it ended.
 */

import { badRequest, engineError, notFound, tooBig } from "./errors.js";
import { pause } from "./clock.js";

export const CHUNK_MAX = 384 * 1024;
export const FILE_MAX = 100 * 1024 * 1024;
const STASH_MAX = 256 * 1024 * 1024;
const TRANSFERS_MAX = 64;
const KEEP_MS = 10 * 60 * 1000;
const SWEEP_MS = 60 * 1000;
const NAME = /^[A-Za-z0-9._-]{1,128}$/;
const BASE64 = /^[A-Za-z0-9+/]*={0,2}$/;

// -- base64 --

export function encode(bytes) {
  if (typeof bytes.toBase64 === "function") {
    return bytes.toBase64();
  }
  let out = "";
  for (let at = 0; at < bytes.length; at += 0x8000) {
    out += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  }
  return btoa(out);
}

export function decode(data) {
  if (typeof data !== "string" || data.length % 4 !== 0 || !BASE64.test(data)) {
    throw badRequest("A piece of a file was not base64.");
  }
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// -- coming in --

export class Stash {
  constructor({ clock, fileMax = FILE_MAX, heldMax = STASH_MAX, transfersMax = TRANSFERS_MAX, keepMs = KEEP_MS }) {
    this.clock = clock;
    this.limits = { fileMax, heldMax, transfersMax, keepMs };   // as they are; tests make them small
    this.transfers = new Map();   // xfer -> {chunks: [Uint8Array], size, done, at}
    this.held = 0;
    this.timer = null;
  }

  /** One `blob` request: the piece `seq` of `xfer`, `last` when it is the end. */
  put({ xfer, seq, data, last }) {
    this.sweep();
    if (typeof xfer !== "string" || !NAME.test(xfer)) {
      throw badRequest("That file has no usable name.");
    }
    if (!Number.isInteger(seq) || seq < 0 || typeof last !== "boolean") {
      throw badRequest("A piece of a file has no place in it.");
    }
    const bytes = decode(data);
    if (bytes.length > CHUNK_MAX) {
      throw tooBig("A piece of a file was bigger than it may be.");
    }
    let transfer = this.transfers.get(xfer);
    if (!transfer) {
      if (seq !== 0) {
        throw badRequest("A file did not start at its first piece.");
      }
      if (this.transfers.size >= this.limits.transfersMax) {
        throw engineError("Thunderbird is holding too many files already.");
      }
      transfer = { chunks: [], size: 0, done: false, at: this.clock.now() };
      this.transfers.set(xfer, transfer);
    }
    const fail = error => {
      this.drop(xfer);
      throw error;
    };
    if (transfer.done || seq !== transfer.chunks.length) {
      fail(badRequest("A file's pieces came out of order."));
    }
    if (transfer.size + bytes.length > this.limits.fileMax) {
      fail(tooBig("That file is too big to send."));
    }
    if (this.held + bytes.length > this.limits.heldMax) {
      fail(tooBig("Thunderbird is holding too much of other files to take this one."));
    }
    transfer.chunks.push(bytes);
    transfer.size += bytes.length;
    transfer.done = last;
    transfer.at = this.clock.now();
    this.held += bytes.length;
    this.arm();
  }

  /** The whole files for these transfers: {xfer: {chunks, size}}. A file that is missing or not whole is an error. */
  take(xfers) {
    this.sweep();
    const out = new Map();
    for (const xfer of xfers) {
      const transfer = this.transfers.get(xfer);
      if (!transfer || !transfer.done) {
        throw badRequest("An attachment did not arrive whole. Nothing was sent.");
      }
      out.set(xfer, transfer);
    }
    return out;
  }

  drop(xfer) {
    const transfer = this.transfers.get(xfer);
    if (transfer) {
      this.held -= transfer.size;
      this.transfers.delete(xfer);
    }
  }

  /** Forget the transfers that have waited too long. */
  sweep() {
    const now = this.clock.now();
    for (const [xfer, transfer] of this.transfers) {
      if (now - transfer.at >= this.limits.keepMs) {
        this.drop(xfer);
      }
    }
  }

  arm() {
    if (this.timer === null && this.transfers.size > 0) {
      this.timer = this.clock.setTimeout(() => {
        this.timer = null;
        this.sweep();
        this.arm();
      }, SWEEP_MS);
    }
  }

  close() {
    if (this.timer !== null) {
      this.clock.clearTimeout(this.timer);
      this.timer = null;
    }
    this.transfers.clear();
    this.held = 0;
  }
}

/** A name for a file that is safe to put in a header and on disk: no path, no control characters. */
export function fileName(name) {
  const clean = String(name ?? "")
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ")
    .split(/[\\/]/)
    .pop()
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 200);
  return clean && clean !== "." && clean !== ".." ? clean : "attachment";
}

/** A content type that is only that; anything else is "application/octet-stream". */
export function contentType(type) {
  return typeof type === "string" && /^[A-Za-z0-9][A-Za-z0-9.+-]{0,60}\/[A-Za-z0-9][A-Za-z0-9.+-]{0,100}$/.test(type)
    ? type.toLowerCase()
    : "application/octet-stream";
}

// -- going out --

const STREAMS_MAX = 4;

/** Files being sent out, one after the other: the pieces of two files never take turns on the one link. */
export class Outgoing {
  constructor(log = () => {}) {
    this.log = log;
    this.pending = 0;
    this.tail = Promise.resolve();
  }

  /** Run `fn` after the ones before it; false, and `fn` never runs, when too many are waiting. */
  start(fn) {
    if (this.pending >= STREAMS_MAX) {
      return false;
    }
    this.pending++;
    this.tail = this.tail
      .then(fn)
      .catch(e => this.log("file", e))
      .finally(() => this.pending--);
    return true;
  }
}

/**
 * `attachment`: the answer is what the file is and the name of its transfer; the pieces follow as `blob`
 * events, in order, once the answer is on its way (`ctx.after`). A file over the limit is refused before the
 * first piece. A piece that cannot be read ends the transfer, and the service, which waits for the next one
 * for thirty seconds, gives up on it and says so.
 */
export async function attachment({ messenger, mailbox, clock, outgoing, randomId }, args, ctx) {
  const { account, key, part } = args;
  if (typeof part !== "string" || !part || part.length > 64) {
    throw badRequest("“part” is missing or not text.");
  }
  const { header } = await mailbox.locate(account, key);
  const listed = await messenger.messages.listAttachments(header.id);
  const found = (Array.isArray(listed) ? listed : []).find(a => a.partName === part);
  if (!found) {
    throw notFound("That attachment is not in the message.");
  }
  let file;
  try {
    file = await messenger.messages.getAttachmentFile(header.id, part);
  } catch {
    throw engineError("Thunderbird could not read that attachment.");
  }
  if (file.size > FILE_MAX) {
    throw tooBig("That file is too big to save here.");
  }
  const xfer = randomId();
  if (outgoing.pending >= STREAMS_MAX) {
    throw engineError("Thunderbird is already sending several files.");
  }
  ctx.after(() => outgoing.start(() => stream({ clock, emit: ctx.emit }, xfer, file)));
  return {
    name: fileName(found.name || file.name),
    content_type: contentType(found.contentType || file.type),
    size: file.size,
    xfer,
  };
}

/** The file as `blob` events: pieces of CHUNK_MAX, a turn of the event loop between them. */
export async function stream({ clock, emit }, xfer, file) {
  let at = 0;
  for (let seq = 0; ; seq++) {
    const end = Math.min(file.size, at + CHUNK_MAX);
    const bytes = new Uint8Array(await file.slice(at, end).arrayBuffer());
    const last = end >= file.size;
    if (!emit({ event: "blob", xfer, seq, data: encode(bytes), last })) {
      return false;   // the link is gone; the service's side has given the transfer up
    }
    if (last) {
      return true;
    }
    at = end;
    await pause(clock, 0);
  }
}

/*
 * `list` and `get`: a folder's messages a page at a time, and one message's words.
 *
 * `list` is newest first and `before` is inclusive (a message at that very second is shown again, and the
 * service drops the ones it has). The first page of a folder is read from `messages.list`, which Thunderbird
 * sorts itself, so a folder with a hundred thousand messages costs about what one with a hundred does. A page
 * that is not that, one of the unread ones or one that starts at `before`, would mean paging through every
 * message on the way at a millisecond each, so it is found by time windows that Thunderbird filters itself
 * (newest.js). A page that is not full when the time given to it is up is said to have more, which is true,
 * and the next page starts after the last message of this one; when not even one window could be read in
 * time nothing is said of the folder but that it took too long, since a page of messages that are not the
 * newest would hide the ones between.
 *
 * `get` reads a message with `messages.getFull` and leaves it unread (nothing here sets a read flag). The
 * words are the text/plain parts, and the HTML only when there is none, so the service never has to carry
 * both. Parts that are attachments, and attached messages, are not part of the words. Encrypted mail is
 * not decrypted: that could ask for a passphrase in a window nobody sees.
 */

import { limit, optBool, optNumber, text } from "./args.js";
import { withDeadline } from "./clock.js";
import { badRequest, engineError } from "./errors.js";
import { isInline } from "./mailbox.js";
import { Newest, scan, windowQuery, windows } from "./newest.js";
import { folderStream, newestFirst } from "./streams.js";

export const KINDS = ["inbox", "sent", "drafts", "archive", "trash", "other"];
const LIST_MAX = 500;
const LIST_BUDGET_MS = 5000;           // and then what is listed is looked at for attachments, up to 3 s more
const READ_BUDGET_MS = 8000;
const UNREAD_WIDE_MAX = 400;           // this many unread messages or fewer are asked for in one query, not by window
const TEXT_CAP = 1_000_000;           // characters of a message's words that are handed over
const HTML_CAP = 2_000_000;
const ATTACHED_MAX = 4;                // attached messages whose real size is looked up
const ATTACHED_MS = 2000;
const RAW_HEAD = 64 * 1024;           // how much of a message is read to find its headers, when it has to be
const WANTED = ["message-id", "in-reply-to", "references", "reply-to"];

/** The ops are given a context (`ctx.cancelled()` says the request was answered already); a caller may give none. */
const unasked = { cancelled: () => false };

/** How many unread messages these folders have, or Infinity when Thunderbird does not say. */
async function unreadIn(messenger, folders) {
  try {
    const infos = await Promise.all(folders.map(folder => messenger.folders.getFolderInfo(folder.id)));
    return infos.reduce((sum, info) => sum + (Number.isFinite(info.unreadMessageCount) ? info.unreadMessageCount : Infinity), 0);
  } catch {
    return Infinity;
  }
}

/** The first page of the folders as they stand, newest first: what `messages.list` sorts. */
async function firstPage({ messenger, clock }, folders, count, end, ctx) {
  const streams = folders.map(folder => folderStream(messenger, folder.id));
  const stream = streams.length === 1 ? streams[0] : newestFirst(streams);
  const headers = [];
  let more = false;
  for await (const header of stream) {
    if (headers.length === count) {
      more = true;
      break;
    }
    headers.push(header);
    if (ctx.cancelled() || clock.now() > end) {
      more = true;
      break;
    }
  }
  return { headers, more };
}

/** The page of the unread messages, or of those no newer than `top` (ms), found window by window. */
async function windowed(env, folders, { count, unread, top }, end, ctx) {
  const { clock, messenger } = env;
  const ids = folders.map(folder => folder.id);
  const few = unread && (await unreadIn(messenger, folders)) <= UNREAD_WIDE_MAX;
  const spans = few ? [{ from: -Infinity, to: top ?? Infinity }] : windows(undefined, top, clock.now());
  const found = [];
  let more = false;
  for (const span of spans) {
    if (ctx.cancelled()) {
      break;
    }
    const piece = new Newest(count + 1 - found.length);
    const query = windowQuery(span, ids, unread ? { unread: true } : {});
    const whole = await scan(env, query, piece, { end, cancelled: ctx.cancelled, accept: header => header.date.getTime() <= (top ?? Infinity) });
    if (!whole) {
      if (found.length === 0) {
        throw engineError("Thunderbird took too long over that.");
      }
      more = true;
      break;
    }
    found.push(...piece.items);
    if (found.length > count) {
      more = true;
      break;
    }
  }
  return { headers: found.slice(0, count), more };
}

export async function list(env, args, ctx = unasked) {
  const { clock, mailbox } = env;
  const account = text(args, "account");
  const kind = text(args, "folder", 20);
  if (!KINDS.includes(kind)) {
    throw badRequest("That is not a kind of folder.");
  }
  const unread = optBool(args, "unread") === true;
  const before = optNumber(args, "before");
  const count = limit(args, 50, LIST_MAX);
  const folders = await mailbox.foldersOf(account, kind);
  if (folders.length === 0) {
    return { messages: [], more: false };
  }
  const end = clock.now() + LIST_BUDGET_MS;
  const { headers, more } =
    unread || before !== undefined
      ? await windowed(env, folders, { count, unread, top: before === undefined ? undefined : Math.round(before * 1000) }, end, ctx)
      : await firstPage(env, folders, count, end, ctx);
  return { messages: await mailbox.emsgs(headers), more };
}

// -- get --

const attachmentPart = part => {
  const disposition = String(part.headers?.["content-disposition"]?.[0] ?? "");
  return /^\s*attachment/i.test(disposition) || (Boolean(part.name) && !/^\s*inline/i.test(disposition));
};

/** The text/plain and text/html parts of a message that are its words. `root` is the message itself. */
export function bodies(root) {
  const found = { plain: [], html: [] };
  const visit = part => {
    const type = String(part.contentType ?? "").toLowerCase();
    if (type.startsWith("multipart/")) {
      part.parts?.forEach(visit);
    } else if (type !== "message/rfc822" && !attachmentPart(part) && typeof part.body === "string") {
      if (type === "text/plain") {
        found.plain.push(part.body);
      } else if (type === "text/html") {
        found.html.push(part.body);
      }
    }
  };
  root.parts?.forEach(visit);
  return found;
}

/** The four headers the service wants, as Thunderbird has them (angle brackets and all), "" when absent. */
export function wantedHeaders(dictionary) {
  const out = {};
  for (const name of WANTED) {
    const value = dictionary?.[name];
    out[name] = Array.isArray(value) ? String(value[0] ?? "") : typeof value === "string" ? value : "";
  }
  return out;
}

/** The headers out of the start of a raw message: unfolded, only the wanted ones, nothing else kept. */
export function rawHeaders(head) {
  const block = head.split(/\r?\n\r?\n/, 1)[0].replace(/\r?\n[ \t]+/g, " ");
  const dictionary = {};
  for (const line of block.split(/\r?\n/)) {
    const colon = line.indexOf(":");
    const name = line.slice(0, colon).trim().toLowerCase();
    if (colon > 0 && WANTED.includes(name) && !(name in dictionary)) {
      dictionary[name] = [line.slice(colon + 1).trim()];
    }
  }
  return dictionary;
}

const ids = value => String(value).match(/<[^<>\s]+>/g)?.map(id => id.slice(1, -1)) ?? [];

/** What conversation a message is in: the first message of its References, else what it answers, else itself. */
export function threadOf(headers) {
  return ids(headers.references)[0] ?? ids(headers["in-reply-to"])[0] ?? ids(headers["message-id"])[0] ?? null;
}

/**
 * The sizes of the attached messages (message/rfc822), by part. Thunderbird lists such a part with the size of its
 * headers (18 bytes for one of 284), so the file is asked for, a few at most and for a short time; what cannot be
 * had in that time is listed as Thunderbird gave it.
 */
async function wholeSizes(messenger, clock, id, listed) {
  const sizes = new Map();
  const attached = listed.filter(a => String(a.contentType).toLowerCase() === "message/rfc822" && a.partName).slice(0, ATTACHED_MAX);
  await Promise.all(
    attached.map(async a => {
      try {
        const file = await withDeadline(clock, messenger.messages.getAttachmentFile(id, a.partName), ATTACHED_MS, () => new Error("slow"));
        sizes.set(a.partName, file.size);
      } catch {
        // listed as Thunderbird gave it
      }
    })
  );
  return sizes;
}

export async function get({ messenger, clock, mailbox }, args) {
  const account = text(args, "account");
  const key = text(args, "key", 600);
  const { header } = await mailbox.locate(account, key);
  const read = async () => {
    const [full, listed] = await Promise.all([
      messenger.messages.getFull(header.id, { decrypt: false }),
      Promise.resolve(messenger.messages.listAttachments(header.id)).catch(() => []),
    ]);
    let dictionary = full.headers;
    if (!dictionary || Object.keys(dictionary).length === 0) {
      const file = await messenger.messages.getRaw(header.id, { data_format: "File" });
      dictionary = rawHeaders(await file.slice(0, RAW_HEAD).text());
    }
    return { full, listed, dictionary };
  };
  const { full, listed, dictionary } = await withDeadline(clock, read(), READ_BUDGET_MS, () =>
    engineError("Thunderbird did not read that message in time.")
  ).catch(e => {
    throw e && e.code ? e : engineError("Thunderbird could not read that message just now.");
  });
  const { plain, html } = bodies(full);
  const words = plain.join("\n\n");
  const given = (Array.isArray(listed) ? listed : []).slice(0, 200);
  const sizes = await wholeSizes(messenger, clock, header.id, given);
  const attachments = given.map(a => ({
    part: String(a.partName ?? ""),
    name: String(a.name ?? ""),
    content_type: String(a.contentType ?? ""),
    size: sizes.get(a.partName) ?? (Number.isFinite(a.size) ? a.size : 0),
    inline: isInline(a),
  }));
  const headers = wantedHeaders(dictionary);
  const [message] = await mailbox.emsgs([header], { probe: false });
  message.attachments = attachments.some(a => !a.inline);
  message.thread = threadOf(headers);
  mailbox.remember(account, key, header.id, message.attachments);
  return {
    message,
    text: plain.length ? words.slice(0, TEXT_CAP) : null,
    html: words.trim() || !html.length ? null : html.join("\n").slice(0, HTML_CAP),
    headers,
    attachments,
  };
}

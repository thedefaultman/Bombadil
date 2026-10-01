/*
 * `list` and `get`: a folder's messages a page at a time, and one message's words.
 *
 * `list` is newest first and `before` is inclusive (a message at that very second is shown again, and the
 * service drops the ones it has). Thunderbird sorts the folder, this reads pages until the page asked for is
 * full and stops; a folder with a hundred thousand messages costs the same as one with a hundred, except for
 * the messages skipped on the way to `before`. A page that is not full when the time given to it is up is
 * said to have more, which is true, and the next page starts after the last message of this one.
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
import { folderStream, newestFirst } from "./streams.js";

export const KINDS = ["inbox", "sent", "drafts", "archive", "trash", "other"];
const LIST_MAX = 500;
const LIST_BUDGET_MS = 5000;           // and then what is listed is looked at for attachments, up to 3 s more
const READ_BUDGET_MS = 8000;
const TEXT_CAP = 1_000_000;           // characters of a message's words that are handed over
const HTML_CAP = 2_000_000;
const RAW_HEAD = 64 * 1024;           // how much of a message is read to find its headers, when it has to be
const WANTED = ["message-id", "in-reply-to", "references", "reply-to"];

export async function list({ messenger, clock, mailbox }, args) {
  const account = text(args, "account");
  const kind = text(args, "folder", 20);
  if (!KINDS.includes(kind)) {
    throw badRequest("That is not a kind of folder.");
  }
  const unread = optBool(args, "unread");
  const before = optNumber(args, "before");
  const count = limit(args, 50, LIST_MAX);
  const folders = await mailbox.foldersOf(account, kind);
  if (folders.length === 0) {
    return { messages: [], more: false };
  }
  const streams = folders.map(folder => folderStream(messenger, folder.id));
  const stream = streams.length === 1 ? streams[0] : newestFirst(streams);
  const newest = before === undefined ? Infinity : Math.round(before * 1000);
  const end = clock.now() + LIST_BUDGET_MS;
  const headers = [];
  let more = false;
  for await (const header of stream) {
    if (header.date.getTime() > newest || (unread && header.read)) {
      continue;
    }
    if (headers.length === count) {
      more = true;
      break;
    }
    headers.push(header);
    if (clock.now() > end) {
      more = true;
      break;
    }
  }
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
  const attachments = (Array.isArray(listed) ? listed : []).slice(0, 200).map(a => ({
    part: String(a.partName ?? ""),
    name: String(a.name ?? ""),
    content_type: String(a.contentType ?? ""),
    size: Number.isFinite(a.size) ? a.size : 0,
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

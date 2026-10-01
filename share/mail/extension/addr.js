/*
 * Mailboxes: the `Name <address>` strings Thunderbird gives, and the ones it is given.
 *
 * Thunderbird hands each address of a message over as its own string (`author` is one, `recipients` and
 * `ccList` are arrays of one each), already decoded, with a name that needs quoting quoted. So a single
 * mailbox is all this has to read, and it does so without asking Thunderbird (one call per address would be
 * three hundred for a page of a hundred mails). What it cannot make sense of is kept as it is, in the
 * `email` slot, so the person still sees something.
 */

import { badRequest, oneLine } from "./errors.js";

const CONTROL = /[\u0000-\u001f\u007f-\u009f\u2028\u2029]/;

function unquote(text) {
  const name = text.trim();
  const quoted = name.length >= 2 && (name[0] === '"' || name[0] === "'") && name.at(-1) === name[0];
  return (quoted ? name.slice(1, -1).replace(/\\(.)/g, "$1") : name).replace(/\s+/g, " ").trim();
}

/** `Name <a@b.example>`, `a@b.example`, `<a@b.example>` or `a@b.example (Name)` as {name, email}. */
export function parseMailbox(text) {
  const raw = String(text ?? "").trim();
  if (!raw) {
    return { name: "", email: "" };
  }
  if (raw.endsWith(">")) {
    const open = raw.lastIndexOf("<");
    if (open >= 0) {
      return { name: unquote(raw.slice(0, open)), email: raw.slice(open + 1, -1).trim().toLowerCase() };
    }
  }
  if (!/\s/.test(raw) && raw.includes("@")) {
    return { name: "", email: raw.toLowerCase() };
  }
  const commented = /^(\S+@\S+)\s*\((.*)\)$/.exec(raw);
  if (commented) {
    return { name: unquote(commented[2]), email: commented[1].toLowerCase() };
  }
  return { name: "", email: raw };
}

export const parseMailboxes = list =>
  (Array.isArray(list) ? list : []).map(parseMailbox).filter(a => a.email || a.name);

/** The address of one mailbox, lower case, "" when there is none. */
export const emailOf = text => parseMailbox(text).email;

const NOT_AN_ADDRESS = /[\s<>,;:"\\()[\]]/;

/**
 * A recipient as the service gave it ({name, email} or a string) made safe to hand to Thunderbird, or a
 * `bad_request`. Nothing with a line break or another control character gets through, because a recipient
 * ends up in a header.
 */
export function recipient(item) {
  const one = typeof item === "string" ? parseMailbox(item) : { name: item?.name ?? "", email: item?.email ?? "" };
  const name = typeof one.name === "string" ? one.name : "";
  const email = typeof one.email === "string" ? one.email.trim().toLowerCase() : "";
  const bad =
    !/^[^@]+@[^@]+\.[^@]+$/.test(email) || NOT_AN_ADDRESS.test(email) || CONTROL.test(email) || CONTROL.test(name);
  if (bad) {
    throw badRequest(`“${oneLine(typeof item === "string" ? item : email, 60)}” is not an email address.`);
  }
  return { name: name.replace(/\s+/g, " ").trim(), email };
}

/** The string Thunderbird takes for a recipient: the name always quoted, so no name can end the address early. */
export function formatRecipient({ name, email }) {
  return name ? `"${name.replace(/(["\\])/g, "\\$1")}" <${email}>` : email;
}

/** The recipients of a request as a list (it may be missing, or be one), each checked. */
export function recipients(value, what) {
  if (value === undefined || value === null || value === "") {
    return [];
  }
  const items = Array.isArray(value) ? value : [value];
  if (items.length > 200) {
    throw badRequest(`“${what}” has too many addresses.`);
  }
  return items.map(recipient);
}

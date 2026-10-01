/*
 * `find` and `known`: looking for mail, and for people the person has dealt with.
 *
 * The newest matches are wanted first, and a mailbox is not read in full to find them, so a search goes through
 * time in windows (newest.js): the last day, then a week, a month and so on, each a query of its own, and the
 * older ones are never asked once the newer have made enough. The whole search has a time of its own, under
 * the service's, after which what has been found is answered and `partial` says so.
 *
 * What Thunderbird can filter on itself it is left to: dates (as Date objects: a string or a number makes the
 * call never answer), unread, flagged, attachments, and a sender or recipient that is a whole address.
 * Anything else about people and subject is matched here as a part of the text, without regard to case (its
 * own text matching is case-sensitive: "invoice" does not find "Invoice 4711").
 *
 * The words of a mail are the exception. Reading every body of a window into the add-on would mean decoding
 * and handing over each message, about five milliseconds apiece and mail made to be slow to read; Thunderbird's
 * own `body` matching reads the stored message, turns HTML into text itself and costs about one. It is
 * case-sensitive, so a text is asked for as it was given, in lower case, with its first letter capital, with
 * each word's and in capitals, which is how words and names are written; a mail that has it in another case (CamelCase) is found by
 * its subject or people only. A search that runs out of its time before every window was read says `partial`.
 *
 * A mail that is in several folders (Gmail's All Mail and labels, a mail sent to oneself) is one result, the
 * inbox's copy, not several that use up what was asked for.
 */

import { limit, optBool, optList, optNumber, optText } from "./args.js";
import { emailOf } from "./addr.js";
import { withDeadline } from "./clock.js";
import { badRequest } from "./errors.js";
import { Newest, scan, windowQuery, windows } from "./newest.js";
import { folderStream, newestFirst } from "./streams.js";
import { KINDS } from "./reading.js";
import { pool } from "./mailbox.js";

export { windows };

const FIND_MAX = 200;
const FIND_BUDGET_MS = 5500;           // the service gives up on a search at 10 s; the answer takes up to 3 s more
const KNOWN_BUDGET_MS = 4000;          // and on `known` at 5
const SENT_SCAN_MAX = 3000;
const DEFAULT_KINDS = ["inbox", "archive", "sent", "other"];
const ADDRESS = /^[^\s@<>,;"]+@[^\s@<>,;"]+\.[^\s@<>,;"]+$/;

const unasked = { cancelled: () => false };

const squeeze = text => text.replace(/\s+/g, " ").trim();

function criteria(args) {
  const given = name => squeeze(optText(args, name, 300) ?? "") || undefined;
  const lower = name => given(name)?.toLowerCase();
  const kinds = optList(args, "folders") ?? DEFAULT_KINDS;
  for (const kind of kinds) {
    if (typeof kind !== "string" || ![...KINDS, "junk"].includes(kind)) {
      throw badRequest("That is not a kind of folder.");
    }
  }
  const accounts = optList(args, "accounts");
  if (accounts && accounts.some(id => typeof id !== "string" || !id)) {
    throw badRequest("“accounts” is a list of accounts.");
  }
  const since = optNumber(args, "since");
  const until = optNumber(args, "until");
  return {
    accounts,
    kinds,
    text: lower("text"),
    typed: given("text"),
    from: lower("from"),
    to: lower("to"),
    subject: lower("subject"),
    unread: optBool(args, "unread"),
    flagged: optBool(args, "flagged"),
    attachments: optBool(args, "attachments"),
    since: since === undefined ? undefined : Math.round(since * 1000),
    until: until === undefined ? undefined : Math.round(until * 1000),
    limit: limit(args, 50, FIND_MAX),
  };
}

const people = header => [header.author, ...(header.recipients ?? []), ...(header.ccList ?? [])];

/** Whether the header has the text in its subject or among the people it names. */
const headerHas = (header, needle) => [header.subject, ...people(header)].join("\n").toLowerCase().includes(needle);

/** The filters Thunderbird is not asked to do. */
function passes(header, c) {
  if (c.from && !ADDRESS.test(c.from) && !String(header.author).toLowerCase().includes(c.from)) {
    return false;
  }
  if (c.to && !ADDRESS.test(c.to)) {
    const to = [...(header.recipients ?? []), ...(header.ccList ?? []), ...(header.bccList ?? [])];
    if (!to.join("\n").toLowerCase().includes(c.to)) {
      return false;
    }
  }
  return !c.subject || String(header.subject).toLowerCase().includes(c.subject);
}

/** What Thunderbird filters on in every query of a search. */
function nativeFilters(c) {
  const filters = {};
  for (const [name, value] of [["unread", c.unread], ["flagged", c.flagged], ["attachment", c.attachments]]) {
    if (value !== undefined) {
      filters[name] = value;
    }
  }
  if (c.from && ADDRESS.test(c.from)) {
    filters.author = c.from;
  }
  if (c.to && ADDRESS.test(c.to)) {
    filters.recipients = c.to;
  }
  return filters;
}

/** The ways a text is asked for in a body, which Thunderbird matches letter for letter. */
function written(c) {
  const lower = c.text;
  const title = lower.replace(/(^|[\s(\[{"'-])(\p{L})/gu, (_, before, letter) => before + letter.toUpperCase());
  return [...new Set([c.typed, lower, lower.charAt(0).toUpperCase() + lower.slice(1), title, lower.toUpperCase()])];
}

/**
 * One window's matches into `piece`. Whether the window was read to the end: it is not when time is up,
 * and then what `piece` has is what was found, which may not be all there is.
 */
async function searchWindow(env, c, folderIds, span, piece, end, ctx) {
  const filters = nativeFilters(c);
  const run = (extra, accept) =>
    scan(env, windowQuery(span, folderIds, { ...filters, ...extra }), piece, { end, cancelled: ctx.cancelled, accept });
  if (!(await run({}, header => passes(header, c) && (!c.text || headerHas(header, c.text))))) {
    return false;
  }
  if (c.text) {
    for (const variant of written(c)) {
      if (ctx.cancelled() || env.clock.now() > end) {
        return false;
      }
      if (!(await run({ body: variant }, header => passes(header, c)))) {
        return false;
      }
    }
  }
  return true;
}

export async function find(env, args, ctx = unasked) {
  const { clock, mailbox } = env;
  const c = criteria(args);
  const accountIds = c.accounts ?? (await mailbox.accounts()).map(account => account.id);
  const folderIds = [];
  const inboxes = new Set();
  for (const account of accountIds) {
    for (const kind of c.kinds) {
      try {
        for (const folder of await mailbox.foldersOf(account, kind)) {
          folderIds.push(folder.id);
          if (kind === "inbox") {
            inboxes.add(folder.id);
          }
        }
      } catch (e) {
        if (e.code !== "not_found") {
          throw e;
        }
      }
    }
  }
  if (folderIds.length === 0) {
    return { messages: [] };
  }
  const end = clock.now() + FIND_BUDGET_MS;
  const rank = header => (inboxes.has(header.folder?.id) ? 0 : 1);
  const found = [];
  let partial = false;
  for (const span of windows(c.since, c.until, clock.now())) {
    if (ctx.cancelled()) {
      break;
    }
    if (clock.now() > end) {
      partial = true;
      break;
    }
    const piece = new Newest(c.limit - found.length, { distinct: true, rank });
    const whole = await searchWindow(env, c, folderIds, span, piece, end, ctx);
    found.push(...piece.items);
    if (!whole) {
      partial = true;
      break;
    }
    if (found.length >= c.limit) {
      break;
    }
  }
  const result = { messages: await mailbox.emsgs(found.slice(0, c.limit)) };
  if (partial) {
    result.partial = true;
  }
  return result;
}

// -- known --

const emailFields = key => /e-?mail/i.test(key);

const hasEmail = (contact, email) =>
  Object.entries(contact.properties ?? {}).some(
    ([key, value]) => emailFields(key) && typeof value === "string" && value.trim().toLowerCase() === email
  );

/**
 * Which of these addresses the person has dealt with: one is in an address book (the collected addresses
 * are one), or has been written to from a Sent folder. Address books are searched on this machine only
 * (a remote one could mean the network), and the Sent folders newest first, at most SENT_SCAN_MAX
 * messages of them and no longer than a few seconds.
 */
export async function known({ messenger, clock, mailbox }, args, ctx = unasked) {
  const given = optList(args, "emails", 500);
  if (!given || given.some(e => typeof e !== "string" || !e || e.length > 320)) {
    throw badRequest("“emails” is a list of addresses.");
  }
  const end = clock.now() + KNOWN_BUDGET_MS;
  const found = new Set();
  const wanted = new Set(given.map(e => e.trim().toLowerCase()));
  await pool([...wanted], 6, async email => {
    if (clock.now() > end || ctx.cancelled()) {
      return;
    }
    try {
      const contacts = await withDeadline(
        clock,
        messenger.contacts.quickSearch({ searchString: email, includeRemote: false }),
        end - clock.now(),
        () => new Error("slow")
      );
      if (contacts.some(contact => hasEmail(contact, email))) {
        found.add(email);
      }
    } catch {
      // no address book to ask: not known from there
    }
  });
  const sent = [];
  if (found.size < wanted.size) {
    for (const account of await mailbox.accounts()) {
      sent.push(...(await mailbox.foldersOf(account.id, "sent")));
    }
    let looked = 0;
    for await (const header of newestFirst(sent.map(folder => folderStream(messenger, folder.id)))) {
      for (const address of [...(header.recipients ?? []), ...(header.ccList ?? []), ...(header.bccList ?? [])]) {
        found.add(wanted.has(emailOf(address)) ? emailOf(address) : "");
      }
      found.delete("");
      if (found.size >= wanted.size || ++looked >= SENT_SCAN_MAX || clock.now() > end || ctx.cancelled()) {
        break;
      }
    }
  }
  return Object.fromEntries(given.map(e => [e, found.has(e.trim().toLowerCase())]));
}

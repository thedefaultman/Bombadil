/*
 * `find` and `known`: looking for mail, and for people the person has dealt with.
 *
 * Thunderbird's `messages.query` walks every header of the folders it is given, one at a time, whatever it
 * is asked, and what it finds comes back in no order. Two things follow, and the shape of `find` is the
 * answer to them.
 *
 * - The newest matches are wanted first, and a mailbox is not read in full to find them. So the search goes
 *   through time in windows, the last day, then a week, a month, three months, a year, three years, and
 *   everything older, each a separate query that only looks at its own dates. A window keeps its newest
 *   matches and no more; when the windows so far have made enough, the older ones are never asked. And the
 *   whole search has a time of its own, under the service's, after which what has been found is answered
 *   and `partial` says so.
 * - `query`'s own text matching is case-sensitive ("invoice" does not find "Invoice 4711"). Text is
 *   therefore matched here, without regard to case, in the subject, the people and, for the newest mails
 *   only, the body (read with `getFull`, at most BODY_SCAN_MAX of them per search).
 *
 * What Thunderbird can filter on its own is left to it: dates (as Date objects: a string or a number makes
 * the call never answer), unread, flagged, attachments, and a sender or recipient that is a whole address.
 * Anything else about people and subject is matched here as a part of the text.
 */

import { limit, optBool, optList, optNumber, optText } from "./args.js";
import { emailOf } from "./addr.js";
import { withDeadline } from "./clock.js";
import { badRequest } from "./errors.js";
import { abort, folderStream, newestFirst } from "./streams.js";
import { bodies, KINDS } from "./reading.js";
import { pool } from "./mailbox.js";

const FIND_MAX = 200;
const FIND_BUDGET_MS = 5500;           // the service gives up on a search at 10 s; the answer takes up to 3 s more
const KNOWN_BUDGET_MS = 4000;          // and on `known` at 5
const BODY_SCAN_MAX = 300;
const BODY_BATCH = 4;
const BODY_CHARS = 200_000;
const SENT_SCAN_MAX = 3000;
const DAY = 86_400_000;
const WINDOW_DAYS = [1, 7, 30, 90, 365, 1095];
const DEFAULT_KINDS = ["inbox", "archive", "sent", "other"];
const ADDRESS = /^[^\s@<>,;"]+@[^\s@<>,;"]+\.[^\s@<>,;"]+$/;

/** The time ranges to search, newest first, each as {from, to} in milliseconds, both ends inclusive. */
export function windows(since, until, now) {
  const top = until ?? Infinity;
  const low = since ?? -Infinity;
  const out = [];
  let to = top;
  for (const edge of WINDOW_DAYS.map(days => now - days * DAY).filter(edge => edge < top && edge > low)) {
    out.push({ from: edge, to });
    to = edge - 1;
  }
  out.push({ from: low, to });
  return out.filter(window => window.from <= window.to);
}

function criteria(args) {
  const lower = name => squeeze(optText(args, name, 300) ?? "").toLowerCase() || undefined;
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

/** Put `header` in `kept` (newest first), which holds at most `max`. */
function keepNewest(kept, header, max) {
  const at = header.date.getTime();
  let i = kept.length;
  while (i > 0 && kept[i - 1].date.getTime() < at) {
    i--;
  }
  if (i < max) {
    kept.splice(i, 0, header);
    kept.length = Math.min(kept.length, max);
  }
}

const squeeze = text => text.replace(/\s+/g, " ").trim();

const htmlText = html =>
  html
    .replace(/<(style|script)[\s\S]*?<\/\1>/gi, " ")
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&amp;/gi, "&");

async function bodyHas(messenger, header, needle) {
  try {
    const { plain, html } = bodies(await messenger.messages.getFull(header.id, { decrypt: false }));
    const body = plain.length ? plain.join("\n") : html.map(htmlText).join("\n");
    return squeeze(body.slice(0, BODY_CHARS)).toLowerCase().includes(needle);
  } catch {
    return false;
  }
}

/** One window's matches: those that matched in the header (up to `max`) and the others, for a look at the body. */
async function collect({ messenger, clock }, c, folderIds, window, end) {
  const query = { folderId: folderIds };
  if (window.from > -Infinity) {
    query.fromDate = new Date(window.from);
  }
  if (window.to < Infinity) {
    query.toDate = new Date(window.to);
  }
  for (const [name, value] of [["unread", c.unread], ["flagged", c.flagged], ["attachment", c.attachments]]) {
    if (value !== undefined) {
      query[name] = value;
    }
  }
  if (c.from && ADDRESS.test(c.from)) {
    query.author = c.from;
  }
  if (c.to && ADDRESS.test(c.to)) {
    query.recipients = c.to;
  }
  const direct = [];
  const rest = [];
  let timedOut = false;
  let page = await messenger.messages.query(query);
  try {
    while (true) {
      for (const header of page.messages) {
        if (!passes(header, c)) {
          continue;
        }
        if (!c.text || headerHas(header, c.text)) {
          keepNewest(direct, header, c.limit);
        } else {
          keepNewest(rest, header, BODY_SCAN_MAX);
        }
      }
      if (!page.id) {
        break;
      }
      if (clock.now() > end) {
        timedOut = true;
        break;
      }
      page = await messenger.messages.continueList(page.id);
    }
  } finally {
    if (page && page.id) {
      abort(messenger, page.id);
    }
  }
  return { direct, rest, timedOut };
}

export async function find(env, args) {
  const { messenger, clock, mailbox } = env;
  const c = criteria(args);
  const accountIds = c.accounts ?? (await mailbox.accounts()).map(account => account.id);
  const folderIds = [];
  for (const account of accountIds) {
    for (const kind of c.kinds) {
      try {
        folderIds.push(...(await mailbox.foldersOf(account, kind)).map(folder => folder.id));
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
  const found = [];
  let partial = false;
  let bodiesLeft = BODY_SCAN_MAX;
  for (const window of windows(c.since, c.until, clock.now())) {
    if (clock.now() > end) {
      partial = true;
      break;
    }
    const need = c.limit - found.length;
    const { direct, rest, timedOut } = await collect(env, c, folderIds, window, end);
    const matches = direct.slice(0, need);
    let next = 0;
    while (c.text && next < rest.length && bodiesLeft > 0 && clock.now() < end && matches.length < 2 * need) {
      const batch = rest.slice(next, next + Math.min(BODY_BATCH, bodiesLeft));
      const edge = direct.length >= need ? direct[need - 1].date.getTime() : -Infinity;
      if (batch[0].date.getTime() < edge) {
        break;
      }
      const hits = await Promise.all(batch.map(header => bodyHas(messenger, header, c.text)));
      batch.forEach((header, i) => hits[i] && matches.push(header));
      next += batch.length;
      bodiesLeft -= batch.length;
    }
    matches.sort((a, b) => b.date.getTime() - a.date.getTime());
    found.push(...matches.slice(0, need));
    if (found.length >= c.limit) {
      break;
    }
    if (timedOut) {
      partial = true;
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
export async function known({ messenger, clock, mailbox }, args) {
  const given = optList(args, "emails", 500);
  if (!given || given.some(e => typeof e !== "string" || !e || e.length > 320)) {
    throw badRequest("“emails” is a list of addresses.");
  }
  const end = clock.now() + KNOWN_BUDGET_MS;
  const found = new Set();
  const wanted = new Set(given.map(e => e.trim().toLowerCase()));
  await pool([...wanted], 6, async email => {
    if (clock.now() > end) {
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
      if (found.size >= wanted.size || ++looked >= SENT_SCAN_MAX || clock.now() > end) {
        break;
      }
    }
  }
  return Object.fromEntries(given.map(e => [e, found.has(e.trim().toLowerCase())]));
}

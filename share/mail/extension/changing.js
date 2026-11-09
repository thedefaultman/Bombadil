/*
 * `mark` and `move`: the two things done to a message that is already there.
 *
 * Both are given a key, and the same key can name several copies of a message (Gmail keeps one mail in All
 * Mail and in each label's folder; a mail the person sent to themselves is in Sent and in the Inbox). `mark`
 * says the same thing about every copy it finds, because a copy left unread would keep the count up. `move`
 * moves one copy, the one most worth moving, and says nothing about the rest: moving a mail to the archive
 * from the inbox takes it out of the inbox, and does not empty every label it has.
 *
 * Nothing is deleted. "trash" is a move into the account's Trash folder, which is what the person would
 * do, and a mail there can be moved back. (`messages.delete` would need a permission that nothing else here
 * does, and it is the one call that can lose a message.)
 */

import { optBool, text } from "./args.js";
import { withDeadline } from "./clock.js";
import { badRequest, engineError } from "./errors.js";

const TARGETS = ["archive", "trash", "inbox"];
const COPIES_MAX = 20;
const CALL_BUDGET_MS = 8000;

// Which copy to move, by what it is now: the one the person is most likely looking at goes first, and the
// target itself last (a copy that is already there is nothing to move).
const ORDER = {
  archive: ["inbox", "other", "trash", "sent", "drafts", "archive"],
  trash: ["inbox", "other", "archive", "sent", "drafts", "trash"],
  inbox: ["archive", "trash", "other", "sent", "drafts", "inbox"],
};

const LABEL = { archive: "an Archive", trash: "a Trash", inbox: "an Inbox" };

/** Thunderbird's own call, given up on if it does not answer: the work may still happen, but the op does not hang. */
const within = (clock, promise, what) =>
  withDeadline(clock, promise, CALL_BUDGET_MS, () => engineError(`Thunderbird did not confirm ${what} in time.`));

export async function mark({ messenger, clock, mailbox }, args) {
  const account = text(args, "account");
  const key = text(args, "key", 600);
  const read = optBool(args, "read");
  const flagged = optBool(args, "flagged");
  if (read === undefined && flagged === undefined) {
    throw badRequest("Say what to change: read or flagged.");
  }
  const changes = {};
  if (read !== undefined) {
    changes.read = read;
  }
  if (flagged !== undefined) {
    changes.flagged = flagged;
  }
  const { candidates } = await mailbox.locate(account, key, undefined, { all: true });
  for (const { header } of candidates.slice(0, COPIES_MAX)) {
    const same = (read === undefined || header.read === read) && (flagged === undefined || header.flagged === flagged);
    if (!same) {
      await within(clock, messenger.messages.update(header.id, changes), "the change");
    }
  }
  return {};
}

export async function move({ messenger, clock, mailbox }, args) {
  const account = text(args, "account");
  const key = text(args, "key", 600);
  const to = text(args, "to", 20);
  if (!TARGETS.includes(to)) {
    throw badRequest("A message can be moved to the archive, the trash or the inbox.");
  }
  const order = ORDER[to];
  const { header, kind } = await mailbox.locate(account, key, k => order.indexOf(k));
  if (kind === to) {
    return {};
  }
  const [folder] = await mailbox.foldersOf(account, to);
  if (folder) {
    await within(clock, messenger.messages.move([header.id], folder.id), "the move");
  } else if (to === "archive") {
    // No archive folder yet: Thunderbird makes the one its settings call for (and files by year, if they say so).
    await within(clock, messenger.messages.archive([header.id]), "the move");
  } else {
    throw engineError(`Thunderbird has not got ${LABEL[to]} folder for that account.`);
  }
  mailbox.forget(account, key);
  return {};
}

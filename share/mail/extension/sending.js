/*
 * `send`: the one op that cannot be taken back, and so the one that is most careful.
 *
 * It runs only when the service asks (the person pressed Send), and nothing here sends on its own: no
 * listener, no timer, no window that is left open for anyone to press a button in. Everything the person
 * pressed for is in the request, and what Thunderbird is given is checked before it is allowed to send.
 *
 * Two ways in, because Thunderbird has two:
 *
 * - A new mail goes through `messages.sendMessage`, which opens no window and is the quietest way. It is an
 *   optional permission (`messages.send`) that the profile grants; without it a new mail goes the other way.
 * - A reply or a forward has to go through a compose window, since only `compose.beginReply` and
 *   `beginForward` keep the thread (`In-Reply-To`, `References`); `messages.sendMessage` drops them. The window
 *   is opened for the one send, filled with what the request says, read back and compared with it (who, what
 *   subject, what words, which files), and only then sent. If anything differs, nothing is sent. The
 *   window is closed afterwards, whatever happened, unless Thunderbird is still in the middle of a send.
 *
 * Time: the service gives a send 60 s. This one is over at 55 s from the moment the request arrived, and
 * a send that has not been begun by then (it waited its turn, or Thunderbird was slow to open a window) is
 * refused, and that is a plain failure, nothing sent. A send that was begun and has not answered by then is
 * `unknown_outcome`: Thunderbird may have sent it, and nobody retries. A call that never answered is
 * remembered (`stuck`) until it does, and another send for the same account is refused meanwhile, so that
 * one stuck dialog cannot grow into a pile of sends that all go at once when it is clicked away.
 */

import { formatRecipient, emailOf, recipients } from "./addr.js";
import { optList, optText, text } from "./args.js";
import { pause, withDeadline } from "./clock.js";
import { badRequest, engineError, EngineError, notFound, oneLine, UNKNOWN_OUTCOME } from "./errors.js";
import { contentType, fileName } from "./files.js";
import { abort } from "./streams.js";

const KINDS = ["new", "reply", "reply_all", "forward"];
export const SEND_DEADLINE_MS = 55_000;
const START_MARGIN_MS = 3000;          // a send is not begun with less time than this left
const CLOSE_MS = 3000;
const BODY_MAX = 900_000;
const SUBJECT_MAX = 2000;
const ATTACHMENTS_MAX = 20;
const TAIL_MAX = 20_000;               // what Thunderbird may add after the words (the identity's signature)
const XFER = /^[A-Za-z0-9._-]{1,128}$/;
const FILED_TRIES = 5;
const FILED_EVERY_MS = 300;
const FILED_MARGIN_MS = 4000;

// What Thunderbird says when a connection broke part-way: the mail may have gone.
const UNCERTAIN = /time(d)?[ -]?out|reset|interrupt|broken pipe|connection (was )?(lost|closed|dropped)|disconnect/i;

const sorted = items => [...new Set(items)].sort();
const collapse = value => String(value ?? "").replace(/[\s\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ").trim();
const plain = value => String(value ?? "").replace(/\r\n?/g, "\n").trimEnd();
const addresses = list => sorted((Array.isArray(list) ? list : []).map(item => (typeof item === "string" ? emailOf(item) : "?")));

function notSent(e) {
  const detail = oneLine(String(e?.message ?? e).replace(/^(messages|compose)\.sendMessage failed:\s*/i, "").replace(/^Sending FAILED!\s*/i, ""), 140);
  if (UNCERTAIN.test(detail)) {
    return new EngineError(
      UNKNOWN_OUTCOME,
      "Thunderbird lost the connection while sending, so it cannot tell whether the mail went. Look in the Sent folder before sending it again."
    );
  }
  return engineError(
    `Thunderbird could not send the mail${detail ? `: ${detail.replace(/[.\s]+$/, "")}` : ""}. Nothing was sent.`
  );
}

export class Sending {
  constructor({ messenger, clock, mailbox, stash }) {
    this.messenger = messenger;
    this.clock = clock;
    this.mailbox = mailbox;
    this.stash = stash;
    this.stuck = new Map();   // account -> how many of its sends Thunderbird has not answered
  }

  /** The request, checked and cleaned; a `bad_request` for anything that is not one. */
  read(args) {
    const kind = text(args, "kind", 20);
    if (!KINDS.includes(kind)) {
      throw badRequest("That is not a kind of mail to send.");
    }
    const replyTo = args.reply_to === undefined || args.reply_to === null ? null : text(args, "reply_to", 600);
    if ((kind === "new") !== (replyTo === null)) {
      throw badRequest(kind === "new" ? "A new mail does not answer another." : "Say which mail this answers.");
    }
    const to = recipients(args.to, "to");
    const cc = recipients(args.cc, "cc");
    const bcc = recipients(args.bcc, "bcc");
    if (to.length + cc.length + bcc.length === 0) {
      throw badRequest("There is nobody to send it to.");
    }
    const body = args.body === undefined || args.body === null ? "" : args.body;
    if (typeof body !== "string" || body.length > BODY_MAX) {
      throw badRequest("“body” is missing or too long.");
    }
    const files = (optList(args, "attachments", ATTACHMENTS_MAX) ?? []).map(item => {
      if (!item || typeof item !== "object" || typeof item.xfer !== "string" || !XFER.test(item.xfer)) {
        throw badRequest("An attachment is not one.");
      }
      return { xfer: item.xfer, name: fileName(item.name), type: contentType(item.content_type) };
    });
    return {
      account: text(args, "account"),
      identity: optText(args, "identity", 200),
      kind,
      replyTo,
      to,
      cc,
      bcc,
      subject: collapse(optText(args, "subject", SUBJECT_MAX)),
      body,
      files,
    };
  }

  async send(args, ctx) {
    const xfers = Array.isArray(args.attachments)
      ? args.attachments.map(item => item?.xfer).filter(x => typeof x === "string")
      : [];
    try {
      return await this.run(args, ctx);
    } finally {
      // The pieces are for this send and no other, whether it went, failed or was refused.
      xfers.forEach(x => this.stash.drop(x));
    }
  }

  async run(args, ctx) {
    const { messenger, clock, mailbox, stash } = this;
    const request = this.read(args);
    const deadline = ctx.receivedAt + SEND_DEADLINE_MS;
    const left = () => deadline - clock.now();
    const before = promise =>
      withDeadline(clock, promise, left() - START_MARGIN_MS, () =>
        engineError("Thunderbird was too slow to get the mail ready. Nothing was sent.")
      );
    const sender = await before(this.sender(request));
    if (this.stuck.get(sender.account.id)) {
      throw engineError("Thunderbird is still working on an earlier mail for that account. Nothing was sent.");
    }
    const blobs = stash.take(request.files.map(f => f.xfer));
    const files = request.files.map(f => ({ name: f.name, file: new File(blobs.get(f.xfer).chunks, f.name, { type: f.type }) }));
    request.files.forEach(f => stash.drop(f.xfer));
    const original = request.replyTo ? (await before(mailbox.locate(request.account, request.replyTo))).header : null;
    const use = { request, sender, files, original, left, before };
    const quiet = request.kind === "new" && typeof messenger.messages.sendMessage === "function";
    return quiet ? this.direct(use) : this.window(use);
  }

  /** The account, and the identity the mail is from. */
  async sender({ account, identity }) {
    let found = null;
    try {
      found = await this.messenger.accounts.get(account, false);
    } catch {
      // handled below
    }
    if (!found) {
      throw notFound("Thunderbird has no such account. Nothing was sent.");
    }
    const identities = found.identities ?? [];
    const chosen = identity ? identities.find(i => i.id === identity) : identities[0];
    if (!chosen) {
      throw identity
        ? badRequest("That account has no such sender. Nothing was sent.")
        : engineError("Thunderbird has no sender set up for that account. Nothing was sent.");
    }
    return { account: found, identity: chosen };
  }

  /**
   * Thunderbird's call that sends (`start` makes it), held to the deadline; what it answers is the send's result.
   * The call is not made at all when too little time is left to see it through.
   */
  async commit(start, { account, left }) {
    if (left() < START_MARGIN_MS) {
      throw engineError("Thunderbird was too slow to begin sending. Nothing was sent.");
    }
    let call;
    try {
      call = Promise.resolve(start());
    } catch (e) {
      throw notSent(e);
    }
    this.stuck.set(account, (this.stuck.get(account) ?? 0) + 1);
    const settled = new Promise(resolve => {
      const done = () => {
        const n = (this.stuck.get(account) ?? 1) - 1;
        if (n > 0) {
          this.stuck.set(account, n);
        } else {
          this.stuck.delete(account);
        }
        resolve();
      };
      call.then(done, done);
    });
    let result;
    try {
      result = await withDeadline(
        this.clock,
        call,
        left(),
        () =>
          new EngineError(
            UNKNOWN_OUTCOME,
            "Thunderbird did not say whether the mail was sent in time. Look in the Sent folder before sending it again."
          )
      );
    } catch (e) {
      throw e instanceof EngineError ? Object.assign(e, { settled }) : Object.assign(notSent(e), { settled });
    }
    if (result?.mode && result.mode !== "sendNow") {
      throw Object.assign(
        new EngineError(UNKNOWN_OUTCOME, "Thunderbird kept the mail to send later, not now. Look in its Outbox."),
        { settled }
      );
    }
    return {
      message_id: typeof result?.headerMessageId === "string" ? result.headerMessageId.replace(/^<|>$/g, "") : "",
      saved: Array.isArray(result?.messages) && result.messages.length > 0,
    };
  }

  /** A new mail with no window: `messages.sendMessage`. */
  async direct({ request, sender, files, left }) {
    const details = {
      identityId: sender.identity.id,
      subject: request.subject,
      isPlainText: true,
      plainTextBody: request.body || "\n",
    };
    for (const field of ["to", "cc", "bcc"]) {
      if (request[field].length) {
        details[field] = request[field].map(formatRecipient);
      }
    }
    if (files.length) {
      details.attachments = files.map(({ file, name }) => ({ file, name }));
    }
    const sent = await this.commit(() => this.messenger.messages.sendMessage(details, { mode: "sendNow" }), {
      account: sender.account.id,
      left,
    });
    // Thunderbird files the copy in Sent a moment after it answers, and does not say so in the answer.
    sent.saved = sent.saved || (await this.filed(sender.account.id, sent.message_id, left));
    return sent;
  }

  /** Whether the Sent folder has the mail, looked at for a second or two (never at the cost of the deadline). */
  async filed(account, messageId, left) {
    try {
      const folders = (await this.mailbox.foldersOf(account, "sent")).map(folder => folder.id);
      for (let tries = 0; messageId && folders.length && tries < FILED_TRIES && left() > FILED_MARGIN_MS; tries++) {
        const page = await this.messenger.messages.query({ headerMessageId: messageId, folderId: folders });
        if (page.id) {
          abort(this.messenger, page.id);
        }
        if (page.messages.length > 0) {
          return true;
        }
        await pause(this.clock, FILED_EVERY_MS);
      }
    } catch {
      // not known to be saved: said so
    }
    return false;
  }

  /** A reply, a forward, or a new mail when `messages.send` is not granted: a compose window, checked, then sent. */
  async window({ request, sender, files, original, left, before }) {
    const { messenger } = this;
    if (!request.subject) {
      // Thunderbird stops to ask about a mail with no subject, in a dialog nobody sees, and waits for ever.
      throw engineError("Thunderbird would stop to ask about a mail with no subject. Give it a subject. Nothing was sent.");
    }
    let tab = null;
    try {
      const opening = this.open(request, original);
      try {
        tab = await before(opening);
      } catch (e) {
        // Too slow: the window may still come, and it is not left for anyone to find.
        opening.then(late => this.close(late), () => {});
        throw e;
      }
      await before(this.fill(tab, request, sender, files));
      const mismatch = await before(this.differences(tab, request, sender, files));
      if (mismatch) {
        throw engineError(`Thunderbird did not take the mail as it was written (${mismatch}). Nothing was sent.`);
      }
      const sent = await this.commit(() => messenger.compose.sendMessage(tab.id, { mode: "sendNow" }), {
        account: sender.account.id,
        left,
      });
      sent.saved = sent.saved || (await this.filed(sender.account.id, sent.message_id, left));
      return sent;
    } catch (e) {
      if (e && e.settled && tab) {
        // Thunderbird is still in the middle of the send: the window is left to it, and closed when it is done.
        const abandoned = tab;
        tab = null;
        e.settled.then(() => this.close(abandoned));
      }
      throw e;
    } finally {
      if (tab) {
        await this.close(tab);
      }
    }
  }

  async open(request, original) {
    const { compose } = this.messenger;
    if (request.kind === "new") {
      return compose.beginNew();
    }
    if (request.kind === "forward") {
      return compose.beginForward(original.id, "forwardInline");
    }
    return compose.beginReply(original.id, request.kind === "reply_all" ? "replyToAll" : "replyToSender");
  }

  async fill(tab, request, sender, files) {
    const { compose } = this.messenger;
    // The identity first: a change of identity can rewrite the body, and the body comes after.
    await compose.setComposeDetails(tab.id, { identityId: sender.identity.id });
    await compose.setComposeDetails(tab.id, {
      to: request.to.map(formatRecipient),
      cc: request.cc.map(formatRecipient),
      bcc: request.bcc.map(formatRecipient),
      subject: request.subject,
      isPlainText: true,
      plainTextBody: request.body || "\n",
    });
    // A forward brings the original's attachments with it; the mail has the ones that were asked for.
    for (const present of await compose.listAttachments(tab.id)) {
      await compose.removeAttachment(tab.id, present.id);
    }
    for (const { file, name } of files) {
      await compose.addAttachment(tab.id, { file, name });
    }
  }

  /** What differs between the compose window and the request, as a few words; "" when nothing does. */
  async differences(tab, request, sender, files) {
    const { compose } = this.messenger;
    const [now, attached] = await Promise.all([compose.getComposeDetails(tab.id), compose.listAttachments(tab.id)]);
    if (now.identityId !== sender.identity.id) {
      return "the sender";
    }
    for (const field of ["to", "cc", "bcc"]) {
      if (addresses(now[field]).join() !== sorted(request[field].map(r => r.email)).join()) {
        return `the ${field} line`;
      }
    }
    if (collapse(now.subject) !== request.subject) {
      return "the subject";
    }
    const words = plain(request.body || "\n");
    const got = plain(now.plainTextBody);
    if (now.isPlainText !== true || !got.startsWith(words) || got.length - words.length > TAIL_MAX) {
      return "the words";
    }
    const have = attached.map(a => `${a.name}\n${a.size}`).sort().join("\n");
    const want = files.map(f => `${f.name}\n${f.file.size}`).sort().join("\n");
    return have === want ? "" : "the attachments";
  }

  /** Close a compose window without the question about saving it, and without failing if it is gone. */
  async close(tab) {
    const { messenger, clock } = this;
    try {
      await withDeadline(
        clock,
        (async () => {
          try {
            await messenger.compose.setComposeDetails(tab.id, { isModified: false });
          } catch {
            // the window may be gone already, which is what is wanted
          }
          await messenger.windows.remove(tab.windowId);
        })(),
        CLOSE_MS,
        () => new Error("slow")
      );
    } catch {
      // gone, or Thunderbird will not close it: nothing more can be done from here
    }
  }
}

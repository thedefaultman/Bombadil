/*
 * Bombadil Mail: the add-on that lets the mail service use this Thunderbird.
 *
 * It does one thing. It opens a native-messaging port to `bombadil-mail-host`, which is a pipe to the mail
 * service, says hello on it, and answers what the service asks (docs/MAIL.md, "Engine protocol"), telling it
 * what happens in Thunderbird meanwhile. It has no network access and no page of its own; what it can read is
 * what the person's accounts hold, and what it may do is what the service asks the person for. engine.js is
 * where the requests are answered and link.js keeps the port.
 */

import { systemClock } from "./clock.js";
import { Engine } from "./engine.js";
import { Link } from "./link.js";

const log = (what, e) => console.log(`[bombadil-mail] ${what}${e ? `: ${e && e.message ? e.message : e}` : ""}`);

const engine = new Engine({ messenger, clock: systemClock, log });
const link = new Link({ messenger, clock: systemClock, engine, log });

await engine.init();
link.start();
// The first look at the accounts (nothing is told from it) and the listeners; they do not wait for the port.
engine.start().catch(e => log("start", e));

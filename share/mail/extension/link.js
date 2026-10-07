/*
 * The native-messaging port to `bombadil-mail-host`, kept up.
 *
 * Thunderbird starts the host when the port is opened and closes the port when the host goes (or when a frame
 * from it is over 1 MiB). The add-on opens the port again after a pause that doubles with each failure, from
 * half a second up to ten seconds, and starts again from half a second once a connection has lasted a minute.
 * The first frame on every connection is the `hello` event, before any answer or other event can be sent.
 *
 * Mail that arrives while there is no connection (between a lost host and the next one, half a second to ten) is
 * not lost to the service's notices: the newest twenty messages of what `new_mail` would have said are kept, for
 * ten minutes, and told once `hello` has been. Every other event is dropped then, since the service looks at
 * the accounts and the mail again when it connects, and nothing else of it is news.
 *
 * What was in flight on a port that closed is not answered on the next one: the service has numbered those
 * requests on its side of the old connection, and told whoever waited that the engine went away. The calls
 * to Thunderbird that were running go on (they cannot be taken back), and their answers are dropped.
 */

const NAME = "bombadil_mail";
const FIRST_MS = 500;
const LAST_MS = 10_000;
const STABLE_MS = 60_000;
const MISSED_MAX = 20;                 // messages
const MISSED_KEEP_MS = 10 * 60_000;

export class Link {
  constructor({ messenger, clock, engine, log = () => {}, name = NAME }) {
    this.messenger = messenger;
    this.clock = clock;
    this.engine = engine;
    this.log = log;
    this.name = name;
    this.session = null;
    this.delay = FIRST_MS;
    this.timer = null;
    this.stopped = false;
    this.connects = 0;
    this.missed = [];          // [{at, frame}]: `new_mail` events that had nobody to be told to, oldest first
    engine.out = frame => this.emit(frame);
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    this.clock.clearTimeout(this.timer);
    this.timer = null;
    const session = this.session;
    this.session = null;
    if (session) {
      session.live = false;
      try {
        session.port.disconnect();
      } catch {
        // already closed
      }
    }
  }

  /** An event for the service: false when there is no connection to tell it on (new mail is kept for the next). */
  emit(frame) {
    try {
      if (this.session) {
        return this.session.post(frame);
      }
      this.keep(frame);
      return false;
    } catch (e) {
      this.log("event", e);
      return false;
    }
  }

  keep(frame) {
    if (frame?.event !== "new_mail" || !Array.isArray(frame.messages)) {
      return;
    }
    this.missed.push({ at: this.clock.now(), frame });
    let held = this.missed.reduce((sum, item) => sum + item.frame.messages.length, 0);
    while (held > MISSED_MAX && this.missed.length > 1) {
      held -= this.missed.shift().frame.messages.length;
    }
  }

  /** What arrived while nobody was listening and is still news, told on the connection that has come. */
  tell(session) {
    const fresh = this.missed.filter(item => this.clock.now() - item.at < MISSED_KEEP_MS);
    this.missed = [];
    for (const { frame } of fresh) {
      session.post(frame);
    }
  }

  connect() {
    this.timer = null;
    if (this.stopped || this.session) {
      return;
    }
    let port;
    try {
      port = this.messenger.runtime.connectNative(this.name);
    } catch (e) {
      this.log("connectNative", e);
      this.again();
      return;
    }
    this.connects++;
    const session = {
      port,
      live: true,
      since: this.clock.now(),
      ids: new Set(),
      post: frame => {
        if (!session.live) {
          return false;
        }
        port.postMessage(frame);
        return true;
      },
    };
    this.session = session;
    port.onMessage.addListener(frame => {
      if (session.live) {
        this.engine.handle(frame, session);
      }
    });
    port.onDisconnect.addListener(() => this.lost(session, port));
    try {
      session.post(this.engine.hello());
      this.tell(session);
    } catch (e) {
      this.log("hello", e);
    }
  }

  lost(session, port) {
    session.live = false;
    const why = port.error?.message ?? this.messenger.runtime.lastError?.message;
    if (this.session === session) {
      this.session = null;
    }
    if (this.clock.now() - session.since >= STABLE_MS) {
      this.delay = FIRST_MS;
    }
    this.log(`the host went away${why ? ` (${String(why).slice(0, 120)})` : ""}`);
    if (!this.stopped) {
      this.again();
    }
  }

  again() {
    if (this.timer !== null || this.stopped) {
      return;
    }
    const wait = this.delay;
    this.delay = Math.min(LAST_MS, this.delay * 2);
    this.timer = this.clock.setTimeout(() => this.connect(), wait);
  }
}

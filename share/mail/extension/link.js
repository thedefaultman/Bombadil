/*
 * The native-messaging port to `bombadil-mail-host`, kept up.
 *
 * Thunderbird starts the host when the port is opened and closes the port when the host goes (or when a frame
 * from it is over 1 MiB). The add-on opens the port again after a pause that doubles with each failure, from
 * half a second up to ten seconds, and starts again from half a second once a connection has lasted a minute.
 * The first frame on every connection is the `hello` event, before any answer or other event can be sent.
 *
 * What was in flight on a port that closed is not answered on the next one: the service has numbered those
 * requests on its side of the old connection, and told whoever waited that the engine went away. The calls
 * to Thunderbird that were running go on (they cannot be taken back), and their answers are dropped.
 */

const NAME = "bombadil_mail";
const FIRST_MS = 500;
const LAST_MS = 10_000;
const STABLE_MS = 60_000;

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

  /** An event for the service: false when there is no connection to tell it on. */
  emit(frame) {
    try {
      return this.session ? this.session.post(frame) : false;
    } catch (e) {
      this.log("event", e);
      return false;
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

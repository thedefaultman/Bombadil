/*
 * Time, as something that is handed in.
 *
 * Every timer and deadline in the add-on goes through a clock, so the tests can run a ten-minute expiry or a
 * 55-second send deadline without waiting and can make the time stand still while they look at the state.
 */

export const systemClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: timer => clearTimeout(timer),
};

/** `promise`, or what `onTimeout()` returns as an error if it has not settled in `ms`. The promise is not stopped:
 * Thunderbird's calls cannot be cancelled, only given up on. */
export function withDeadline(clock, promise, ms, onTimeout) {
  return new Promise((resolve, reject) => {
    const timer = clock.setTimeout(() => reject(onTimeout()), Math.max(0, ms));
    Promise.resolve(promise).then(
      value => {
        clock.clearTimeout(timer);
        resolve(value);
      },
      error => {
        clock.clearTimeout(timer);
        reject(error);
      }
    );
  });
}

/** Let other work run: one turn of the event loop. */
export function pause(clock, ms = 0) {
  return new Promise(resolve => clock.setTimeout(resolve, ms));
}

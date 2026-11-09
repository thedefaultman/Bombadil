/*
 * What an op says when it cannot do what it was asked.
 *
 * The service understands a handful of codes (docs/MAIL.md, "Engine protocol"): `not_found` and `too_big`
 * pass through to the person, every other code becomes "engine_error" with the sentence, and for a send
 * only, `unknown_outcome` means "cannot tell whether it went". Anything else Thunderbird throws is turned
 * into one plain sentence here, so a raw JavaScript error never reaches the person.
 */

export const NOT_FOUND = "not_found";
export const TOO_BIG = "too_big";
export const BAD_REQUEST = "bad_request";
export const ENGINE_ERROR = "engine_error";
export const UNKNOWN_OUTCOME = "unknown_outcome";

export class EngineError extends Error {
  constructor(code, sentence) {
    super(sentence);
    this.name = "EngineError";
    this.code = code;
  }
}

export const notFound = sentence => new EngineError(NOT_FOUND, sentence);
export const tooBig = sentence => new EngineError(TOO_BIG, sentence);
export const badRequest = sentence => new EngineError(BAD_REQUEST, sentence);
export const engineError = sentence => new EngineError(ENGINE_ERROR, sentence);

/** A line of text safe to put in a sentence: no control characters, one line, short. */
export function oneLine(text, max = 120) {
  const line = String(text ?? "")
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return line.length > max ? line.slice(0, max - 1) + "…" : line;
}

/** The `code` and `error` of a failed answer for anything an op threw. */
export function describe(e) {
  if (e instanceof EngineError) {
    return { code: e.code, error: oneLine(e.message, 300) || "Thunderbird could not do that." };
  }
  const detail = oneLine(e && e.message, 100).replace(/[.\s]+$/, "");
  return {
    code: ENGINE_ERROR,
    error: detail ? `Thunderbird could not do that: ${detail}.` : "Thunderbird could not do that.",
  };
}

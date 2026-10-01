/*
 * Reading a request: the arguments an op needs, checked once, in one place.
 *
 * The service sends well-formed requests, but what arrives here is a frame from another program, so each
 * value is checked for its type and size, and what is wrong is a `bad_request` with a sentence, not a
 * TypeError somewhere inside Thunderbird's calls.
 */

import { badRequest } from "./errors.js";

export function text(args, name, max = 1000) {
  const value = args[name];
  if (typeof value !== "string" || !value || value.length > max) {
    throw badRequest(`“${name}” is missing or not text.`);
  }
  return value;
}

export function optText(args, name, max = 1000) {
  if (args[name] === undefined || args[name] === null) {
    return undefined;
  }
  const value = args[name];
  if (typeof value !== "string" || value.length > max) {
    throw badRequest(`“${name}” is not text.`);
  }
  return value;
}

export function optBool(args, name) {
  const value = args[name];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "boolean") {
    throw badRequest(`“${name}” is yes or no.`);
  }
  return value;
}

export function optNumber(args, name) {
  const value = args[name];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw badRequest(`“${name}” is a number.`);
  }
  return value;
}

export function limit(args, fallback, max) {
  const value = optNumber(args, "limit");
  return value === undefined ? fallback : Math.min(max, Math.max(1, Math.floor(value)));
}

export function optList(args, name, max = 200) {
  const value = args[name];
  if (value === undefined || value === null) {
    return undefined;
  }
  if (!Array.isArray(value) || value.length > max) {
    throw badRequest(`“${name}” is a list.`);
  }
  return value;
}

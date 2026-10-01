/*
 * Messages in order, newest first, without holding them.
 *
 * `messages.list` gives a folder's messages a page at a time (a first page, then `continueList` with the id
 * that came with it). With `sortType: "date"` Thunderbird sorts the folder itself, so a page is always the
 * next hundred newest, and all this add-on keeps of a folder is the page it is looking at. An early stop
 * calls `abortList`, so Thunderbird does not go on preparing pages nobody will read.
 *
 * Several folders (an inbox's own, an archive with a folder for each year) are merged the same way, by
 * date, taking one message at a time from whichever is newest.
 */

export async function* folderStream(messenger, folderId) {
  let page = await messenger.messages.list(folderId, { sortType: "date", sortOrder: "descending" });
  try {
    while (true) {
      for (const header of page.messages) {
        yield header;
      }
      if (!page.id) {
        return;
      }
      page = await messenger.messages.continueList(page.id);
    }
  } finally {
    if (page && page.id) {
      abort(messenger, page.id);
    }
  }
}

/** Give up on a list Thunderbird is still preparing. Never throws: the list may be done already. */
export function abort(messenger, listId) {
  try {
    Promise.resolve(messenger.messages.abortList(listId)).catch(() => {});
  } catch {
    // a list that is gone is what was wanted
  }
}

const time = header => header.date.getTime();

/** The streams as one, newest first; a tie goes to the earlier stream, so the order is the same every time. */
export async function* newestFirst(streams) {
  const heads = [];
  const iterators = streams.map(stream => stream[Symbol.asyncIterator]());
  try {
    const firsts = await Promise.all(iterators.map(it => it.next()));
    firsts.forEach((first, index) => {
      if (!first.done) {
        heads.push({ it: iterators[index], value: first.value, index });
      }
    });
    while (heads.length) {
      let best = 0;
      for (let i = 1; i < heads.length; i++) {
        const a = heads[i];
        const b = heads[best];
        if (time(a.value) > time(b.value) || (time(a.value) === time(b.value) && a.index < b.index)) {
          best = i;
        }
      }
      const head = heads[best];
      yield head.value;
      const next = await head.it.next();
      if (next.done) {
        heads.splice(best, 1);
      } else {
        head.value = next.value;
      }
    }
  } finally {
    await Promise.allSettled(iterators.map(it => it.return?.()));
  }
}

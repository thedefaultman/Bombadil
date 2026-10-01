/*
 * Messages in order, newest first, without holding them.
 *
 * `messages.list` gives a folder's messages a page at a time (a first page, then `continueList` with the id
 * that came with it). With `sortType: "date"` Thunderbird sorts the folder itself, so a page is always the
 * next hundred newest, and all this add-on keeps of a folder is the page it is looking at.
 *
 * A list Thunderbird has made is kept by Thunderbird until its last page has been read: `abortList` only says
 * it is not wanted any more (it stops Thunderbird preparing pages), it does not forget it, and the pages already
 * prepared stay in Thunderbird's memory for as long as it runs, about 0.7 MiB for each list that was begun
 * and not read to the end, whether it came from `list`, `query` or an event. `abort` therefore reads
 * what is left, which is quick since nothing more is being prepared, and that is what lets the list go.
 *
 * Several folders (an inbox's own, an archive with a folder for each year) are merged the same way, by
 * date, taking one message at a time from whichever is newest.
 */

const DRAIN_MAX = 64;                  // pages: a list that was begun a moment ago has one or two

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

/**
 * Let go of a list Thunderbird is still preparing: stop it, then read what it had made so that Thunderbird
 * can forget the list. The promise it returns never rejects (the list may be gone already); callers do not wait
 * for it, since a Thunderbird that has stopped answering must not hold up an op that is done.
 */
export function abort(messenger, listId) {
  const release = async () => {
    await messenger.messages.abortList(listId);
    let id = listId;
    for (let pages = 0; id && pages < DRAIN_MAX; pages++) {
      id = (await messenger.messages.continueList(id))?.id;
    }
  };
  try {
    return Promise.resolve(release()).catch(() => {});
  } catch {
    return Promise.resolve();
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

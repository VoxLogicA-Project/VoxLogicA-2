// Who gets a WebGL context, and who gets a picture of one.
//
// A browser gives about sixteen live WebGL contexts. A board can hold more cards
// than that, and the failure when it does is the worst kind: the browser drops
// the *oldest* context silently, its canvas goes blank or black, and nothing
// anywhere says why. Nobody diagnoses that from the symptom.
//
// So contexts are not owned by cards. There is a small pool, lent by least
// recent use to the cards that are actually being looked at; a card that loses
// its lease keeps the last frame it drew, as a bitmap, and asks for the lease
// back when it is approached. The limit is respected by construction rather than
// by hoping fewer than sixteen viewers are open.
//
// Eight, not sixteen: the browser's limit counts every context on the page, and
// something else -- a chart, a thumbnail, a devtools panel -- is entitled to
// some. Leaving half the budget alone is what makes this a rule rather than a
// race.
//
// See doc/dev/ui-cards.md section 7.

const CAPACITY = 8;

/** A pool of leases, held by whoever is being looked at. */
class Contexts {
  #capacity;
  /** Holders in least-recently-touched order. The array is the LRU: it is
   * never longer than the capacity, and a board with eight viewers is a
   * linear scan of eight, which is cheaper than any structure that improves
   * on it. */
  #live = [];

  constructor(capacity = CAPACITY) {
    this.#capacity = capacity;
  }

  get capacity() {
    return this.#capacity;
  }

  get size() {
    return this.#live.length;
  }

  holds(holder) {
    return this.#live.includes(holder);
  }

  /**
   * Ask for a context, evicting the least recently wanted holder if the pool
   * is full.
   *
   * `holder` is anything with a `release()`: the component that had the lease
   * is *told*, rather than being polled or watched, because it is the only
   * thing that knows how to keep its last frame.
   *
   * @returns {boolean} whether the caller now holds a lease
   */
  acquire(holder) {
    if (this.holds(holder)) {
      this.touch(holder);
      return true;
    }
    while (this.#live.length >= this.#capacity) {
      const evicted = this.#live.shift();
      // Never let a holder's own teardown take the pool down with it: a viewer
      // that throws while giving up its canvas must not stop the one asking.
      try {
        evicted?.release();
      } catch {
        /* it is going away regardless */
      }
    }
    this.#live.push(holder);
    return true;
  }

  /** Mark a holder as the most recently wanted, without changing the count. */
  touch(holder) {
    const at = this.#live.indexOf(holder);
    if (at >= 0) {
      this.#live.splice(at, 1);
      this.#live.push(holder);
    }
  }

  /** Give a lease back. Idempotent: a component unmounting after it was
   * already evicted is the ordinary case, not an error. */
  drop(holder) {
    const at = this.#live.indexOf(holder);
    if (at >= 0) this.#live.splice(at, 1);
  }
}

/** The one pool for the page. A second one would be two budgets for one
 * browser, which is exactly the thing this exists to prevent. */
export const contexts = new Contexts();

export { Contexts };

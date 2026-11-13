import {randomBytes, timingSafeEqual} from 'node:crypto';

const minute = 60_000;
const random = () => randomBytes(32).toString('base64url');
export const cookieName = '__Host-convergence-review';
export const tokenPattern = /^[A-Za-z0-9_-]{43}$/;
export const absoluteLifetime = 30 * minute;

export function failure(status) {
  return Object.assign(new Error('Reviewer request failed'), {status});
}

// Fixed cardinality plus the global bucket prevent arbitrary browser addresses
// from creating unbounded entries. Both successful and failed attempts count.
export class Admission {
  constructor({total, perAddress}) {
    this.total = total;
    this.perAddress = perAddress;
    this.started = 0;
    this.count = 0;
    this.addresses = new Map();
  }

  take(address) {
    const now = Date.now();
    if (now - this.started >= minute) {
      this.started = now;
      this.count = 0;
      this.addresses.clear();
    }
    if (this.count >= this.total) throw failure(429);
    this.count += 1;
    const count = this.addresses.get(address) || 0;
    if (count >= this.perAddress) throw failure(429);
    this.addresses.set(address, count + 1);
  }
}

export class ReviewerSessions {
  constructor(clock = Date.now) {
    this.clock = clock;
    this.entries = new Map();
  }

  get(id) {
    const entry = this.entries.get(id);
    if (entry && this.clock() >= Math.min(entry.idleUntil, entry.expires)) {
      this.entries.delete(id);
      return undefined;
    }
    return entry;
  }

  bootstrap() {
    for (const id of this.entries.keys()) this.get(id);
    if (this.entries.size >= 512) throw failure(429);
    const now = this.clock();
    const entry = {id: random(), csrf: random(), expires: now + 5 * minute, idleUntil: now + 5 * minute};
    this.entries.set(entry.id, entry);
    return entry;
  }

  authenticated(previous, grant, user, snapshot) {
    this.requireCurrent(previous);
    const expires = Math.min(this.clock() + absoluteLifetime, Date.parse(grant.expiresAt), Date.parse(snapshot.expiresAt));
    const idleUntil = Math.min(expires, Date.parse(grant.idleExpiresAt), Date.parse(snapshot.idleExpiresAt),
      this.clock() + 10 * minute);
    if (!Number.isFinite(idleUntil) || idleUntil <= this.clock()) throw failure(401);
    const entry = {id: random(), csrf: random(), grant: grant.token, user, expires, idleUntil};
    this.entries.delete(previous.id);
    this.entries.set(entry.id, entry);
    return entry;
  }

  requireCurrent(entry) {
    if (!entry || this.get(entry.id) !== entry) throw failure(401);
  }

  checkCsrf(entry, value) {
    this.requireCurrent(entry);
    if (typeof value !== 'string' || !tokenPattern.test(value) ||
        !timingSafeEqual(Buffer.from(entry.csrf), Buffer.from(value))) throw failure(403);
  }

  updateDeadline(entry, snapshot, wrote) {
    this.requireCurrent(entry);
    entry.expires = Math.min(entry.expires, Date.parse(snapshot.expiresAt));
    // Polls can only shorten the locally known deadline. After a successful
    // write the service reports the renewed idle expiry from the database.
    entry.idleUntil = Math.min(entry.expires, Date.parse(snapshot.idleExpiresAt),
      wrote ? this.clock() + 10 * minute : entry.idleUntil);
    if (!Number.isFinite(entry.idleUntil) || entry.idleUntil <= this.clock()) {
      this.remove(entry);
      throw failure(401);
    }
  }

  remove(entry) {
    this.entries.delete(entry.id);
  }

  close() {
    const grants = [];
    for (const session of this.entries.values()) {
      if (session.grant) grants.push(session.grant);
    }
    this.entries.clear();
    return grants;
  }
}

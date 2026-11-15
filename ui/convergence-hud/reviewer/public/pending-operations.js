const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;

export async function tokenDigest(browser, fields) {
  const bytes = new TextEncoder().encode(JSON.stringify(fields));
  const digest = await browser.crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
}

// The tab keeps one opaque receipt pointer per principal, site and case. Notes,
// names, grant tokens and case IDs are never serialized into browser storage.
export function receiptPointers(browser, namespace = 'review', fields = ['kind', 'reason']) {
  const inputs = new Map();
  return {
    async scope(user, site, id) {
      return namespace + ':' + await tokenDigest(browser, [user, site, id]);
    },
    get(key) {
      const value = browser.sessionStorage.getItem(key);
      if (!value) return null;
      const [id, digest] = value.split('.');
      if (!UUID.test(id) || !/^[0-9a-f]{64}$/.test(digest)) throw Error('Invalid receipt pointer');
      return {key, id, digest, input: inputs.get(key)};
    },
    async prepare(key, values, previous, stillCurrent) {
      const id = previous?.id || browser.crypto.randomUUID();
      const digest = await tokenDigest(browser, [id, ...fields.map(field => values[field])]);
      if (!stillCurrent() || (previous && digest !== previous.digest)) return null;
      const input = {operationId: id, ...Object.fromEntries(fields.map(field => [field, values[field]]))};
      browser.sessionStorage.setItem(key, id + '.' + digest);
      inputs.set(key, input);
      return {key, id, digest, input};
    },
    clearPrivate() { inputs.clear(); },
    forget(key) { inputs.delete(key); browser.sessionStorage.removeItem(key); },
  };
}


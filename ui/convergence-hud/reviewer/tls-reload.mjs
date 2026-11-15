// Reload TLS material in place when its files change (specs/1016-tls-renewal-reload).
// The API/relay image carries the same contract in ui/convergence-api/src/reviewer/tls-reload.js.
import {createPrivateKey, X509Certificate} from 'node:crypto';
import fs from 'node:fs/promises';
import tls from 'node:tls';

const pemBlock = /-----BEGIN CERTIFICATE-----[\s\S]+?-----END CERTIFICATE-----/g;

export function certificates(pem) {
  const blocks = String(pem).match(pemBlock) || [];
  if (!blocks.length) throw new Error('no certificate');
  return blocks.map(block => new X509Certificate(block));
}

// A replacement identity must parse, match its key and be valid now. The old one keeps
// serving otherwise, which also rejects a half-written cert/key pair.
export function identity({cert, key, ca}, now = Date.now()) {
  const [leaf] = certificates(cert);
  if (!leaf.checkPrivateKey(createPrivateKey(key))) throw new Error('key does not match certificate');
  if (Date.parse(leaf.validFrom) > now || Date.parse(leaf.validTo) <= now) throw new Error('certificate is not currently valid');
  if (ca !== undefined) certificates(ca);
  tls.createSecureContext({cert, key, ca});
  return {cert, key, ca, notAfter: Date.parse(leaf.validTo)};
}

export function trust({ca}) {
  certificates(ca);
  return {ca};
}

// Polls stat (not lstat): kubelet publishes a Secret by swapping a symlink, which changes
// the resolved inode. On a change it reads every file, then build() validates and apply()
// swaps. Failures keep the current material and are logged once per distinct reason. The
// first poll applies what is on disk, so a change racing the caller's startup read is not
// mistaken for the baseline.
export function watchTls({files, build, apply, intervalMs = 10000, log = console.error}) {
  let seen = null;
  let lastError = null;
  let running = Promise.resolve();
  const state = {reloads: 0, error: null};
  const signature = async () => (await Promise.all(Object.values(files).map(async file => {
    const s = await fs.stat(file);
    return [s.dev, s.ino, s.size, s.mtimeMs].join(':');
  }))).join('|');

  async function poll() {
    let sig;
    try { sig = await signature(); } catch { return note('TLS files are unreadable'); }
    if (sig === seen) return;
    try {
      const material = Object.fromEntries(await Promise.all(Object.entries(files)
        .map(async ([name, file]) => [name, await fs.readFile(file)])));
      apply(build(material));
      if (seen !== null) { state.reloads += 1; log('TLS material reloaded'); }
      seen = sig;
      state.error = lastError = null;
    } catch (error) {
      note('TLS reload rejected: ' + (error?.message || 'invalid material'));
    }
  }
  function note(message) {
    state.error = message;
    if (message !== lastError) log(message);
    lastError = message;
  }
  const check = () => (running = running.then(poll));
  check();
  const timer = setInterval(check, intervalMs);
  timer.unref();
  return {check, state, close: () => clearInterval(timer)};
}

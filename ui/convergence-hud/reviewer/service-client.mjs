import https from 'node:https';

const uuid = /^[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12}$/i;
function casePath(site, id) {
  if (typeof site !== 'string' || !/^[a-z\d_.-]{1,128}$/i.test(site) || site === '.' || site === '..' ||
      typeof id !== 'string' || !uuid.test(id)) {
    throw Object.assign(new Error('Invalid case'), {status: 400});
  }
  return '/v1/sites/' + site + '/cases/' + id;
}

export function createReviewerClient({origin, tls, deadlineMs = 10000}) {
  const base = new URL(origin);
  if (base.protocol !== 'https:' || base.origin !== origin || !tls?.key || !tls.cert || !tls.ca) {
    throw new Error('Expected an exact HTTPS service origin and installation TLS credentials');
  }
  const agent = new https.Agent({keepAlive: true, maxSockets: 16, maxTotalSockets: 16, maxFreeSockets: 4,
    ca: tls.ca, cert: tls.cert, key: tls.key, rejectUnauthorized: true, minVersion: 'TLSv1.2'});
  async function call(method, path, token, input) {
    if (token !== null && (typeof token !== 'string' || !/^[a-zA-Z0-9_-]{43}$/.test(token))) {
      throw Object.assign(new Error('Invalid session'), {status: 401});
    }
    const data = input === undefined ? undefined : Buffer.from(JSON.stringify(input));
    const headers = {};
    if (token !== null) headers.authorization = 'Bearer ' + token;
    if (data) {
      headers['content-type'] = 'application/json';
      headers['content-length'] = data.length;
    }
    return new Promise((resolve, reject) => {
      const unavailable = () => reject(Object.assign(new Error('Reviewer service unavailable'), {status: 503}));
      const req = https.request(base, {agent, method, path, headers, signal: AbortSignal.timeout(deadlineMs)}, res => {
        if (res.statusCode === 204) { res.resume(); return resolve(); }
        if (![200, 201].includes(res.statusCode)) {
          const status = [400,401,403,404,409,413,415,429].includes(res.statusCode) ? res.statusCode : 503;
          res.destroy();
          return reject(Object.assign(new Error('Reviewer service rejected request'), {status}));
        }
        let size = 0;
        const chunks = [];
        res.on('error', unavailable);
        res.on('aborted', unavailable);
        res.on('data', part => {
          size += part.length;
          if (size > 256 * 1024) { res.destroy(); unavailable(); }
          else chunks.push(part);
        });
        res.on('end', () => {
          try {
            if (!res.complete) return unavailable();
            resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
          } catch { unavailable(); }
        });
      });
      req.on('error', unavailable);
      req.end(data);
    });
  }
  return {
    issue: credentials => call('POST', '/v1/grants', null, credentials),
    current: token => call('GET', '/v1/grants/current', token),
    revoke: token => call('DELETE', '/v1/grants/current', token),
    readCase: (token, site, id) => call('GET', casePath(site, id), token),
    recordDisposition: (token, site, id, body) => call('POST', casePath(site, id) + '/disposition', token, body),
    readReceipt(token, site, id, operationId) {
      if (typeof operationId !== 'string' || !uuid.test(operationId)) throw Object.assign(new Error('Invalid receipt'), {status: 400});
      return call('GET', casePath(site, id) + '/disposition/' + operationId, token);
    },
    close: () => agent.destroy(),
  };
}

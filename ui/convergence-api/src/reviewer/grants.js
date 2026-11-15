'use strict';

const {createHash, randomBytes} = require('node:crypto');
const {createPasswordVerifier} = require('../lib/password-verification');

const digest = value => createHash('sha256').update(value).digest();
const roles = ['viewer', 'operator', 'admin'];
const idleInterval = '10 minutes';
const absoluteInterval = '30 minutes';

function denied(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

const unauthenticated = () => denied(401, 'Invalid or expired credentials');

async function findReviewerAccount(queryable, username) {
  const {rows} = await queryable.query(
    `SELECT id, username, name, password_hash AS "passwordHash", role, sites, revision,
            credential_revision AS "credentialRevision"
       FROM reviewer_accounts WHERE enabled AND username = $1`, [username]);
  return rows[0] || null;
}

async function transaction(pool, callback) {
  const client = await pool.connect();
  let discard;
  // PostgreSQL can terminate an idle transaction between queries. A pg client
  // checked out from the pool has no idle error listener of its own.
  const connectionLost = Promise.withResolvers();
  const onError = error => {
    discard = error;
    connectionLost.reject(error);
  };
  client.on('error', onError);
  try {
    const run = async () => {
      // SET LOCAL is transaction-scoped even when this client is reused by a
      // standalone web route. Bound waits on locks, queries and idle holders.
      await client.query(`BEGIN;
        SET LOCAL lock_timeout = '2s';
        SET LOCAL statement_timeout = '8s';
        SET LOCAL idle_in_transaction_session_timeout = '5s'`);
      return callback(client);
    };
    const result = await Promise.race([run(), connectionLost.promise]);
    await client.query('COMMIT');
    return result;
  } catch (error) {
    try { await client.query('ROLLBACK'); }
    catch (rollbackError) { discard = rollbackError; }
    if (discard || ['55P03', '57014', '25P03'].includes(error.code)) {
      throw denied(503, 'Reviewer service unavailable');
    }
    throw error;
  } finally {
    client.removeListener('error', onError);
    client.release(discard);
  }
}

async function admitLogin(pool, installation, username) {
  const buckets = [
    ['global', '', 120],
    ['installation', installation, 60],
    ['username', digest(username).toString('hex'), 10],
  ];
  const admitted = await transaction(pool, async client => {
    // All replicas acquire these in the same order. Commit earlier increments
    // even if a narrower bucket is full, before doing any expensive bcrypt work.
    for (const [kind, key, limit] of buckets) {
      const {rowCount} = await client.query(
        `INSERT INTO reviewer_login_buckets AS b (kind, key, started_at, attempts)
         VALUES ($1, $2, statement_timestamp(), 1)
         ON CONFLICT (kind, key) DO UPDATE SET
           (started_at, attempts) = (
             CASE WHEN b.started_at <= EXCLUDED.started_at - interval '1 minute'
                  THEN EXCLUDED.started_at ELSE b.started_at END,
             CASE WHEN b.started_at <= EXCLUDED.started_at - interval '1 minute'
                  THEN 1 ELSE b.attempts + 1 END
           )
         WHERE b.started_at <= EXCLUDED.started_at - interval '1 minute' OR b.attempts < $3`,
        [kind, key, limit]);
      if (!rowCount) return false;
    }
    // Bound cleanup per admission and keep each indexed scan independent of
    // the lifetime of the installation. Expiry checks never depend on cleanup.
    await client.query(`DELETE FROM reviewer_login_buckets WHERE (kind, key) IN (
      SELECT kind, key FROM reviewer_login_buckets WHERE started_at <= statement_timestamp() - interval '1 minute'
      ORDER BY started_at LIMIT 16 FOR UPDATE SKIP LOCKED)`);
    await client.query(`DELETE FROM reviewer_grants WHERE token_digest IN (
      SELECT token_digest FROM reviewer_grants WHERE LEAST(expires_at, idle_expires_at) <= statement_timestamp()
      ORDER BY LEAST(expires_at, idle_expires_at) LIMIT 16 FOR UPDATE SKIP LOCKED)`);
    return true;
  });
  if (!admitted) throw denied(429, 'Too many sign-in attempts');
}

function checkInstallation(installation) {
  if (typeof installation !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(installation)) {
    throw unauthenticated();
  }
}

function bearerDigest(token) {
  if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw unauthenticated();
  return digest(token);
}

function createGrantAuthority(pool) {
  async function issue(installation, credentials) {
    checkInstallation(installation);
    const {username, password} = credentials || {};
    if (typeof username !== 'string' || !username || username.length > 128 || username.includes('\0') ||
        typeof password !== 'string' || !password || Buffer.byteLength(password) > 4096) {
      throw denied(400, 'Invalid credentials format');
    }
    await admitLogin(pool, installation, username);
    // Do bcrypt outside the connection pool and the account lock. Verify that
    // the observed account revision still exists when inserting the grant.
    const account = await transaction(pool, client => findReviewerAccount(client, username));
    const verify = createPasswordVerifier(() => account);
    if (!await verify(username, password)) throw unauthenticated();
    const token = randomBytes(32).toString('base64url');
    const row = await transaction(pool, async client => {
      const current = await client.query(
        'SELECT id FROM reviewer_accounts WHERE id = $1 AND revision = $2 AND enabled FOR SHARE',
        [account.id, account.revision]);
      if (!current.rowCount) throw unauthenticated();
      const {rows} = await client.query(
        `INSERT INTO reviewer_grants (token_digest, account_id, installation, issued_role, issued_sites,
          issued_revision, credential_revision, expires_at, idle_expires_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, statement_timestamp() + $8::interval,
            statement_timestamp() + $9::interval) RETURNING expires_at, idle_expires_at`,
        [digest(token), account.id, installation, account.role, account.sites,
          account.revision, account.credentialRevision, absoluteInterval, idleInterval]);
      return rows[0];
    });
    return {token, expiresAt: row.expires_at, idleExpiresAt: row.idle_expires_at, id: account.id, name: account.name, role: account.role, sites: account.sites};
  }

  async function revoke(installation, token) {
    checkInstallation(installation);
    const tokenDigest = bearerDigest(token);
    await transaction(pool, client => client.query(
      'DELETE FROM reviewer_grants WHERE installation = $1 AND token_digest = $2', [installation, tokenDigest]));
  }

  async function withGrant(installation, token, {site, write}, operation) {
    checkInstallation(installation);
    const tokenDigest = bearerDigest(token);
    if (typeof write !== 'boolean' || (site === null && write) ||
        (site !== null && (typeof site !== 'string' || !site || site.length > 256 || site.includes('\0')))) {
      throw denied(400, 'Invalid grant scope');
    }
    return transaction(pool, async client => {
      // Lock both records through the operation and commit. Concurrent revoke
      // or downgrade waits or becomes visible before this operation is admitted.
      // Follow the same account-then-grant lock order as account deletion
      // (its foreign key cascades) and grant issuance. A concurrent logout
      // can remove the grant before the second query, which must then deny it.
      const {rows: [account]} = await client.query(
        `SELECT id, name, role, sites, credential_revision FROM reviewer_accounts
         WHERE enabled AND id = (SELECT account_id FROM reviewer_grants
           WHERE token_digest = $1 AND installation = $2) FOR SHARE`, [tokenDigest, installation]);
      if (!account) throw unauthenticated();
      const {rows: [grant]} = await client.query(
        `SELECT issued_role, issued_sites, expires_at, idle_expires_at FROM reviewer_grants WHERE token_digest = $1
         AND account_id = $2 AND installation = $3 AND credential_revision = $4 FOR UPDATE`,
        [tokenDigest, account.id, installation, account.credential_revision]);
      if (!grant) throw unauthenticated();

      // Use the DB wall clock after obtaining the locks, including on the way
      // out. A slow operation cannot publish a result or commit after expiry.
      const expiry = 'LEAST(expires_at, idle_expires_at) > clock_timestamp()';
      const validGrant = `SELECT 1 FROM reviewer_grants WHERE token_digest = $1 AND ${expiry}`;
      if (!(await client.query(validGrant, [tokenDigest])).rowCount) throw unauthenticated();
      const role = roles[Math.min(roles.indexOf(account.role), roles.indexOf(grant.issued_role))];
      const sites = account.sites.filter(item => grant.issued_sites.includes(item));
      if ((site !== null && !sites.includes(site)) || (write && !['admin', 'operator'].includes(role))) {
        throw denied(403, 'Operation not authorized');
      }
      const result = await operation(client, {id: account.id, name: account.name, role, sites, installation,
        expiresAt: grant.expires_at, idleExpiresAt: grant.idle_expires_at});
      const renewed = write
        ? await client.query(`UPDATE reviewer_grants SET idle_expires_at = LEAST(expires_at,
              clock_timestamp() + $2::interval) WHERE token_digest = $1 AND ${expiry}`, [tokenDigest, idleInterval])
        : await client.query(validGrant, [tokenDigest]);
      if (!renewed.rowCount) throw unauthenticated();
      return result;
    });
  }

  return {issue, revoke, withGrant};
}

module.exports = {createGrantAuthority, findReviewerAccount};

'use strict';

require('dotenv').config();
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const {pool} = require('./src/db');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {createIdTokenVerifier} = require('./src/reviewer/oidc');
const {identity, watchTls} = require('./src/reviewer/tls-reload');
const {createReviewerService} = require('./src/reviewer/transport');
const {createTelemetryReader} = require('./src/reviewer/telemetry');
const {backfillQueue} = require('./src/reviewer/queue');

async function migrate(pool) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // Serialize installations across replicas without taking a lock during normal startup.
    await client.query("SELECT pg_advisory_xact_lock(hashtext('convergence reviewer schema v1'))");
    await client.query('SELECT security_disposition FROM events LIMIT 0');
    for (const file of ['grants.sql', 'cases.sql', 'investigations.sql', 'jev.sql', 'queue.sql']) {
      await client.query(fs.readFileSync(path.join(__dirname, 'src/reviewer', file), 'utf8'));
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
}

function enrollmentRecords(text) {
  try { return JSON.parse(text); } catch { throw new Error('enrollment file is not JSON'); }
}

async function main() {
  if (!pool) throw new Error('PostgreSQL must be enabled');
  if (process.argv[2] === 'migrate' && process.argv.length === 3) {
    try { await migrate(pool); } finally { await pool.end(); }
    return;
  }
  if (process.argv[2] === 'backfill-queue' && process.argv.length === 3) {
    try {
      const copied = await backfillQueue(pool, step => console.log(JSON.stringify(step)));
      console.log(JSON.stringify({copied}));
    } finally { await pool.end(); }
    return;
  }
  if (process.argv.length !== 2) throw new Error('Expected no arguments, migrate or backfill-queue');
  const host = process.env.REVIEWER_SERVICE_HOST || '127.0.0.1';
  const port = Number(process.env.REVIEWER_SERVICE_PORT || 3443);
  if (!net.isIP(host) || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid listener address');
  const input = key => fs.readFileSync(process.env[key]);
  const tls = {ca: input('REVIEWER_CLIENT_CA'), key: input('REVIEWER_TLS_KEY'), cert: input('REVIEWER_TLS_CERT')};
  const enrollments = JSON.parse(input('REVIEWER_INSTALLATIONS_FILE'));
  // Refuse to start on a schema from before this release, so a rollout keeps the
  // previous replica serving until the migration has run.
  await pool.query('SELECT 1 FROM reviewer_grants LIMIT 0');
  await pool.query('SELECT 1 FROM reviewer_identity_links LIMIT 0');
  await pool.query('SELECT 1 FROM reviewer_oidc_exchanges LIMIT 0');
  await pool.query('SELECT 1 FROM reviewer_case_queue LIMIT 0');
  // Both settings are required together. Without them only password sign-in exists.
  const {REVIEWER_OIDC_ISSUER: issuer, REVIEWER_OIDC_CLIENT_ID: audience} = process.env;
  if (Boolean(issuer) !== Boolean(audience)) throw new Error('Incomplete identity provider settings');
  const oidc = issuer ? createIdTokenVerifier({issuer, audience,
    requiredGroup: process.env.REVIEWER_OIDC_REQUIRED_GROUP || ''}) : undefined;
  const passwordSignIn = process.env.REVIEWER_PASSWORD_SIGN_IN !== '0';
  if (!oidc && !passwordSignIn) throw new Error('No sign-in method is enabled');
  const server = createReviewerService({tls, enrollments, authority: createGrantAuthority(pool, {oidc, passwordSignIn}),
    investigationEnabled: process.env.REVIEWER_INVESTIGATION_ENABLED === '1',
    jevEnabled: process.env.REVIEWER_JEV_ENABLED === '1',
    telemetry: process.env.REVIEWER_TELEMETRY_ENABLED === '1' ? createTelemetryReader() : undefined});
  server.listen(port, host, () => console.log('Convergence reviewer service listening with mutual TLS'));
  const watcher = watchTls({files: {ca: process.env.REVIEWER_CLIENT_CA, cert: process.env.REVIEWER_TLS_CERT,
    key: process.env.REVIEWER_TLS_KEY, enrollments: process.env.REVIEWER_INSTALLATIONS_FILE},
  build: ({enrollments, ...material}) => ({...identity(material), enrollments: enrollmentRecords(enrollments)}),
  apply: next => server.reload(next)});
  const close = () => { watcher.close(); server.close(() => pool.end()); };
  process.once('SIGTERM', close);
  process.once('SIGINT', close);
}

if (require.main === module) main().catch(() => {
  console.error('Reviewer service failed to start. Check the database, migration, enrollment and TLS configuration.');
  process.exitCode = 1;
  if (pool && !pool.ended) pool.end().catch(() => {});
});

module.exports = {migrate};

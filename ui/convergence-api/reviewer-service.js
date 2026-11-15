'use strict';

require('dotenv').config();
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const {pool} = require('./src/db');
const {createGrantAuthority} = require('./src/reviewer/grants');
const {createReviewerService} = require('./src/reviewer/transport');

async function migrate(pool) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // Serialize installations across replicas without taking a lock during normal startup.
    await client.query("SELECT pg_advisory_xact_lock(hashtext('convergence reviewer schema v1'))");
    await client.query('SELECT security_disposition FROM events LIMIT 0');
    for (const file of ['grants.sql', 'cases.sql', 'investigations.sql']) {
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

async function main() {
  if (!pool) throw new Error('PostgreSQL must be enabled');
  if (process.argv[2] === 'migrate' && process.argv.length === 3) {
    try { await migrate(pool); } finally { await pool.end(); }
    return;
  }
  if (process.argv.length !== 2) throw new Error('Expected no arguments, or migrate');
  const host = process.env.REVIEWER_SERVICE_HOST || '127.0.0.1';
  const port = Number(process.env.REVIEWER_SERVICE_PORT || 3443);
  if (!net.isIP(host) || !Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid listener address');
  const input = key => fs.readFileSync(process.env[key]);
  const tls = {ca: input('REVIEWER_CLIENT_CA'), key: input('REVIEWER_TLS_KEY'), cert: input('REVIEWER_TLS_CERT')};
  const enrollments = JSON.parse(input('REVIEWER_INSTALLATIONS_FILE'));
  await pool.query('SELECT 1 FROM reviewer_grants LIMIT 0');
  const server = createReviewerService({tls, enrollments, authority: createGrantAuthority(pool),
    investigationEnabled: process.env.REVIEWER_INVESTIGATION_ENABLED === '1'});
  server.listen(port, host, () => console.log('Convergence reviewer service listening with mutual TLS'));
  const close = () => server.close(() => pool.end());
  process.once('SIGTERM', close);
  process.once('SIGINT', close);
}

if (require.main === module) main().catch(() => {
  console.error('Reviewer service failed to start. Check the database, migration, enrollment and TLS configuration.');
  process.exitCode = 1;
  if (pool && !pool.ended) pool.end().catch(() => {});
});

module.exports = {migrate};

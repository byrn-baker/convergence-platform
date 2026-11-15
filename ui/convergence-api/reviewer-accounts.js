'use strict';

require('dotenv').config();
const bcrypt = require('bcrypt');
const {randomUUID} = require('node:crypto');
const {pool} = require('./src/db');

const usage = 'Usage: node reviewer-accounts.js create|access USERNAME viewer|operator|admin SITE...; or password|disable USERNAME. Supply a new password on stdin for create or password.';

async function passwordHash(input) {
  if (input.isTTY) throw new Error('Provide the password through stdin without terminal echo');
  const chunks = [];
  let size = 0;
  for await (const chunk of input) {
    size += Buffer.byteLength(chunk);
    if (size > 74) throw new Error('New passwords must be between 1 and 72 UTF-8 bytes');
    chunks.push(chunk);
  }
  const password = new TextDecoder('utf-8', {fatal: true}).decode(Buffer.concat(chunks)).replace(/\r?\n$/, '');
  if (!password || Buffer.byteLength(password) > 72 || /[\r\n\0]/.test(password)) {
    throw new Error('New passwords must be a single line between 1 and 72 UTF-8 bytes');
  }
  return bcrypt.hash(password, 10);
}

async function provision(database, args, input) {
  const [command, username, role, ...sites] = args;
  if (!['create', 'access', 'password', 'disable'].includes(command) || typeof username !== 'string' ||
      !username.trim() || username.length > 128 || /[\x00-\x1f\x7f]/.test(username)) throw new Error(usage);
  let result;
  if (command === 'create' || command === 'access') {
    if (!['viewer','operator','admin'].includes(role) || !sites.length || sites.length > 256 ||
        sites.some(site => !/^[a-zA-Z0-9_.-]{1,128}$/.test(site)) || new Set(sites).size !== sites.length) throw new Error(usage);
    const known = await database.query('SELECT id FROM sites WHERE id = ANY($1::text[])', [sites]);
    if (known.rowCount !== sites.length) throw new Error('Unknown case site');
    if (command === 'create') {
      result = await database.query(`INSERT INTO reviewer_accounts(id, username, name, password_hash, role, sites)
        VALUES ($1, $2, $2, $3, $4, $5) RETURNING id`,
      [randomUUID(), username, await passwordHash(input), role, sites]);
    } else {
      result = await database.query('UPDATE reviewer_accounts SET role = $2, sites = $3 WHERE username = $1 RETURNING id',
        [username, role, sites]);
    }
  } else {
    if (args.length !== 2) throw new Error(usage);
    if (command === 'disable') {
      result = await database.query('UPDATE reviewer_accounts SET enabled = false WHERE username = $1 RETURNING id', [username]);
    } else {
      result = await database.query('UPDATE reviewer_accounts SET password_hash = $2 WHERE username = $1 RETURNING id',
        [username, await passwordHash(input)]);
    }
  }
  if (!result.rowCount) throw new Error('Unknown reviewer');
}

if (require.main === module) {
  provision(pool, process.argv.slice(2), process.stdin)
    .then(() => console.log('Reviewer directory updated.'))
    .catch(error => {
      // A pg error may include the rejected row or bound input.
      console.error(error.code ? 'Reviewer directory update failed. Check the database, user and schema.' : error.message);
      process.exitCode = 1;
    })
    .finally(() => pool?.end());
}

module.exports = {provision};

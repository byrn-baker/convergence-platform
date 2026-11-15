'use strict';

const {createPasswordVerifier} = require('./password-verification');

function webAccounts(database, environment) {
  const source = environment.CONVERGENCE_ACCOUNT_SOURCE || 'environment';
  if (source === 'postgres') {
    const secret = environment.JWT_SECRET?.trim();
    if (!secret || Buffer.byteLength(secret) < 32 || /^(?:dev-secret$|change-me|<generate with:)/i.test(secret)) {
      throw new Error('The PostgreSQL account source requires an explicit JWT signing secret');
    }
    if (!database) throw new Error('PostgreSQL account source requires a database');
    const {findReviewerAccount} = require('../reviewer/grants');
    return {
      async authenticate(username, password) {
        let observed;
        const verify = createPasswordVerifier(async name => {
          observed = await findReviewerAccount(database, name);
          return observed;
        });
        const user = await verify(username, password);
        return user && {...user, directory: 'postgres', revision: observed.revision};
      },
      async current(claims) {
        if (claims.directory !== 'postgres' || typeof claims.revision !== 'string') return false;
        // One revision covers credentials, enabled state, role and sites. A
        // directory change requires a fresh web login, including on another replica.
        const result = await database.query(
          'SELECT 1 FROM reviewer_accounts WHERE id=$1 AND enabled AND revision=$2', [claims.sub, claims.revision]);
        return result.rowCount === 1;
      },
    };
  }
  if (source !== 'environment') throw new Error('Invalid Convergence account source');
  let users = [];
  try {
    const configured = JSON.parse(environment.USERS || '[]');
    if (!Array.isArray(configured)) throw new Error();
    users = configured;
  } catch {
    console.error('Invalid USERS configuration');
  }
  return {
    authenticate: createPasswordVerifier(username => users.find(user => user?.username === username), users[0]?.passwordHash),
    current: claims => !claims.directory,
  };
}

module.exports = {webAccounts};

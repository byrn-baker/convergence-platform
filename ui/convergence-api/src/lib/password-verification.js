'use strict';

const bcrypt = require('bcrypt');

// Public salt and checksum used only for comparisons when the account is absent.
const DUMMY_SALT_AND_CHECKSUM = 'nF42pwqCfs6/2QG2bFqEX.nGnLI6/GCNXxSGWKhSa6qBnxpvX1VL6';

// bcrypt 6 also verifies the original $2$ format. The final salt/checksum
// characters carry two/four significant bits in bcrypt's base64 alphabet.
const BCRYPT_HASH = /^\$2[ab]?\$(0[4-9]|[12]\d|3[01])\$[./A-Za-z0-9]{21}[.Oeu][./A-Za-z0-9]{30}[.CGKOSWaeimquy26]$/;

function bcryptCost(hash) {
  return typeof hash === 'string' && BCRYPT_HASH.exec(hash)?.[1];
}

class InvalidCredentialInput extends Error {}

function createPasswordVerifier(findUser, representativeHash) {
  // Match the directory's bcrypt cost on misses. Empty directories use the setup default.
  const cost = bcryptCost(representativeHash) || '10';
  const dummyHash = '$2b$' + cost + '$' + DUMMY_SALT_AND_CHECKSUM;

  return async function verifyPassword(username, password) {
    if (typeof username !== 'string' || !username || typeof password !== 'string' || !password) {
      throw new InvalidCredentialInput();
    }

    const user = await findUser(username);
    const hash = user?.passwordHash;
    const usable = Boolean(bcryptCost(hash));
    const valid = await bcrypt.compare(password, usable ? hash : dummyHash);
    if (!usable || !valid) return null;

    return { id: user.id, name: user.name || user.username, role: user.role, sites: user.sites };
  };
}

module.exports = { createPasswordVerifier, InvalidCredentialInput };

'use strict';

const express = require('express');
const jwt = require('jsonwebtoken');
const { InvalidCredentialInput } = require('../lib/password-verification');
const router = express.Router();

const JWT_SECRET = process.env.JWT_SECRET || 'dev-secret';
const TOKEN_EXPIRY = '24h';

const {webAccounts} = require('../lib/web-accounts');
const accounts = webAccounts(process.env.CONVERGENCE_ACCOUNT_SOURCE === 'postgres' ? require('../db').pool : null, process.env);

/**
 * POST /api/auth/login
 * Issue a JWT token for valid credentials.
 */
router.post('/login', async (req, res) => {
  const { username, password } = req.body || {};
  let user;
  try {
    user = await accounts.authenticate(username, password);
  } catch (err) {
    if (err instanceof InvalidCredentialInput) {
      return res.status(400).json({ error: 'Username and password required' });
    }
    console.error('Credential verification failed');
    return res.status(500).json({ error: 'Authentication error' });
  }

  if (!user) {
    return res.status(401).json({ error: 'Invalid credentials' });
  }

  const token = jwt.sign(
    {
      sub: user.id,
      name: user.name,
      role: user.role,
      sites: user.sites,
      ...(user.directory && {directory: user.directory, revision: user.revision}),
    },
    JWT_SECRET,
    { expiresIn: TOKEN_EXPIRY }
  );

  res.json({
    token,
    expiresIn: 86400,
    sites: user.sites,
    role: user.role,
    name: user.name
  });
});

module.exports = router;

'use strict';

const express = require('express');
const jwt = require('jsonwebtoken');
const { createPasswordVerifier, InvalidCredentialInput } = require('../lib/password-verification');
const router = express.Router();

const JWT_SECRET = process.env.JWT_SECRET || 'dev-secret';
const TOKEN_EXPIRY = '24h';

// Load users from environment
let users = [];
try {
  const configured = JSON.parse(process.env.USERS || '[]');
  if (!Array.isArray(configured)) throw new Error();
  users = configured;
} catch {
  console.error('Invalid USERS configuration');
}

const verifyPassword = createPasswordVerifier(
  username => users.find(user => user?.username === username),
  users[0]?.passwordHash,
);

/**
 * POST /api/auth/login
 * Issue a JWT token for valid credentials.
 */
router.post('/login', async (req, res) => {
  const { username, password } = req.body || {};
  let user;
  try {
    user = await verifyPassword(username, password);
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
      sites: user.sites
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

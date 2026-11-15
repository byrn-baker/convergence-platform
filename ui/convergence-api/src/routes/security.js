'use strict';
const router = require('express').Router();
const {readVmSecurity, readSecurityHistory, readSecurity} = require('../services/security');
const {httpRead} = require('../services/http-read');
router.get('/vms', httpRead(readVmSecurity));
router.get('/history', httpRead(readSecurityHistory));
router.get('/', httpRead(readSecurity));
module.exports = router;

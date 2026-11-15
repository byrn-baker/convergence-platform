'use strict';
const router = require('express').Router();
const {readHealth} = require('../services/health');
const {httpRead} = require('../services/http-read');
router.get('/', httpRead(readHealth));
module.exports = router;

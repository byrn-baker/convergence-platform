'use strict';
const router = require('express').Router();
const {readAlerts} = require('../services/alerts');
const {httpRead} = require('../services/http-read');
router.get('/', httpRead(readAlerts));
module.exports = router;

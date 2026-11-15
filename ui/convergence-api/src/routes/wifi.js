'use strict';
const router = require('express').Router();
const {readWifi} = require('../services/wifi');
const {httpRead} = require('../services/http-read');
router.get('/', httpRead(readWifi));
module.exports = router;

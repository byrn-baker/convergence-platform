'use strict';
const router = require('express').Router();
const {readDevices} = require('../services/devices');
const {httpRead} = require('../services/http-read');
router.get('/', httpRead(readDevices));
module.exports = router;

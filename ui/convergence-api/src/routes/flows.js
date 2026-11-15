'use strict';
const router = require('express').Router();
const {httpRead} = require('../services/http-read');
const {readFlows,readFlowRecords} = require('../services/flows');
router.get('/records',httpRead(readFlowRecords));
router.get('/',httpRead(readFlows));
module.exports = router;

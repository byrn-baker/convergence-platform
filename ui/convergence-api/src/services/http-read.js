'use strict';
const {ReadError} = require('./read-error');
function httpRead(read) {
  return async (req, res) => {
    try { res.json(await read(req.site, req.query, req.params)); }
    catch (error) {
      if (error instanceof ReadError) return res.status(error.status).json(error.body);
      res.status(503).json({site:req.site, unavailable:true, error:'Telemetry unavailable'});
    }
  };
}
module.exports = {httpRead};

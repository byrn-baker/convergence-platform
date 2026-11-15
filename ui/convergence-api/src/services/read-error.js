'use strict';
// Read services return domain data or a typed failure; HTTP adapters own responses.
class ReadError extends Error {
  constructor(status, body) { super(body.error || body.reason || 'Read unavailable'); this.status = status; this.body = body; }
}
function fail(status, body) { throw new ReadError(status, body); }
module.exports = {ReadError, fail};

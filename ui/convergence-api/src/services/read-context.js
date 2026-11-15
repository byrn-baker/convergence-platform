'use strict';
const {AsyncLocalStorage} = require('node:async_hooks');
const reads = new AsyncLocalStorage();
function withReadContext(signal, operation) { return reads.run({signal}, operation); }
function readSignal(timeoutMs) {
  const timeout = AbortSignal.timeout(timeoutMs);
  const signal = reads.getStore()?.signal;
  return signal ? AbortSignal.any([signal,timeout]) : timeout;
}
function currentRead() { return reads.getStore(); }
module.exports = {withReadContext,readSignal,currentRead};

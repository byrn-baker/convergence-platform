import test from 'node:test';
import assert from 'node:assert/strict';
import {bulkTarget, queueTarget} from '../reviewer/service-client.mjs';

test('queue and bulk targets are built only from validated parts', () => {
  assert.equal(queueTarget('red','signatures'),'/v1/sites/red/signatures');
  assert.equal(queueTarget('red','cases','window=7d&signature=2900417&before=MTc5_-'),'/v1/sites/red/cases?window=7d&signature=2900417&before=MTc5_-');
  assert.equal(queueTarget('red','cases',''),'/v1/sites/red/cases');
  assert.equal(bulkTarget('red','2900417',true,'window=24h&source=2001:db8::17'),
    '/v1/sites/red/signatures/2900417/dispositions/preview?window=24h&source=2001:db8::17');
  assert.equal(bulkTarget('red','2900417',false),'/v1/sites/red/signatures/2900417/dispositions');
  // A write never carries a query, whatever is passed.
  assert.equal(bulkTarget('red','2900417',false,'window=24h'),'/v1/sites/red/signatures/2900417/dispositions');
  const invalid = [
    () => queueTarget('..','cases'), () => queueTarget('red/blue','cases'), () => queueTarget('','cases'), () => queueTarget(7,'cases'),
    () => queueTarget('red','grants'), () => queueTarget('red','cases','a=b#c'), () => queueTarget('red','cases','a=%2e%2e'),
    () => queueTarget('red','cases','a=b c'), () => queueTarget('red','cases','a=b/../c'), () => queueTarget('red','cases','?a=b'),
    () => queueTarget('red','cases',{}), () => queueTarget('red','cases','a'.repeat(513)),
    () => bulkTarget('red','',true), () => bulkTarget('red','a/b',true), () => bulkTarget('red','..%2f',true), () => bulkTarget('red',7,true),
    () => bulkTarget('red','1'.repeat(65),true), () => bulkTarget('..','1',false), () => bulkTarget('red','1',true,'a'.repeat(129)),
  ];
  for (const build of invalid) assert.throws(build, error => error.status === 400, String(build));
});

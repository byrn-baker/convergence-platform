import test from 'node:test';
import assert from 'node:assert/strict';
import {HomeView} from '../modules/convergence/HomeView.js';
import {diaryHTML} from '../modules/convergence/DiaryExplorer.js';
import {reviewerLink} from '../modules/convergence/ReviewerLink.js';

const site = 'test-lab';
const id = '22222222-2222-4222-8222-222222222222';
const origin = 'https://review.example.test';
test('local diary routes case work to a granted reviewer and renders inventory failure explicitly', () => {
  const caseData = {id, site_id:site, timestamp:new Date().toISOString(), alert_name:'SuricataIntrusion'};
  const diary = diaryHTML({events:[caseData]}, {site, readOnly:true, reviewerOrigin:origin});
  assert.match(diary, /Review this case/);
  assert.doesNotMatch(diary, /data-jev-review=/);
  assert.equal(reviewerLink('https://user@review.example.test', site, id), '');
  assert.equal(reviewerLink('http://review.example.test', site, id), '');
  assert.equal(reviewerLink(origin + '/path', site, id), '');
  assert.doesNotMatch(reviewerLink(origin, site, 'invalid'), /#site/);
  const view = new HomeView(null, {hostAccess:{}});
  view.cache.models = {live:{defaultsPrimary:'test/model'}};
  assert.match(view.htmlModels(), /test\/model/);
  assert.doesNotMatch(view.htmlModels(), /data-models-action/);
  view.cache.devices = {site, unavailable:true, reason:'Site-scoped inventory is unavailable.'};
  assert.match(view.htmlDevices(), /Site-scoped inventory is unavailable/);
  assert.doesNotMatch(view.htmlDevices(), /No devices in response/);
});

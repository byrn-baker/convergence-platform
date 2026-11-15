import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {HomeView} from '../modules/convergence/HomeView.js';
import {jevReviewHTML} from '../modules/convergence/JevReview.js';

const event = review => ({id:'case', alert_name:'SuricataIntrusion', jev_review:review});
const doc = html => new JSDOM(html).window.document;

test('JEV offers IDS-only review and queued delivery can be checked again', () => {
  assert.equal(jevReviewHTML({alert_name:'WanDown'}), '');
  assert.equal(doc(jevReviewHTML(event())).querySelector('button').textContent, 'Review with JEV');
  const pending = doc(jevReviewHTML(event({status:'queued'}), true));
  assert.ok(pending.querySelector('button').disabled);
  assert.ok(pending.querySelector('details').open);
  const queued = doc(jevReviewHTML(event({status:'queued'})));
  assert.equal(queued.querySelector('button').textContent, 'Check review');
  assert.equal(queued.querySelector('button').disabled, false);
});

test('typed advice is escaped and its score cannot be confused with incident probability', () => {
  const document = doc(jevReviewHTML(event({status:'ok', model:'<img src=x>', assessment_id:'<script>bad</script>',
    answers:{next_check:{choice:'capture'}, evidence_strength:{score:0.2}},
    questions:{next_check:{criteria:{capture:'<img src=y> Check capture artifacts'}},
      evidence_strength:{criteria:['Insufficient evidence', 'Weak', 'Corroborated', 'Verified']}},
    input_summary:{rule_family:'protocol_anomaly'}, cost_usd:0.00001})));
  assert.equal(document.querySelector('img'),null);assert.equal(document.querySelector('script'),null);
  assert.match(document.body.textContent,/Check capture artifacts/);
  assert.match(document.body.textContent,/0.20 on a 0–3 rubric/);
  assert.match(document.body.textContent,/not an incident probability/);
  assert.match(document.body.textContent,/Abstract evidence sent/);
  assert.equal(document.querySelector('button'),null);
  const failure = doc(jevReviewHTML(event({status:'budget_exhausted'})));
  assert.match(failure.body.textContent,/No conclusion about network safety/);
});

function view() {
  return {site:'home', cache:{events:{events:[event()]}, escalated:{events:[event()]}},
    jevPending:new Set(), jevErrors:new Map(), renderSubview(){}, waitJevPoll:async()=>{}};
}

test('case polling survives an initially absent result and updates Diary and Triage', async () => {
  const context = view(), calls=[];
  context.fetch = async(path, options) => {
    calls.push({path,options});
    if(options?.method === 'POST') return {status:'accepted'};
    if(calls.length === 2) return event();
    return event({status:'ok',assessment_id:'result'});
  };
  await HomeView.prototype.handleJevReview.call(context,'case');
  assert.equal(calls.length,3);assert.ok(calls.every(c=>c.path.includes('site=home')));
  assert.equal(context.cache.events.events[0].jev_review.assessment_id,'result');
  assert.equal(context.cache.escalated.events[0].jev_review.assessment_id,'result');
  assert.equal(context.jevPending.size,0);
});

test('review completion cannot replace another site and polling is bounded', async () => {
  let context=view();context.fetch=async()=>{context.site='other';return {review:{status:'ok'}}};
  await HomeView.prototype.handleJevReview.call(context,'case');
  assert.equal(context.cache.events.events[0].jev_review,undefined);
  context=view();let calls=0;context.fetch=async()=>{calls++;return {status:'accepted'}};
  await HomeView.prototype.handleJevReview.call(context,'case');
  assert.equal(calls,16);assert.equal(context.jevPending.size,0);
  assert.match(context.jevErrors.get('home:case'),/still queued/);
});

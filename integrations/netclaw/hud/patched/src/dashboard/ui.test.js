import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { JSDOM, VirtualConsole } from 'jsdom';
const output = await build({ entryPoints: [new URL('./main.jsx',import.meta.url).pathname], bundle:true, write:false, outfile:'ui.js', format:'iife', define:{'process.env.NODE_ENV':'"production"'} });
const js=output.outputFiles.find(f=>f.path.endsWith('.js')).text;
const settle=()=>new Promise(resolve=>setTimeout(resolve,30));
async function waitFor(find, label) {
  const deadline=Date.now()+3000;
  while(Date.now()<deadline) { const value=find(); if(value)return value; await settle(); }
  assert.fail(`Timed out waiting for ${label}`);
}
async function app(t, preview=true, fetcher, runtime){const errors=[];const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',e=>errors.push(e.message));const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost:3000',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole});dom.window.NETCLAW_PREVIEW=preview;if(fetcher)dom.window.fetch=(url,options)=>url==='/api/runtime'?Promise.resolve({ok:true,json:async()=>(runtime || {kind:'openclaw',installationId:'11111111-1111-4111-8111-111111111111',label:'OpenClaw',readiness:{ready:true},capabilities:{}})}):fetcher(url,options);dom.window.Headers=Headers;dom.window.AbortSignal=AbortSignal;dom.window.AbortController=AbortController;dom.window.eval(js);t.after(()=>dom.window.close());await settle();return {document:dom.window.document,window:dom.window,errors};}
function click(document,label){const button=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===label || b.textContent.trim().startsWith(label));assert.ok(button,`button ${label}`);button.click();}
test('sending from Avatar unlocks voice before the request and speaks only a safe notice',async t=>{
  const calls=[],order=[];let finish;
  const {document,window,errors}=await app(t,false,async(url,options={})=>{
    calls.push([url,options]);
    if(url==='/api/chat'){order.push('chat');return await new Promise(resolve=>{finish=()=>resolve({ok:true,json:async()=>({fromGateway:true,response:'Private network answer 10.1.2.3'})});});}
    if(url==='/api/pal/local/speech')return {ok:true,arrayBuffer:async()=>new ArrayBuffer(44)};
    return {ok:true,json:async()=>url==='/api/pal/local/status'?{available:true,maxCharacters:1800}:url==='/api/chat/models'?{models:[]}:{}};
  });
  let starts=0;
  window.AudioContext=class {
    state='suspended';destination={};
    createGain(){return {gain:{value:1},connect(){},disconnect(){}};}
    async resume(){order.push('unlock');this.state='running';}
    async close(){} async decodeAudioData(){return {};}
    createBufferSource(){return {connect(){},disconnect(){},start(){starts++;},stop(){}};}
    createAnalyser(){return {connect(){},disconnect(){},getFloatTimeDomainData(array){array.fill(.04);}};}
  };
  click(document,'Avatar');await settle();
  assert.match(document.querySelector('.local-pal-state').textContent,/Voice off/);
  typeChat(window,document,'Check this');await settle();
  document.querySelector('.chat-composer').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.deepEqual(order.slice(0,2),['unlock','chat']);
  finish();await settle();await settle();
  const speech=calls.filter(([url])=>url==='/api/pal/local/speech');
  assert.equal(speech.length,1);assert.deepEqual(JSON.parse(speech[0][1].body),{kind:'notice',notice:'ready',rate:1});
  assert.equal(starts,1);assert.match(document.querySelector('.local-pal-state').textContent,/Speaking/);
  document.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await settle();
  assert.match(document.querySelector('.local-pal-state').textContent,/Voice ready/);
  click(document,'Read latest reply');await settle();
  assert.equal(JSON.parse(calls.filter(([url])=>url==='/api/pal/local/speech').at(-1)[1].body).text,'Private network answer 10.1.2.3');
  assert.deepEqual(errors,[]);
});
test('local Pal shares the Chat draft and thread; avatar choice never starts Tavus or sends a model request',async t=>{
  const calls=[];
  const {document,window,errors}=await app(t,false,async(url,options={})=>{
    calls.push([url,options]);return {ok:true,json:async()=>url==='/api/pal/local/status'?{available:true,maxCharacters:1800}:url==='/api/chat/models'?{models:[]}:{}};
  });
  typeChat(window,document,'Keep this question while I choose a Pal');await settle();
  const chat=document.querySelector('.standard-chat');
  click(document,'Avatar');await settle();
  assert.deepEqual([...document.querySelector('[aria-label="Chat interface"]').querySelectorAll('button,a')].map(node=>node.textContent),['Chat','Canvas','OpenClaw ↗','Avatar','Convergence']);
  assert.equal([...document.querySelectorAll('nav button')].some(button=>/Avatar|Pal/.test(button.textContent)),false);
  assert.equal(document.querySelector('.standard-chat'),chat);
  assert.equal(document.querySelectorAll('#standard-chat-message').length,1);
  assert.equal(document.querySelector('#standard-chat-message').value,'Keep this question while I choose a Pal');
  const lobster=await waitFor(()=>[...document.querySelectorAll('[aria-label="Avatar selection"] button')].find(button=>button.textContent.includes('Lobster')), 'Lobster avatar control');
  assert.ok(lobster);lobster.click();await settle();
  assert.equal(lobster.getAttribute('aria-pressed'),'true');
  assert.equal(window.localStorage.getItem('nc-pal-avatar-v1'),'lobster');
  click(document,'00Chat');await settle();
  assert.equal(document.querySelector('.standard-chat'),chat);
  assert.equal(document.querySelector('#standard-chat-message').value,'Keep this question while I choose a Pal');
  assert.equal(calls.some(([url])=>url==='/api/chat'||url==='/api/pal/sessions'||url==='/api/pal/status'),false);
  assert.deepEqual(errors,[]);
});
test('production dashboard renders and Basic/Advanced retains navigation without errors',async t=>{const {document,errors}=await app(t);assert.match(document.body.textContent,/SYNTHETIC PREVIEW/);assert.match(document.querySelector('h1').textContent,/^Chat/);click(document,'01Overview');await settle();assert.match(document.body.textContent,/Execution members/);click(document,'Advanced');await settle();assert.equal(document.querySelector('button[aria-pressed=true]').textContent,'Advanced');for(const label of ['Risk of Claws','External neighbours','Mobile devices','Science Officer','Network','Knowledge','Operations','Integrations','Settings','RAG','Configuration','Canvas','Tokenomics','Documentation','Logs','Security']){const button=[...document.querySelectorAll('nav button')].find(b=>b.textContent.includes(label));assert.ok(button,label);button.click();await settle();assert.match(document.querySelector('h1').textContent,new RegExp(label));}assert.deepEqual(errors,[]);});
test('member inspector uses exact identity; Three.js not required for selection',async t=>{const {document,errors}=await app(t);click(document,'03Risk of Claws');await settle();document.querySelector('button[aria-label="Inspect Network Claw"]').click();await settle();assert.match(document.querySelector('.inspector').textContent,/demo\/network/);assert.match(document.querySelector('.inspector').textContent,/Execution member/);assert.equal(document.querySelector('canvas'),null);assert.deepEqual(errors,[]);});
test('typed assessment comparison shows Noul probability, Choice answer and Score separately',async t=>{const {document,errors}=await app(t);click(document,'06Science Officer');await settle();click(document,'Inspect synthetic original');await settle();assert.equal(document.querySelectorAll('.assessment').length,2);assert.match(document.body.textContent,/0.72/);assert.match(document.body.textContent,/interfaces/);assert.match(document.body.textContent,/Rubric position/);assert.match(document.body.textContent,/not network health/);assert.match(document.body.textContent,/Border's interpretation/);assert.deepEqual(errors,[]);});

test('Canvas tab mounts real canvas.html once and preserves iframe across navigation and mode changes',async t=>{
  const {document,errors}=await app(t,false,async()=>({ok:true,json:async()=>({})}));
  click(document,'02Canvas');await settle();const frame=document.querySelector('iframe');
  assert.ok(frame);assert.equal(frame.getAttribute('src'),'/canvas.html?embedded=1');
  assert.equal(document.querySelector('.canvas-workspace').hidden,false);
  click(document,'01Overview');await settle();assert.equal(document.querySelector('.canvas-workspace').hidden,true);
  click(document,'Advanced');await settle();click(document,'02Canvas');await settle();
  assert.equal(document.querySelector('iframe'),frame);assert.equal(document.querySelector('.canvas-workspace').hidden,false);
  assert.ok(document.querySelector('a[href="/canvas.html"]'));assert.doesNotMatch(document.body.textContent,/Adam's/);assert.deepEqual(errors,[]);
});
test('RAG and configuration are available in Basic; preview never uploads or reads credentials',async t=>{
  const {document,errors}=await app(t);
  click(document,'12RAG');await settle();assert.match(document.body.textContent,/Collections & ingestion/);
  assert.equal(document.querySelector('input[type=file]').disabled,true);
  assert.match(document.body.textContent,/Branch routing runbook/);
  click(document,'13Configuration');await settle();assert.match(document.body.textContent,/TYPESAFE_API_KEY/);
  assert.match(document.body.textContent,/Values stay masked/);assert.deepEqual(errors,[]);
});
test('RAG retrieval uses selected collection and passes cited results to Canvas draft review',async t=>{
  const calls=[];
  const fetcher=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>url==='/api/rag/search'?{collection:'documents',results:[{chunk_id:'c1',title:'Runbook',chunk_text:'Check counters',citation:'[Runbook p.2]',score:0.8}]}:url==='/api/rag/documents'?{documents:[],snapshots:[],replicas:[]}:url==='/api/rag/stats'?{collections:['documents']}: {}};};
  const {document,window,errors}=await app(t,false,fetcher);click(document,'12RAG');await settle();
  const input=document.querySelector('textarea');Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(input,'routing');input.dispatchEvent(new window.Event('input',{bubbles:true}));await settle();
  input.closest('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle();
  const request=calls.find(([url])=>url==='/api/rag/search');assert.ok(request);assert.deepEqual(JSON.parse(request[1].body),{query:'routing',collection:'documents',k:5});
  assert.match(document.body.textContent,/Runbook p.2/);click(document,'Use evidence in Canvas');await settle();
  assert.match(document.querySelector('.context-review').textContent,/Runbook p.2/);assert.match(document.querySelector('h1').textContent,/Canvas/);assert.deepEqual(errors,[]);
});
test('RAG upload submits multipart fields and treats 202 as pending rather than ready',async t=>{
  const calls=[];const fetcher=async(url,options={})=>{calls.push([url,options]);return {ok:true,json:async()=>url==='/api/rag/upload'?{status:'pending'}:url==='/api/rag/documents'?{documents:[],snapshots:[],replicas:[]}: {}};};
  const {document,window,errors}=await app(t,false,fetcher);click(document,'12RAG');await settle();
  const form=document.querySelector('input[type=file]').closest('form');form.querySelector('[name=title]').value='Upload regression';
  form.dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle();
  const upload=calls.find(([url])=>url==='/api/rag/upload');assert.ok(upload);assert.equal(upload[1].method,'POST');assert.equal(upload[1].body.get('title'),'Upload regression');assert.equal(upload[1].body.get('doc_type'),'other');assert.ok(upload[1].body.has('file'));assert.equal(upload[1].headers,undefined);assert.match(document.body.textContent,/Ingestion is pending/);assert.deepEqual(errors,[]);
});
test('RAG failures stay visible without reporting empty collections or successful uploads',async t=>{
  const {document,window}=await app(t,false,async url=>({ok:!url.startsWith('/api/rag'),status:503,json:async()=>({})}));click(document,'12RAG');await settle();
  assert.match(document.querySelector('[role=alert]').textContent,/could not complete/);assert.doesNotMatch(document.body.textContent,/No documents reported/);
  document.querySelector('input[type=file]').closest('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle();assert.match(document.querySelector('[role=status]').textContent,/could not complete/);assert.doesNotMatch(document.body.textContent,/Upload accepted/);
});

test('Claw inspector shows exact reported MCP tools and model, not generic capabilities',async t=>{
  const {document}=await app(t);click(document,'03Risk of Claws');await settle();document.querySelector('button[aria-label="Inspect Network Claw"]').click();await settle();const detail=document.querySelector('.inspector');assert.match(detail.textContent,/example\/network-model/);assert.match(detail.textContent,/pyats_run_show_command/);assert.match(detail.textContent,/tool list not reported/);
});
test('Tokenomics distinguishes recorded usage, heuristics, cache and separate Jev budgets',async t=>{
  const {document}=await app(t);click(document,'14Tokenomics');await settle();assert.match(document.body.textContent,/42,000/);assert.match(document.body.textContent,/Cache read/);assert.match(document.body.textContent,/\$0.17 per assistant turn/);assert.match(document.body.textContent,/not added together/);
});
test('Documentation includes Sean guide, CLI flags, MCP reference and real OpenAPI routes',async t=>{
  const {document}=await app(t);click(document,'15Documentation');await settle();assert.ok(document.querySelector('a[href="https://www.seanmahoney.ai/guides/netclaw-overview/"]'));click(document,'CLI reference');await settle();assert.match(document.body.textContent,/scripts\/netclaw/);assert.match(document.body.textContent,/--edge/);click(document,'MCP reference');await settle();assert.match(document.body.textContent,/rag_search/);click(document,'HTTP API');await settle();assert.match(document.body.textContent,/GET \/api\/hud\/logs/);
});
test('Logs filters synthetic tail and hands exact selected evidence to Canvas without executing commands',async t=>{
  const {document,window}=await app(t);click(document,'16Logs');await settle();const select=[...document.querySelectorAll('select')].find(s=>s.textContent.includes('warning'));select.value='warning';select.dispatchEvent(new window.Event('change',{bubbles:true}));await settle();assert.equal(document.querySelectorAll('tbody tr').length,1);assert.match(document.querySelector('tbody').textContent,/heartbeat delayed/);click(document,'Investigate ↗');await settle();assert.match(document.querySelector('.context-review').textContent,/heartbeat delayed/);assert.match(document.querySelector('h1').textContent,/Canvas/);
});

test('Overview labels LAB separately and Security distinguishes DefenseClaw, OpenShell and host confinement',async t=>{
  const {document}=await app(t);click(document,'01Overview');await settle();assert.match(document.body.textContent,/LAB bypass enabled/);click(document,'Inspect Security');await settle();assert.match(document.body.textContent,/Host member confinement/);assert.match(document.body.textContent,/OpenShell is not required/);assert.match(document.body.textContent,/Observe mode/);click(document,'DefenseClaw logs');await settle();assert.equal(document.querySelector('select').value,'defenseclaw');assert.match(document.body.textContent,/tail -n 200 ~\/\.defenseclaw\/gateway.log/);
});

function typeChat(window, document, value) {
  const input = document.querySelector('#standard-chat-message');
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(input,value);
  input.dispatchEvent(new window.Event('input',{bubbles:true}));
}
function submitChat(window, document) {
  document.querySelector('.chat-composer').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
}
test('Chat is default; native OpenClaw opens separately and Canvas and Chat drafts survive navigation',async t=>{
  const {document,window,errors}=await app(t,false,async url=>({ok:true,json:async()=>url==='/api/hud/runtime'?{controlUi:{available:true,port:19443,basePath:'/ops',tls:true}}:{}}));
  assert.match(document.querySelector('h1').textContent,/^Chat/);
  typeChat(window,document,'Unsent private question');await settle();
  const link=document.querySelector('.chat-switch a');assert.equal(link.href,'https://127.0.0.1:19443/ops/');assert.equal(link.target,'_blank');assert.match(link.rel,/noopener/);assert.match(link.rel,/noreferrer/);assert.equal(link.getAttribute('referrerpolicy'),'no-referrer');
  click(document,'02Canvas');await settle();const frame=document.querySelector('iframe');assert.ok(frame);
  link.addEventListener('click',e=>e.preventDefault());link.click();await settle();assert.equal(document.querySelector('iframe'),frame);
  click(document,'Advanced');click(document,'00Chat');await settle();assert.equal(document.querySelector('#standard-chat-message').value,'Unsent private question');
  click(document,'02Canvas');await settle();assert.equal(document.querySelector('iframe'),frame);assert.deepEqual(errors,[]);
});
test('standard Chat bootstraps scoped session, sends isolated chronological turns and retains response while hidden',async t=>{
  const calls=[];let deliver;const reply=new Promise(resolve=>deliver=resolve);
  const {document,window,errors}=await app(t,false,async(url,options={})=>{calls.push([url,options]);if(url==='/api/chat')return reply;return{ok:true,json:async()=>({})};});
  typeChat(window,document,'First question');await settle();submitChat(window,document);submitChat(window,document);await settle();
  assert.equal(calls.filter(([url])=>url==='/api/chat').length,1);
  assert.ok(calls.findIndex(([url])=>url==='/api/hud/session')<calls.findIndex(([url])=>url==='/api/chat'));
  const body=JSON.parse(calls.find(([url])=>url==='/api/chat')[1].body);assert.match(body.hudThread,/^chat-/);assert.deepEqual(body.messages,[{role:'user',content:'First question'}]);
  assert.equal([...document.querySelectorAll('button')].find(b=>b.textContent==='New chat').disabled,true);
  click(document,'02Canvas');await settle();const frame=document.querySelector('iframe');
  deliver({ok:true,json:async()=>({fromGateway:true,response:'Verified synthetic response <img src=x onerror=alert(1)>',assessmentRefs:[{taskRef:'owned-task',assessmentId:'owned-assessment'},{taskRef:'bad/../../',assessmentId:'unsafe'}]})});await settle();
  click(document,'00Chat');await settle();assert.equal(document.querySelectorAll('.chat-message').length,2);assert.equal(document.querySelector('.chat-message img'),null);assert.match(document.querySelector('.chat-message.assistant').textContent,/Verified synthetic/);
  assert.equal(document.querySelector('.chat-message.assistant a').getAttribute('href'),'/assessment.html?task=owned-task&assessment=owned-assessment');assert.equal(document.querySelectorAll('.chat-message.assistant a').length,1);
  assert.equal(document.querySelector('iframe'),frame);assert.equal(calls.some(([url])=>url==='/api/chat/history'),false);assert.deepEqual(errors,[]);
});
test('failed bootstrap, HTTP failure, gateway fallback and empty replies restore drafts without fabricated assistant messages',async t=>{
  for(const mode of ['bootstrap','http','fallback','empty','network']){
    const calls=[];const {document,window}=await app(t,false,async(url,options)=>{calls.push(url);if(url==='/api/hud/session')return {ok:mode!=='bootstrap'};if(url==='/api/chat'){if(mode==='network')throw new window.TypeError('private transport detail');return{ok:mode!=='http',status:503,json:async()=>({fromGateway:mode!=='fallback',response:mode==='empty'?'':'UNVERIFIED FALLBACK'})};}return{ok:true,json:async()=>({})};});
    typeChat(window,document,'Retain this draft');await settle();submitChat(window,document);await settle();
    assert.equal(document.querySelector('#standard-chat-message').value,'Retain this draft',mode);assert.equal(document.querySelectorAll('.chat-message.assistant').length,0,mode);assert.ok(document.querySelector('[role=alert]'),mode);assert.doesNotMatch(document.querySelector('[role=alert]').textContent,/UNVERIFIED|private transport/);
    assert.equal(calls.filter(url=>url==='/api/chat').length,mode==='bootstrap'?0:1,mode);
  }
});
test('new chat archives history and previous chat restores its original context',async t=>{
  const sent=[];const {document,window}=await app(t,false,async(url,options)=>{if(url==='/api/chat'){sent.push(JSON.parse(options.body));return{ok:true,json:async()=>({fromGateway:true,response:'Synthetic answer'})};}return{ok:true,json:async()=>({})};});
  for(const question of ['One','Two']){typeChat(window,document,question);await settle();submitChat(window,document);await settle();}
  assert.equal(sent[0].hudThread,sent[1].hudThread);assert.equal(sent[1].messages.length,3);
  click(document,'New chat');await settle();assert.equal(document.querySelectorAll('.chat-message').length,0);
  const picker=document.querySelector('#previous-chat');picker.value=`local:${sent[0].hudThread}`;picker.dispatchEvent(new window.Event('change',{bubbles:true}));await settle();
  assert.equal(document.querySelectorAll('.chat-message').length,4);
  click(document,'New chat');await settle();
  typeChat(window,document,'Fresh');await settle();submitChat(window,document);await settle();assert.notEqual(sent[2].hudThread,sent[1].hudThread);assert.deepEqual(sent[2].messages,[{role:'user',content:'Fresh'}]);
});
test('native switch remains disabled for failed runtime reads and synthetic preview never sends',async t=>{
  const {document}=await app(t,false,async url=>({ok:url!=='/api/hud/runtime',json:async()=>({})}));assert.equal(document.querySelector('.chat-switch a'),null);assert.equal(document.querySelector('.chat-unavailable button').disabled,true);
  const preview=await app(t);assert.equal(preview.document.querySelector('#standard-chat-message').disabled,true);assert.equal(preview.document.querySelector('.chat-switch a'),null);assert.match(preview.document.querySelector('.chat-transcript').textContent,/Synthetic example/);
});
test('composer Enter sends but Shift+Enter and IME composition do not',async t=>{
  const sent=[];const {document,window}=await app(t,false,async(url,options)=>{if(url==='/api/chat')sent.push(JSON.parse(options.body));return{ok:true,json:async()=>url==='/api/chat'?{fromGateway:true,response:'Synthetic answer'}:{}};});
  typeChat(window,document,'Keyboard question');await settle();const input=document.querySelector('#standard-chat-message');
  input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',shiftKey:true,bubbles:true,cancelable:true}));
  input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true}));await settle();assert.equal(sent.length,0);
  input.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));await settle();assert.equal(sent.length,1);
});


test('Avatar quiet preference survives switching views and never starts automatic speech',async t=>{
  const speech=[];
  const {document,window,errors}=await app(t,false,async(url,options={})=>{
    if(url==='/api/pal/local/speech')speech.push(options);
    return {ok:true,json:async()=>url==='/api/pal/local/status'?{available:true,maxCharacters:1800}:url==='/api/chat'?{fromGateway:true,response:'Saved answer'}:url==='/api/chat/models'?{models:[]}:{}};
  });
  click(document,'Avatar');await settle();
  const select=document.querySelector('#local-pal-autospeak');select.value='off';select.dispatchEvent(new window.Event('change',{bubbles:true}));await settle();
  click(document,'00Chat');await settle();click(document,'Avatar');await settle();
  assert.equal(document.querySelector('#local-pal-autospeak').value,'off');
  typeChat(window,document,'A quiet question');await settle();
  document.querySelector('.chat-composer').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await settle();
  assert.match(document.querySelector('.chat-transcript').textContent,/Saved answer/);
  assert.equal(speech.length,0);assert.match(document.querySelector('.local-pal-state').textContent,/Voice off/);
  click(document,'00Chat');await settle();click(document,'Avatar');await settle();
  assert.ok([...document.querySelectorAll('button')].find(button=>button.textContent==='Read latest reply'));
  assert.equal(speech.length,0);assert.deepEqual(errors,[]);
});


test('full open lab workspace offers normal Chat Canvas RAG members and protected operations without sessions',async t=>{
 const calls=[],id='11111111-1111-4111-8111-111111111111';
 const runtime={kind:'openclaw',installationId:id,label:'NetClaw Border',readiness:{ready:true,executionVerified:false},access:{mode:'open-lab',principal:{issuer:'open-lab',subject:'8a15663b-80ba-572a-a355-2fa6ddf1267c',display_name:'Unauthenticated lab operator',roles:['lab-operator']}},capabilities:{protectedProfile:true,protectedOperations:true,protectedProduction:true,remoteHud:{schema:1,features:['overview','chat','canvas','rag','members','peers','skills','runtime','tokenomics','documentation','reference','logs','operations']}}};
 const {document,window,errors}=await app(t,false,async(url,options={})=>{
  calls.push([url,options]);assert.ok(!url.includes('/session'),url);
  return {ok:true,json:async()=>url==='/api/chat/models'?{models:[],selectionSupported:false}:url==='/api/chat/conversations'?{conversations:[],sourceAvailable:true}:url==='/api/chat'?{fromGateway:true,response:'A fixture conversation reply'}:url==='/api/rag/stats'?{document_count:1,total_chunks:1,collections:['documents']}:url==='/api/rag/documents'?{documents:[{id:'doc',title:'Actual fixture document',ingest_status:'ready'}]}:url==='/api/hud/operations/catalog'?{revision:'fixture',members:[]}: {}};
 },runtime);
 await waitFor(()=>document.querySelector('#standard-chat-message'),'normal Chat composer: '+JSON.stringify({errors,body:document.body.textContent}));assert.match(document.querySelector('h1').textContent,/Chat/);
 typeChat(window,document,'Hello shared lab');await settle();submitChat(window,document);await waitFor(()=>document.body.textContent.includes('A fixture conversation reply'),'conversation response');
 const chat=calls.find(([url])=>url==='/api/chat');assert.equal(chat[1].credentials,'omit');assert.equal(chat[1].headers.get('X-NetClaw-Installation'),id);
 click(document,'02Canvas');await settle();assert.equal(document.querySelector('iframe').getAttribute('src'),'/canvas.html?embedded=1');
 click(document,'12RAG');await waitFor(()=>document.body.textContent.includes('Actual fixture document'),'RAG inventory');
 click(document,'20Approved operations');await waitFor(()=>document.body.textContent.includes('No eligible protected member profiles'),'protected catalog');
 assert.equal([...document.querySelectorAll('nav button')].some(b=>b.textContent.includes('Configuration')),false);
 assert.equal(calls.some(([url])=>url.includes('/session')),false);assert.ok(calls.every(([,o])=>o.credentials==='omit'));assert.deepEqual(errors,[]);
});

function convergenceFetch(calls, override = () => undefined) {
  return async (url, options = {}) => {
    calls.push([url, options]);
    const path = new URL(url, 'http://localhost').pathname;
    const fixtures = {
      '/api/home/status': { configured: true },
      '/api/home/sites': { sites: [{ id: 'branch', name: 'Branch' }, { id: 'home', name: 'Home' }] },
      '/api/home/health': { site: 'branch', healthScore: { value: 97, status: 'healthy' }, wanLatency: { value: 8 }, wanLoss: { value: 0 }, alertCount: { firing: 1 } },
      '/api/home/wifi': { clients: { wireless: 24, wired: 6, total: 30 } },
      '/api/home/devices': { switches: [{ name: 'BranchSwitch', status: 'up', portsUp: 8, portsTotal: 24 }] },
      '/api/home/events/escalated': { events: [{ id: 'case-1', alert_name: 'LinkDown', message: 'Branch uplink needs review', status: 'escalated', severity: 'warning' }] },
      '/api/home/events': { events: [{ id: 'case-1', alert_name: 'LinkDown', message: 'Investigation recorded', status: 'escalated' }] },
      '/api/home/events/case-1/reinvestigate': { reinvestigate: { status: 'accepted' } },
      '/api/models': { sot: { brain: 'provider/brain', alert: 'provider/alert', fallback: '', file: '.env' }, live: {} },
      '/api/graph': { devices: [{ name: 'BranchSwitch', protocol: 'ssh' }] },
      '/api/ssh/capabilities': { enabled: true, dedicatedAccount: true },
      '/api/chat/models': { models: [] },
    };
    const changed = override(path, url, options);
    if (changed instanceof Error) throw changed;
    const data = changed ?? fixtures[path] ?? {};
    return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
  };
}

test('Convergence loads on demand, discovers sites, and preserves the Chat draft and device section', async t => {
  const calls = [];
  const { document, window, errors } = await app(t, false, convergenceFetch(calls));
  typeChat(window, document, 'Keep my network question');
  assert.equal(calls.some(([url]) => url.startsWith('/api/home')), false);
  window.localStorage.setItem('netclaw.convergence.site', 'retired-site');
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.home-kpi-value')?.textContent === '97', 'live Convergence metrics');
  assert.equal(document.querySelector('h1').textContent, 'Convergence.');
  assert.equal(document.querySelector('[aria-label="Search entities and capabilities"]'), null);
  await waitFor(() => document.querySelector('.heading-actions').textContent.includes('Convergence available'), 'Convergence connection status');
  assert.equal(document.querySelector('#home-site-select').value, 'branch');
  document.querySelector('[data-home-sub="devices"]').click();
  await waitFor(() => document.querySelector('[data-console-device="BranchSwitch"]'), 'device console action');
  assert.equal(calls.filter(([url]) => url.startsWith('/api/home/devices')).length, 1, 'inline toolbar does not bind duplicate listeners');
  const workspace = document.querySelector('.convergence-workspace');
  click(document, '00Chat'); await settle();
  assert.equal(workspace.hidden, true);
  assert.equal(document.querySelector('#standard-chat-message').value, 'Keep my network question');
  click(document, 'Convergence'); await settle();
  assert.equal(document.querySelector('.convergence-workspace'), workspace);
  assert.equal(document.querySelector('[data-home-sub="devices"]').getAttribute('aria-pressed'), 'true');
  assert.match(workspace.textContent, /BranchSwitch/);
  assert.deepEqual(errors, []);
});

test('Convergence preserves triage notes across sections and sends feedback with the active site', async t => {
  const calls = [];
  const { document, window, errors } = await app(t, false, convergenceFetch(calls));
  window.localStorage.setItem('netclaw.convergence.site', 'retired-site');
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.home-kpi-value'), 'Convergence');
  document.querySelector('[data-home-sub="triage"]').click();
  const notes = await waitFor(() => document.querySelector('.home-triage-textarea'), 'triage notes');
  notes.value = 'Check the peer and keep this context';
  notes.dispatchEvent(new window.Event('input', { bubbles: true }));
  document.querySelector('[data-home-sub="wifi"]').click(); await settle();
  document.querySelector('[data-home-sub="triage"]').click(); await settle();
  assert.equal(document.querySelector('.home-triage-textarea').value, notes.value);
  document.querySelector('[data-triage-action="need_more"]').click();
  await waitFor(() => calls.find(([url, opts]) => url.includes('/reinvestigate') && opts.method === 'POST'), 'feedback request');
  const [url, options] = calls.find(([url, opts]) => url.includes('/reinvestigate') && opts.method === 'POST');
  assert.equal(url, '/api/home/events/case-1/reinvestigate?site=branch');
  assert.deepEqual(JSON.parse(options.body), { expert_feedback: notes.value, site: 'branch' });
  await settle();
  const picker = document.querySelector('#home-site-select');
  picker.value = 'home'; picker.dispatchEvent(new window.Event('change', { bubbles: true }));
  await waitFor(() => window.localStorage.getItem('netclaw.convergence.site') === 'home' && !document.querySelector('#home-site-select').disabled, 'site switch');
  assert.equal(document.querySelector('.home-triage-textarea').value, '');
  assert.deepEqual(errors, []);
});

test('Convergence keeps Models drafts while navigating and passes site observations to Canvas without inference', async t => {
  const calls = [];
  const { document, window, errors } = await app(t, false, convergenceFetch(calls));
  window.localStorage.setItem('netclaw.convergence.site', 'retired-site');
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.home-kpi-value'), 'Convergence');
  document.querySelector('[data-home-sub="models"]').click();
  const brain = await waitFor(() => document.querySelector('[data-models-field="brain"]'), 'Models');
  brain.value = 'provider/my-draft'; brain.dispatchEvent(new window.Event('input', { bubbles: true }));
  click(document, '00Chat'); await settle(); click(document, 'Convergence'); await settle();
  assert.equal(document.querySelector('[data-models-field="brain"]').value, 'provider/my-draft');
  assert.equal([...document.querySelectorAll('button')].find(b => b.textContent === 'Investigate in Canvas ↗').disabled, true);
  document.querySelector('[data-home-sub="overview"]').click(); await settle();
  click(document, 'Investigate in Canvas ↗'); await settle();
  assert.match(document.querySelector('h1').textContent, /Canvas/);
  assert.match(document.querySelector('.context-review pre').textContent, /Convergence · branch · overview/);
  assert.match(document.querySelector('.context-review pre').textContent, /\/api\/home\/health/);
  assert.equal(calls.some(([url, options]) => /^\/api\/(home|models|ssh)/.test(url) && options.method && options.method !== 'GET'), false);
  assert.equal(calls.some(([url]) => url === '/api/chat'), false);
  assert.deepEqual(errors, []);
});

test('Convergence reports API and diary failures without presenting an empty queue as healthy', async t => {
  const calls = []; let failed = false;
  const { document, window, errors } = await app(t, false, convergenceFetch(calls, path => {
    if (path === '/api/home/events/escalated') return { unavailable: true, reason: 'Diary database offline', events: [] };
    if (failed && path === '/api/home/health') return new Error('Telemetry offline');
  }));
  window.localStorage.setItem('netclaw.convergence.site', 'retired-site');
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.home-kpi-value'), 'Convergence');
  document.querySelector('[data-home-sub="triage"]').click();
  await waitFor(() => document.querySelector('.home-panel').textContent.includes('Diary database offline'), 'unavailable queue');
  assert.doesNotMatch(document.querySelector('.home-panel').textContent, /Queue empty|No escalated cases/);
  assert.match(document.querySelector('.heading-actions').textContent, /Convergence unavailable/);
  failed = true;
  document.querySelector('[data-home-sub="overview"]').click();
  await waitFor(() => document.querySelector('.home-panel').textContent.includes('Telemetry offline'), 'failed refresh');
  assert.match(document.querySelector('.convergence-source').textContent, /showing previous observations/);
  assert.equal(document.querySelector('.home-kpi-value').textContent, '97');
  assert.deepEqual(errors, []);
});

test('synthetic preview Convergence never reads or changes the live network', async t => {
  const calls = [];
  const { document, errors } = await app(t, true, convergenceFetch(calls));
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.convergence-workspace'), 'preview Convergence');
  assert.match(document.querySelector('.convergence-workspace').textContent, /disabled in this synthetic preview/);
  assert.equal(document.querySelector('[aria-label="Refresh Convergence sources"]').disabled, true);
  assert.equal(calls.length, 0);
  assert.deepEqual(errors, []);
});

test('Convergence OpenAI preset shows applied values, clears fallback and refreshes Chat discovery', async t => {
  const calls = [];
  const { document, errors } = await app(t, false, convergenceFetch(calls, (path, url, options) => {
    if (path === '/api/models' && options.method === 'POST') return { sot: { brain: 'openai/gpt-6.1-sol', alert: 'openai/gpt-6-luna', fallback: '' } };
  }));
  const initialDiscovery = calls.filter(([url]) => url === '/api/chat/models').length;
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('[data-home-sub="models"]'), 'Convergence');
  document.querySelector('[data-home-sub="models"]').click();
  await waitFor(() => document.querySelector('[data-models-field="brain"]'), 'Models controls');
  document.querySelector('[data-preset="openai"]').click();
  await waitFor(() => document.querySelector('[data-models-field="brain"]')?.value === 'openai/gpt-6.1-sol', 'applied model values');
  assert.equal(document.querySelector('[data-models-field="alert"]').value, 'openai/gpt-6-luna');
  assert.equal(document.querySelector('[data-models-field="fallback"]').value, '');
  await waitFor(() => calls.filter(([url]) => url === '/api/chat/models').length > initialDiscovery, 'Chat catalog refresh');
  assert.deepEqual(JSON.parse(calls.find(([url, options]) => url === '/api/models' && options.method === 'POST')[1].body), { preset: 'openai', restart: true });
  assert.deepEqual(errors, []);
});

test('Convergence refreshes a recovered IDS feed while its section button retains focus', async t => {
  const calls = []; let status = 'degraded';
  const { document, window, errors } = await app(t, false, convergenceFetch(calls, path => {
    if (path === '/api/home/security') return { status: 'active', intrusionDetection: { status, sensors: [] } };
  }));
  const timers = [], original = window.setInterval.bind(window);
  window.setInterval = (callback, delay, ...args) => {
    if (delay === 30000) timers.push(callback);
    return original(callback, delay, ...args);
  };
  click(document, 'Convergence');
  await waitFor(() => document.querySelector('.home-kpi-value'), 'Convergence');
  const section = document.querySelector('[data-home-sub="security"]');
  section.focus(); section.click();
  await waitFor(() => document.querySelector('.security-telemetry strong')?.textContent === 'degraded', 'initial degraded IDS feed');
  assert.equal(document.activeElement, section);
  status = 'active';
  assert.ok(timers.length > 0, 'refresh timers are registered');
  timers.forEach(callback => callback());
  await waitFor(() => document.querySelector('.security-telemetry strong')?.textContent === 'active', 'recovered IDS feed without another click');
  assert.equal(document.activeElement, section, 'section navigation retains keyboard focus');
  document.querySelector('[data-home-sub="triage"]').click();
  const notes = await waitFor(() => document.querySelector('.home-triage-textarea'), 'triage editor');
  notes.focus(); notes.value = 'Keep this unsent assessment';
  notes.dispatchEvent(new window.Event('input', { bubbles: true }));
  const reads = calls.filter(([url]) => url.startsWith('/api/home/')).length;
  timers.forEach(callback => callback()); await settle();
  assert.equal(calls.filter(([url]) => url.startsWith('/api/home/')).length, reads, 'editing still pauses content refresh');
  assert.equal(document.activeElement, notes);
  assert.equal(notes.value, 'Keep this unsent assessment');
  assert.deepEqual(errors, []);
});

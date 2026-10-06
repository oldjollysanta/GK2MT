// Run: node test_web.js. No browser, network, or game files are touched.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, 'web/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, 'web/index.html'), 'utf8');
const nodes = new Map();
const $ = id => {
  if (!nodes.has(id)) {
    let value = '';
    nodes.set(id, {get value() { return value; }, set value(v) { value = String(v); },
      textContent: '', innerHTML: '', classList: {toggle() {}}, close() {},
      checked:id==='nexus-remember',events:new Map(),addEventListener(event,callback){this.events.set(event,callback);},
      focus(){},scrollIntoView(){},querySelector(){return {focus(){}};},
      insertAdjacentHTML(_, html) { this.innerHTML += html; }});
  }
  return nodes.get(id);
};
const update = {id: 'mod1', name: 'Test mod', nexus_mod_id: 12, file_id: 34,
  known_version: true, installed_version: '1.0', version: '2.0', downloadable: false,
  manual_installable: true, blocked_reason: 'Nexus Premium is required for automatic downloads'};
const state = {nexus_connected: true, updates: {account: {name: 'User', premium: false}, updates: [update]},
  download_panel: {available: true, folder: 'C:\\Local\\GK2MT\\nexus-downloads'}, downloads: [],
  settings: {deck: {host: '192.168.1.50', user: 'deck', port: 22, key: '', game_path: '/game', workshop_path: '/workshop'}},
  deck_connection: {connected: true, host: '192.168.1.50', user: 'deck'}};
let dialog, refreshes = 0;
const requests = [];
const timers = new Map();
let nextTimer = 0, jobs = [], workingCalls = 0, retryState = 'verifying', deckResult, protonResult, nexusError,deckUITesting=false;
const context = vm.createContext({$, state, comparison: null, toast() {},focusAfterWork(element){element?.focus({preventScroll:true});},
  setTimeout(callback) { timers.set(++nextTimer, callback); return nextTimer; },
  clearTimeout(id) { timers.delete(id); },
  modal(...args) { dialog = args; }, work: async (_, task) => { workingCalls++; return task(); }, refresh: async () => { refreshes++;if(deckUITesting)vm.runInContext('renderDeckConnection()',context); },
  api: async (action, body) => {
    requests.push([action, body === undefined ? undefined : JSON.parse(JSON.stringify(body))]);
    if (action === 'nexus-connect' || action === 'nexus-forget') {
      assert.equal($('nexus-key').value, '', 'clear the API key before sending the request');
      if(nexusError)throw nexusError;
      return {};
    }
    if (action === 'download-panel') {
      jobs = [{id: 'job1', update_id: body.id, name: 'Test mod', version: '2.0', state: 'waiting',
        message: 'Choose the download in the panel.', cancellable: true, retryable: false}];
      return jobs[0];
    }
    if (action === 'downloads') return {download_panel: state.download_panel, downloads: jobs};
    if (action === 'download-panel-cancel') {
      jobs = [{...jobs[0], state: 'cancelled', cancellable: false}];
      return jobs[0];
    }
    if (action === 'download-panel-retry') {
      jobs = [{...jobs[0], state: retryState, retryable: false}];
      return jobs[0];
    }
    if (action === 'update-archive-preview') return {token: 'preview123', name: 'Test mod', version: '2.0',
      files: 2, conflicts: ['BepInEx/plugins/Test.dll'], warnings: []};
    if (action === 'update-archive') return {updated: [{name: 'Test mod', version: '2.0'}], message: 'Updated'};
    if (action === 'deck-connect') {
      assert.equal($('deck-password').value, '', 'clear the password before sending authentication');
      throw Error('simulated authentication error');
    }
    if (action === 'deck-preview') {
      if(deckResult instanceof Error)throw deckResult;
      return deckResult;
    }
    if (action === 'deck-proton-status' || action === 'deck-proton-setup') {
      if(protonResult instanceof Error)throw protonResult;
      return typeof protonResult === 'function' ? protonResult() : protonResult;
    }
    if (action === 'deck-guide') return {text: '# Setup\n<guide> & recovery'};
    if (action === 'open-external') return {};
    throw Error('Unexpected API call: ' + action);
  }});
vm.runInContext(source.slice(source.indexOf('const escapeHTML'), source.indexOf('function toast')), context);
vm.runInContext(source.slice(source.indexOf('function modDuplicates'), source.indexOf('function updateReason')), context);
vm.runInContext(source.slice(source.indexOf('function updateReason'), source.indexOf('async function stagePath')), context);
vm.runInContext(source.slice(source.indexOf('function compareView'), source.indexOf("document.addEventListener('click'")), context);
vm.runInContext(source.slice(source.indexOf("$('nexus-connect').onclick"), source.indexOf("$('check-updates').onclick")), context);
const run = script => vm.runInContext(script, context);

function modSourceChecks() {
  state.mods = [
    {id:'steam',name:'Talent & Tech Refund',source:'Steam Workshop',workshop_id:'123',nexus_mod_id:10},
    {id:'linked-steam',name:'Subscribed mod',source:'GK2MT',workshop_id:'456'},
    {id:'imported-nexus',name:'Imported release',source:'GK2MT',nexus_mod_id:11},
    {id:'vortex-nexus',name:'Linked release',source:'Vortex',nexus_mod_id:12,version:'0.3.0',display_version:'0.3.1'},
    {id:'imported-manual',name:'Local import',source:'GK2MT'},
    {id:'unlinked',name:'Unlinked release',source:'Vortex'},
  ];
  state.rules = []; state.conflicts = [];
  const labels=['Steam','Steam','Nexus','Nexus','Manual','Manual'];
  for(const [i,mod] of state.mods.entries()) {
    assert.equal(run(`sourceLabel(state.mods[${i}])`),labels[i]);
    run(`details(${JSON.stringify(mod.id)})`);
    assert.match(dialog[1],new RegExp(`class="badge ${labels[i].toLowerCase()}">${labels[i]}</span>`));
  }
  run('renderMods()');
  assert.equal($('list-count').textContent,6);
  assert.doesNotMatch($('mod-list').innerHTML,/GK2MT|Vortex/);
  assert.equal(($('mod-list').innerHTML.match(/href="#i-workshop"/g)||[]).length,2);
  assert.match($('mod-list').innerHTML,/0\.3\.1/);
  assert.doesNotMatch($('mod-list').innerHTML,/0\.3\.0/);
  run('details("vortex-nexus")');
  assert.match(dialog[1],/id="detail-version"[^>]*value="0\.3\.1"/);
  assert.match(dialog[1],/Installed release: <strong>0\.3\.1<\/strong>\. The plugin reports 0\.3\.0/);
  for(const label of ['Steam','Nexus','Manual']) {
    $('source-filter').value=label;
    run('renderMods()');
    assert.equal($('list-count').textContent,2);
    assert.equal(($('mod-list').innerHTML.match(new RegExp(`class="badge ${label.toLowerCase()}">${label}</span>`,'g'))||[]).length,2);
    $('source-filter').value=''; $('search').value=label;
    run('renderMods()');
    assert.equal($('list-count').textContent,2,'source names are searchable');
    $('search').value='';
  }
  run('renderRules()');
  assert.match($('conflicts-list').innerHTML,/Installed file overlaps only\. Mods can still conflict when the game runs\./,'zero file overlaps does not imply runtime compatibility');
  assert.match($('rule-first').innerHTML,/value="imported-nexus"/);
  assert.match($('rule-first').innerHTML,/value="imported-manual"/);
  assert.doesNotMatch($('rule-first').innerHTML,/value="vortex-nexus"|value="unlinked"|value="steam"/);
  assert.equal(state.mods[2].source,'GK2MT','display labels never replace ownership');
  const filter=html.match(/<select id="source-filter"[^>]*>(.*?)<\/select>/)[1];
  assert.equal(filter,'<option value="">All sources</option><option>Steam</option><option>Nexus</option><option>Manual</option>');
  assert.match(html,/<th>Source<\/th>/);
}

function libraryConflictChecks(){
  const inputs=new Map(),node=id=>{if(!inputs.has(id))inputs.set(id,{value:'',innerHTML:'',textContent:'',classList:{toggle(){}}});return inputs.get(id);};
  const fixture={mods:[{id:'one',name:'First <mod>',source:'GK2MT',enabled:true},{id:'two',name:'Second <mod>',source:'GK2MT',enabled:true}],rules:[],settings:{},game_found:true,loader_installed:true,locations_confirmed:true,
    conflicts:[{path:'BepInEx/plugins/different.dll',packages:['one','two'],winner:'two',identical:false},{path:'BepInEx/plugins/shared<asset>.json',packages:['one','two'],winner:'two',identical:true},{path:'BepInEx/plugins/unchecked.dll',packages:['one','two'],winner:'one'}]};
  const ui=vm.createContext({$:node,state:fixture,renderWorkshopSetup(){},renderMods(){},renderDuplicates(){},renderUpdates(){},renderDeckConnection(){}});
  const execute=script=>vm.runInContext(script,ui);
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('function render()'),source.indexOf('function renderWorkshopSetup')));
  execute(source.slice(source.indexOf('function renderRules()'),source.indexOf('function updateReason')));
  execute('render()');
  assert.equal(node('setup').disabled,true,'detected BepInEx keeps the default installer disabled');assert.equal(node('setup').textContent,'BepInEx detected');
  assert.equal(node('conflict-count').textContent,2,'identical shared files do not increase the conflict metric; unchecked records remain conflicts');
  const content=node('conflicts-list').innerHTML,shared=content.match(/<details class="import-shared">[\s\S]*?<\/details>/)[0];
  assert.equal((content.match(/ wins<\/span>/g)||[]).length,2,'only differing or unchecked overlaps have winner rows');
  assert.match(shared,/1 identical shared file · no file conflict/);assert.match(shared,/shared&lt;asset&gt;\.json/);assert.match(shared,/First &lt;mod&gt; · Second &lt;mod&gt;/);
  assert.doesNotMatch(shared,/ wins|class="warning"|class="danger"|<details[^>]*\bopen\b/,'shared-file information is neutral and initially folded');
  fixture.conflicts=[fixture.conflicts[1]];execute('render()');assert.equal(node('conflict-count').textContent,0);
  assert.match(node('conflicts-list').innerHTML,/No differing file overlaps/);assert.doesNotMatch(node('conflicts-list').innerHTML,/ wins<\/span>/);
  assert.match(node('conflicts-list').innerHTML,/Mods can still conflict when the game runs/,'a zero metric keeps the runtime compatibility limit visible');
  fixture.conflicts=[];execute('render()');assert.equal(node('conflict-count').textContent,0);assert.doesNotMatch(node('conflicts-list').innerHTML,/class="import-shared"/,'no empty shared-file disclosure is shown');
  fixture.loader_installed=false;execute('render()');assert.equal(node('setup').disabled,false,'a valid game with missing BepInEx enables account-free setup');
}

async function workshopRefreshChecks() {
  const calls=[],warnings=[],fills=[];
  const scan=vm.createContext({state:null,api:async(action,body)=>{calls.push([action,body]);return {settings:{},workshop_title_warning:action==='rescan'?'Workshop temporarily unavailable':''};},
    fields:settings=>fills.push(settings),render(){},toast:(message,error)=>warnings.push([message,error])});
  vm.runInContext(source.slice(source.indexOf('async function refresh('),source.indexOf('function render()')),scan);
  await vm.runInContext('refresh()',scan);
  assert.deepEqual(calls[0],['state',null],'ordinary refreshes stay offline');
  await vm.runInContext('refresh(true,true)',scan);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1])),['rescan',{force_workshop_titles:false}]);
  assert.equal(fills.length,1);
  await vm.runInContext('refresh(false,true)',scan);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[2])),['rescan',{force_workshop_titles:true}]);
  assert.equal(warnings.length,2);
  assert.deepEqual(warnings[1],['Workshop temporarily unavailable',true]);
  assert.match(source,/await refresh\(false,true\);if\(!state\.workshop_title_warning && !state\.nexus_discovery_warning\)toast\('Inventory refreshed\.'/);
  assert.match(source,/Reading your mod setup[^\n]+await refresh\(true,true\)/);
}

async function desktopBridgeChecks() {
  assert.doesNotMatch(source, /\bfetch\s*\(|X-GK2MT-Token|gk2mt-token|\/api\//);
  assert.doesNotMatch(html, /gk2mt-token|__TOKEN__/);
  assert.doesNotMatch(source, /href="\/deck-guide"/);
  const calls=[], events=new Map();
  const window={addEventListener(name,callback,options){assert.equal(options.once,true);events.set(name,callback);}};
  const desktop=vm.createContext({window});
  const bridgeSource=source.slice(source.indexOf('const bridgeReady'),source.indexOf('let state'));
  const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('async function work('));
  vm.runInContext(bridgeSource+apiSource,desktop);
  const request=vm.runInContext('api("state")',desktop);
  await Promise.resolve();
  assert.equal(calls.length,0,'requests wait until the native bridge is ready');
  window.pywebview={api:{request:async(action,body)=>{calls.push([action,body]);return {ok:true,result:{loaded:true}};}}};
  events.get('pywebviewready')();
  assert.equal((await request).loaded,true);
  assert.deepEqual(calls,[['state',null]]);
  window.pywebview.api.request=async()=>({ok:false,error:'Native operation failed'});
  await assert.rejects(vm.runInContext('api("browse", {kind:"zip"})',desktop),/Native operation failed/);
  window.pywebview.api.request=async()=>null;
  await assert.rejects(vm.runInContext('api("state")',desktop),/Request failed/);
  window.pywebview.api.request=async(action,body)=>({ok:true,result:body});
  const alreadyReady=vm.createContext({window});
  vm.runInContext(bridgeSource+apiSource,alreadyReady);
  assert.equal((await vm.runInContext('api("browse", {kind:"zip"})',alreadyReady)).kind,'zip');
}

function navigationChecks(){
  const scrolling=[],focused=[],headings=new Map(),pageNodes=new Map();
  const pages=['mods','browse','profiles','updates','rules','deck','settings'].map(name=>({id:'page-'+name}));
  const navigation=pages.map(entry=>({dataset:{page:entry.id.slice(5)},classList:{toggle(){}},setAttribute(){},removeAttribute(){}}));
  const node=id=>{if(!pageNodes.has(id))pageNodes.set(id,{querySelector(){if(!headings.has(id))headings.set(id,{focus(options){focused.push([id,options]);}});return headings.get(id);}});return pageNodes.get(id);};
  const ui=vm.createContext({$:node,currentPage:'mods',working:false,location:{hash:''},window:{scrollTo:options=>scrolling.push(JSON.parse(JSON.stringify(options)))},document:{querySelectorAll:selector=>selector==='.page'?pages:navigation}});
  vm.runInContext(source.slice(source.indexOf('let pendingFocus='),source.indexOf('function modal(')),ui);
  vm.runInContext('page("updates")',ui);
  assert.deepEqual(scrolling,[{top:0,left:0,behavior:'instant'}]);
  assert.equal(focused[0][0],'page-updates');assert.equal(headings.get('page-updates').tabIndex,-1);
  assert.equal(focused[0][1].preventScroll,true,'new-page heading focus does not undo its scroll reset');
  vm.runInContext('page("updates")',ui);
  assert.equal(scrolling.length,1,'rerendering the same page preserves reading position and focus');
  assert.equal(focused.length,1);
  vm.runInContext('page("deck")',ui);assert.equal(focused.at(-1)[0],'page-deck');
}

async function deckRememberChecks() {
  const inputs=new Map(),calls=[],messages=[];
  const node=id=>{if(!inputs.has(id)){let value='';inputs.set(id,{get value(){return value;},set value(next){value=String(next);},checked:false,classList:{toggle(){}},events:new Map(),addEventListener(event,callback){this.events.set(event,callback);},scrollIntoView(){},focus(){},close(){const callback=this.events.get('close');this.events.delete('close');callback?.();}});}return inputs.get(id);};
  const config={host:'192.168.1.50',user:'deck',port:22,key:'',game_path:'/game',workshop_path:'/workshop'};
  const setup={settings:{deck:{...config}},deck_connection:{connected:false,password_saved:false}};
  let review,probe={known:false,host:config.host,port:22,algorithm:'ssh-ed25519',key:'public-key',fingerprint:'SHA256:test'},warning='';
  const deck=vm.createContext({$:node,state:setup,comparison:null,toast:(...args)=>messages.push(args),focusAfterWork:element=>element?.focus({preventScroll:true}),
    modal(...args){review=args;},refresh:async()=>{},work:async(_,task)=>{try{return await task();}catch{return null;}},
    api:async(action,body)=>{
      calls.push([action,JSON.parse(JSON.stringify(body))]);
      if(action==='deck-probe'){
        assert.equal(node('deck-password').value,'','clear the typed password before host-key probing');
        setup.settings.deck={...body.deck};
        if(probe instanceof Error)throw probe;
        return probe;
      }
      if(action==='deck-connect'){
        assert.equal(node('deck-password').value,'','never keep the password in the input during authentication');
        setup.settings.deck={...body.deck};setup.deck_connection={connected:true,host:body.deck.host,user:body.deck.user,password_saved:body.remember_password};
        return {deck:body.deck,discovery:{found:true},password_saved:body.remember_password,credential_warning:warning};
      }
      if(action==='deck-forget-password'){setup.deck_connection.password_saved=false;delete setup.deck_connection.password_error;return {message:'Password forgotten'};}
      if(action==='deck-disconnect'){setup.deck_connection.connected=false;return {};}
      if(action==='deck-proton-status')return {configured:true,loader_installed:true,prefix:'/fixture/prefix'};
      throw Error('Unexpected Deck request: '+action);
    }});
  const execute=script=>vm.runInContext(script,deck);
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('let deckProtonState'),source.indexOf('async function stagePath')));
  execute(source.slice(source.indexOf("$('deck-connect').onclick"),source.indexOf("$('deck-detect-paths').onclick")));
  execute('deckFieldsFill(state.settings.deck);renderDeckConnection()');
  assert.equal(node('deck-host').value,config.host,'restore the last IP from settings');
  assert.equal(node('deck-settings').open,false,'a saved address starts with compact settings');
  await node('deck-connect').onclick();
  assert.equal(calls.length,0,'missing credentials open connection settings without probing');
  assert.equal(node('deck-settings').open,true);
  node('deck-host').value='';await node('deck-connect').onclick();
  assert.equal(calls.length,0,'missing host also stays local and opens the correct field');
  node('deck-host').value=config.host;
  assert.equal(node('deck-remember-password').checked,false,'password saving defaults off');
  node('deck-remember-password').checked=true;
  execute('renderDeckConnection()');
  node('deck-game_path').events.get('input')();
  assert.equal(node('deck-remember-password').checked,true,'ordinary refreshes and folder edits preserve the choice');
  setup.deck_connection.password_saved=true;
  execute('deckPasswordTarget=undefined;renderDeckConnection()');
  assert.equal(node('deck-remember-password').checked,true,'matching saved credential opts in on startup');
  assert.match(node('deck-password').placeholder,/Saved password/);
  assert.equal(node('deck-password').value,'','a saved password is never revealed or filled in');
  assert.equal(node('deck-forget-password').hidden,false,'Forget is available while disconnected');
  node('deck-host').value='192.168.1.51';node('deck-password').value='old-target-secret';
  node('deck-host').events.get('input')();
  assert.equal(node('deck-remember-password').checked,false);
  assert.equal(node('deck-password').value,'','changing target drops typed credentials too');
  assert.equal(node('deck-forget-password').hidden,true);
  assert.doesNotMatch(node('deck-password').placeholder,/Saved password/);
  node('deck-host').value=config.host;node('deck-host').events.get('input')();
  node('deck-remember-password').checked=false;
  await node('deck-remember-password').events.get('change')();
  assert.equal(calls.at(-1)[0],'deck-forget-password','unchecking Remember removes the matching credential immediately');
  assert.equal(node('deck-remember-password').checked,false);
  node('deck-password').value='cancelled-test-password';
  await node('deck-connect').onclick();
  assert.doesNotMatch(review[1],/cancelled-test-password/);
  review[2][0][1]();
  await review[2][1][1]();
  const latestConnect=()=>calls.findLast(([action])=>action==='deck-connect')[1];
  assert.equal(latestConnect().password,'','cancel drops the password held for the trust dialog');
  node('deck-password').value='trust-test-password';node('deck-remember-password').checked=true;
  await node('deck-connect').onclick();
  await review[2][1][1]();
  assert.equal(latestConnect().password,'trust-test-password');
  assert.equal(latestConnect().remember_password,true);
  assert.equal(latestConnect().expected_key,'public-key');
  assert.equal(calls.at(-1)[0],'deck-proton-status','connected Decks receive a read-only loader check');
  assert.ok(!calls.some(([action])=>action==='deck-proton-setup'),'connection never installs a loader setting');
  await node('deck-disconnect').onclick();
  execute('renderDeckConnection()');
  assert.equal(setup.deck_connection.password_saved,true,'Disconnect preserves an opted-in saved password');
  probe={known:true};warning='Connected, but the password could not be saved.';
  await node('deck-connect').onclick();
  assert.equal(latestConnect().password,'','blank input permits backend reuse for the matching target');
  assert.equal(latestConnect().remember_password,true);
  assert.deepEqual(messages.at(-1),[warning,true]);
  await node('deck-forget-password').onclick();execute('renderDeckConnection()');
  assert.equal(node('deck-forget-password').hidden,true);
  assert.equal(node('deck-remember-password').checked,false);
  node('deck-host').value='192.168.1.99';node('deck-host').events.get('input')();node('deck-password').value='failed-probe-test-password';probe=Error('Deck offline');
  await node('deck-connect').onclick();
  assert.equal(setup.settings.deck.host,'192.168.1.99','a failed probe retains the attempted address');
  assert.equal(node('deck-host').value,'192.168.1.99');
  assert.equal(node('deck-password').value,'');
  setup.deck_connection.password_error='Saved password could not be restored. Forget it and enter it again.';
  execute('renderDeckConnection()');
  assert.equal(node('deck-forget-password').hidden,false,'an unreadable credential can be forgotten offline');
  assert.match(node('deck-password-status').textContent,/could not be restored/);
  node('deck-remember-password').checked=false;
  await node('deck-remember-password').events.get('change')();
  assert.equal(calls.at(-1)[0],'deck-forget-password','unchecking also removes an unreadable saved credential');
  assert.doesNotMatch(JSON.stringify(setup),/test-password|old-target-secret/);
  assert.match(html,/id="deck-remember-password" type="checkbox"/);
  assert.doesNotMatch(html+source,/password is used for this connection only and is never saved/);
}

async function nexusBrowserChecks() {
  const calls=[],timed=new Map(),messages=[],elements=new Map();let sequence=0,refreshed=0,jobs=[];
  const node=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',innerHTML:'',classList:{toggle(){}},querySelector(){return {focus(){}};}});return elements.get(id);};
  const setup={nexus_connected:false,updates:null,downloads:[],game_found:true,download_panel:{available:true,folder:'private managed folder'}};
  const pages=['mods','browse','profiles','updates','rules','deck','settings'].map(name=>({id:'page-'+name}));
  const navigation=pages.map(entry=>({dataset:{page:entry.id.slice(5)},classList:{toggle(){}},setAttribute(){},removeAttribute(){}}));
  const browser=vm.createContext({$:node,state:setup,currentPage:'mods',working:false,location:{hash:''},toast:(...args)=>messages.push(args),
    window:{scrollTo(){}},
    document:{querySelectorAll:selector=>selector==='.page'?pages:navigation},
    setTimeout:callback=>{timed.set(++sequence,callback);return sequence;},clearTimeout:id=>timed.delete(id),
    refresh:async()=>{refreshed++;},work:async(_,task)=>task(),
    api:async(action,body)=>{calls.push([action,body===undefined?undefined:JSON.parse(JSON.stringify(body))]);
      if(action==='nexus-browser'){jobs=[{id:'browser-'+calls.length,kind:'install',update_id:null,mod_id:0,name:'Nexus mod browser',version:'',state:'waiting',cancellable:true}];return jobs[0];}
      if(action==='downloads')return {download_panel:setup.download_panel,downloads:jobs};
      throw Error('Unexpected browser action: '+action);
    }});
  const execute=script=>vm.runInContext(script,browser);
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('let pendingFocus='),source.indexOf('function modal(')));
  execute(source.slice(source.indexOf('function updateReason'),source.indexOf('let deckProtonState')));
  execute('renderNexusBrowser()');
  assert.equal(node('nexus-browser-open').disabled,true);
  assert.equal(node('nexus-browser-connect').hidden,false);
  assert.match(node('nexus-browser-status').textContent,/Connect Nexus on Updates/);
  setup.nexus_connected=true;setup.download_panel.available=false;execute('renderNexusBrowser()');
  assert.equal(node('nexus-browser-open').disabled,true);
  setup.download_panel.available=true;setup.game_found=false;execute('renderNexusBrowser()');
  assert.match(node('nexus-browser-status').textContent,/game folder/);
  setup.game_found=true;execute('renderNexusBrowser();page("browse")');
  assert.equal(node('nexus-browser-open').disabled,false);
  assert.equal(node('nexus-browser-connect').hidden,true);
  assert.equal(node('breadcrumb').textContent,'Browse Nexus');
  assert.equal(node('download-status').hidden,false,'Browse and Updates share one download-status region');
  assert.equal(pages.find(entry=>entry.id==='page-browse').hidden,false);
  assert.equal(pages.find(entry=>entry.id==='page-updates').hidden,true);
  execute('page("updates")');assert.equal(node('download-status').hidden,false);
  execute('page("profiles")');assert.equal(node('download-status').hidden,false);
  execute('page("mods")');assert.equal(node('download-status').hidden,true);
  await execute('openNexusBrowser()');
  assert.deepEqual(calls[0],['nexus-browser',{}]);
  assert.equal(calls[1][0],'downloads');
  assert.equal(refreshed,1);
  assert.equal(timed.size,1,'opening the browser starts the existing status poll');
  assert.match(node('download-jobs').innerHTML,/Browsing Nexus/);
  assert.doesNotMatch(node('download-jobs').innerHTML,/small muted"> · <\/span>/);
  assert.match(node('download-jobs').innerHTML,/data-download-cancel=/);
  assert.equal(node('nexus-browser-open').disabled,true,'one download session at a time');
  jobs=[{...jobs[0],state:'downloading',received:50,total:100}];await execute('pollDownloads()');
  assert.match(node('download-jobs').innerHTML,/value="50"/);
  jobs=[{...jobs[0],state:'waiting_game',retryable:true}];await execute('pollDownloads()');
  assert.equal(node('nexus-browser-open').disabled,true);
  assert.match(node('download-jobs').innerHTML,/Retry installation/);
  assert.equal(timed.size,0);
  jobs=[{...jobs[0],state:'completed',name:'Verified <mod>',version:'1.2',cancellable:false,retryable:false}];await execute('pollDownloads()');
  assert.match(node('download-jobs').innerHTML,/Verified &lt;mod&gt;/);
  assert.match(node('download-jobs').innerHTML,/>Installed<\/span>/);
  assert.doesNotMatch(node('download-jobs').innerHTML,/>Updated<\/span>/);
  assert.equal(refreshed,2,'a completed browser install refreshes My mods');
  assert.equal(node('nexus-browser-open').disabled,false,'reopen the browser for another mod');
  await execute('openNexusBrowser()');
  assert.equal(calls.filter(([action])=>action==='nexus-browser').length,2);
  jobs=[{...jobs[0],state:'error',message:'Ambiguous <ZIP>',cancellable:false},
    {id:'update-complete',kind:'update',state:'completed',name:'Existing mod',version:'2.0'}];
  await execute('pollDownloads()');
  assert.match(node('download-jobs').innerHTML,/Ambiguous &lt;ZIP&gt;/);
  assert.match(node('download-jobs').innerHTML,/>Updated<\/span>/,'update completions retain their label');
  assert.match(html,/data-page="browse"/);
  assert.match(source,/\['mods','browse','profiles','updates','rules','deck','settings'\]\.includes\(location\.hash/);
  assert.match(source,/\['#mods','#browse','#profiles','#updates','#rules','#deck','#settings'\]\.includes\(href\)/);
  for(const id of ['download-status','download-jobs','download-folder','download-poll-error'])assert.equal((html.match(new RegExp(`id="${id}"`,'g')) || []).length,1,'shared download IDs are unique');
}

async function profileChecks() {
  const inputs=new Map(),calls=[],messages=[],timers=new Map();let review,click,zipOpened=0,imported=false,downloadState='waiting',jobs=[],nextTimer=0,saveError;
  const node=id=>{if(!inputs.has(id)){let value='';inputs.set(id,{get value(){return value;},set value(next){value=String(next);},checked:false,classList:{toggle(){}},events:new Map(),addEventListener(event,callback){this.events.set(event,callback);},querySelector(selector){return node(id+' '+selector);},focus(){this.focused=true;},scrollIntoView(){},close(){this.closeCount=(this.closeCount || 0)+1;}});}return inputs.get(id);};
  const setup={profiles:[{id:'p1',name:'Cozy <set>',mod_count:4,enabled_count:3,active:true}],active_profile:'p1',mods:[
    {id:'nexus-copy',name:'Quick <Stash>',nexus_mod_id:12,enabled:true,can_uninstall:true},
    {id:'steam-copy',name:'Steam copy',workshop_id:'123',enabled:true}],
    duplicates:[{row_ids:['nexus-copy','steam-copy'],reason:'Shared <GUID>',enabled_count:2,disabled_count:0}],
    nexus_connected:false,updates:null,downloads:[],download_panel:{available:true,folder:'managed downloads'},game_found:true};
  const entries=[
    {key:'nexus:10',name:'Needed <Nexus>',source:'Nexus',version:'1.0',enabled:true,status:'missing',nexus_mod_id:10,file_id:11},
    {key:'steam:222',name:'Needed Steam',source:'Steam',version:'1.0',enabled:true,status:'missing',workshop_id:222},
    {key:'manual:hash',name:'Needed Manual',source:'Manual',version:'1.0',enabled:true,status:'missing'},
    {key:'nexus:20',name:'Different variant',source:'Nexus',version:'1.1',installed_version:'1.1',enabled:true,status:'installed',nexus_mod_id:20,file_id:31,installed_file_id:32,version_mismatch:true,reason:'Different <variant>'},
    {key:'manual:off',name:'Unused Manual',source:'Manual',version:'1.0',enabled:false,status:'missing'}];
  let result={id:'p1',name:'Cozy <set>',token:'fresh-review',entries,changes:[{id:'old',name:'Old <mod>',enabled:false,source:'Nexus'}],missing:entries.filter(entry=>entry.status==='missing'),mismatches:[entries[3]],blockers:['Install <missing> mods first.']};
  const profile=vm.createContext({$:node,state:setup,comparison:null,currentPage:'profiles',toast:(...args)=>messages.push(args),openLink:()=>false,focusAfterWork:element=>element?.focus({preventScroll:true}),
    modal(...args){review=args;node('dialog-actions').lastElementChild={disabled:!!args[2]?.at(-1)?.[3]};},
    document:{addEventListener(event,callback){if(event==='click')click=callback;}},
    setTimeout:callback=>{timers.set(++nextTimer,callback);return nextTimer;},clearTimeout:id=>timers.delete(id),
    work:async(_,task)=>task(),refresh:async()=>{setup.downloads=jobs;},
    api:async(action,body)=>{calls.push([action,body===undefined?undefined:JSON.parse(JSON.stringify(body))]);
      if(action==='profile-compare')return {...result,id:body.id};
      if(action==='profile-save'){if(saveError)throw saveError;const id=body.id || 'new-profile';if(!body.id)setup.profiles.push({id,name:body.name,mod_count:3,enabled_count:2});return {profile:{id,name:body.name},message:'Saved'};}
      if(action==='profile-import'){if(!imported)return {profile:null};setup.profiles.push({id:'imported',name:'Shared <profile>',mod_count:3,enabled_count:2});return {profile:{id:'imported'},message:'Imported'};}
      if(action==='profile-export')return {message:'Exported metadata only'};
      if(action==='profile-delete'){setup.profiles=setup.profiles.filter(item=>item.id!==body.id);return {message:'Deleted'};}
      if(action==='profile-apply')return {message:'States applied',warnings:['Check <Workshop> approval'],pending:['Pending <mod>']};
      if(action==='profile-download'){jobs=[{id:'profile-job',profile_id:body.id,kind:'install',name:'Needed Nexus',version:'1.0',state:downloadState,cancellable:downloadState!=='completed'}];return jobs[0];}
      if(action==='downloads')return {download_panel:setup.download_panel,downloads:jobs};
      if(action==='steam-workshop')return {message:'Subscribe, then refresh status'};
      throw Error('Unexpected profile action: '+action);
    }});
  const execute=script=>vm.runInContext(script,profile);
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('function modDuplicates'),source.indexOf('function updateReason')));
  execute(source.slice(source.indexOf('function updateReason'),source.indexOf('let deckProtonState')));
  execute(source.slice(source.indexOf("$('profile-create').onclick"),source.indexOf("$('setup').onclick")));
  execute(source.slice(source.indexOf("document.addEventListener('click'"),source.indexOf("document.addEventListener('change'")));
  node('import-zip').onclick=()=>{zipOpened++;};
  execute('renderDuplicates();renderMods();renderProfiles()');
  assert.equal(node('duplicates-banner').hidden,false);
  assert.match(node('duplicates-banner').innerHTML,/Multiple copies enabled/);
  assert.match(node('duplicates-banner').innerHTML,/Quick &lt;Stash&gt;/);
  assert.match(node('duplicates-banner').innerHTML,/Shared &lt;GUID&gt;/);
  assert.match(node('duplicates-banner').innerHTML,/data-details="steam-copy"/);
  assert.match(node('mod-list').innerHTML,/Duplicate copy · multiple enabled/);
  execute('details("nexus-copy")');assert.match(review[1],/Multiple copies are enabled/);
  setup.mods[0].enabled=false;setup.duplicates[0].enabled_count=1;setup.duplicates[0].disabled_count=1;
  execute('renderDuplicates()');assert.match(node('duplicates-banner').innerHTML,/disabled duplicate/);
  assert.doesNotMatch(node('duplicates-banner').innerHTML,/Multiple copies enabled/);
  assert.match(node('profile-select').innerHTML,/Cozy &lt;set&gt;.*last applied/);
  await execute('compareProfile()');
  assert.equal(node('profile-apply').disabled,true,'required missing mods lock the single Apply card');
  assert.match(node('profile-install').innerHTML,/3 missing · review highlighted mods/);
  assert.match(node('profile-entries').innerHTML,/Needed &lt;Nexus&gt;/);
  assert.match(node('profile-entries').innerHTML,/data-profile-download="nexus:10"[^>]*disabled/);
  assert.match(node('profile-entries').innerHTML,/data-profile-workshop="222"/);
  assert.equal((node('profile-entries').innerHTML.match(/data-profile-zip/g) || []).length,1,'disabled missing Manual mods need no install');
  assert.match(node('profile-entries').innerHTML,/profile file #31 · installed file #32/);
  setup.nexus_connected=true;execute('renderProfiles()');
  assert.doesNotMatch(node('profile-entries').innerHTML,/data-profile-download="nexus:10"[^>]*disabled/);
  const target=dataset=>({dataset,hasAttribute:name=>name==='data-profile-zip' && !!dataset.profileZip});
  click({target:{closest:()=>target({profileWorkshop:'222'})}});await Promise.resolve();
  assert.deepEqual(calls.at(-1),['steam-workshop',{workshop_id:'222'}]);
  click({target:{closest:()=>target({profileZip:'yes'})}});assert.equal(zipOpened,1,'Manual action reuses the ZIP import flow');
  await node('profile-refresh').onclick();assert.equal(calls.at(-1)[0],'profile-compare');
  await execute('reviewProfile()');
  assert.equal(review[2].at(-1)[3],'Resolve the missing or ambiguous enabled mods before applying.');
  assert.match(review[1],/Install &lt;missing&gt;/);
  node('profile-accept-versions').checked=true;node('profile-accept-versions').events.get('change')();
  assert.equal(node('dialog-actions').lastElementChild.disabled,true,'version acceptance cannot bypass missing enabled mods');
  result={...result,blockers:[]};await execute('reviewProfile()');
  assert.match(review[1],/Use the installed releases where versions or file variants differ/);
  assert.match(review[1],/profile file #31 · installed file #32/);
  assert.match(review[1],/Different &lt;variant&gt;/);
  assert.equal(node('dialog-actions').lastElementChild.disabled,true);
  node('profile-accept-versions').checked=true;node('profile-accept-versions').events.get('change')();
  assert.equal(node('dialog-actions').lastElementChild.disabled,false);
  await review[2].at(-1)[1]();
  assert.deepEqual(calls.find(([action])=>action==='profile-apply')[1],{id:'p1',token:'fresh-review',accept_versions:true});
  assert.match(review[1],/Check &lt;Workshop&gt; approval/);
  assert.match(review[1],/Pending &lt;mod&gt;/);
  const beforeNew=calls.length;await node('profile-create').onclick();
  assert.equal(calls.length,beforeNew,'New profile opens a local name modal before saving');
  assert.match(review[1],/id="profile-name"/);assert.equal(node('profile-name').focused,true);
  review[2][0][1]();assert.equal(calls.length,beforeNew,'cancelling the name modal leaves profiles unchanged');
  await node('profile-create').onclick();node('profile-name').value='';await review[2].at(-1)[1]();
  assert.equal(calls.length,beforeNew,'an empty name never sends a profile-save request');
  assert.match(messages.at(-1)[0],/Enter a name/);
  node('profile-name').value='New <set>';saveError=Error('Fixture save failure');
  const beforeFailedClose=node('dialog').closeCount;await assert.rejects(review[2].at(-1)[1](),/Fixture save failure/);
  assert.equal(node('dialog').closeCount,beforeFailedClose,'a failed save keeps the modal open for retry');
  assert.equal(node('profile-name').value,'New <set>','a failed save preserves the entered name');
  assert.equal(setup.profiles.length,1,'a failed save cannot create a profile');saveError=undefined;
  await review[2].at(-1)[1]();
  assert.deepEqual(calls.find(([action])=>action==='profile-save')[1],{name:'New <set>'});
  assert.equal(node('profile-name').value,'','successful modal save clears the transient name field');
  assert.equal(node('profile-select').value,'new-profile','successful save selects and checks the new profile');
  assert.equal(calls.at(-1)[0],'profile-compare');
  await node('profile-replace').onclick();
  assert.deepEqual(calls.filter(([action])=>action==='profile-save').at(-1)[1],{id:'new-profile',name:'New <set>'});
  await node('profile-export').onclick();assert.deepEqual(calls.find(([action])=>action==='profile-export')[1],{id:'new-profile'});
  const beforeImport=calls.length;await node('profile-import').onclick();assert.equal(calls.length,beforeImport+1,'cancelled import has no follow-up mutation');
  imported=true;await node('profile-import').onclick();assert.equal(node('profile-select').value,'imported');
  node('profile-delete').onclick();assert.match(review[1],/Shared &lt;profile&gt;/);
  assert.equal(calls.at(-1)[0],'profile-compare','Delete waits for modal confirmation');
  await review[2].at(-1)[1]();assert.equal(calls.at(-1)[0],'profile-delete');
  execute('renderProfiles();compareProfile()');await Promise.resolve();
  await execute('downloadProfileMod("nexus:10")');
  assert.equal(calls.find(([action])=>action==='profile-download')[1].key,'nexus:10');
  assert.equal(node('profile-apply').disabled,true,'pending download blocks profile application');
  jobs=[{...jobs[0],state:'awaiting_review'}];await execute('pollDownloads()');
  assert.equal(node('profile-apply').disabled,true,'duplicate review also blocks profile application');
  assert.equal(node('nexus-browser-open').disabled,true,'duplicate review cannot start a second install session');
  jobs=[{...jobs[0],state:'completed',cancellable:false}];await execute('pollDownloads()');
  assert.equal(calls.at(-1)[0],'profile-compare','native download completion rechecks the selected profile');
  downloadState='completed';const beforeDownload=calls.length;await execute('downloadProfileMod("nexus:10")');
  assert.equal(calls.slice(beforeDownload).at(-1)[0],'profile-compare','direct Premium completion also rechecks the selected profile');
  assert.equal(calls.filter(([action])=>action==='profile-apply').length,1,'downloads, imports and comparison never apply a profile automatically');
  assert.match(html,/data-page="profiles"/);
  assert.match(html,/do not include mod files, saves, account keys, passwords, or configuration files/);
}

async function zipImportChecks(){
  const inputs=new Map(),calls=[],messages=[],focused=[];let review,refreshed=0,profileRefreshes=0;
  const makeNode=()=>({checked:false,value:'',textContent:'',events:new Map(),addEventListener(event,handler){this.events.set(event,handler);},scrollIntoView(options){this.scrolled=options;},close(){}});
  const node=id=>{if(!inputs.has(id))inputs.set(id,makeNode());return inputs.get(id);};
  const normal={token:'zip-review',name:'Local <mod>',status:'ready',files:1,validation:{message:'BepInEx DLL detected'}};
  const other={...normal,token:'second-zip',name:'Second <mod>'};
  const ui=vm.createContext({$:node,state:{downloads:[]},fixture:[normal],plan:{packages:[normal],conflicts:[],digest:'plan-1'},toast:(...args)=>messages.push(args),focusAfterWork:element=>focused.push(element),work:async(_,task)=>task(),refresh:async()=>{refreshed++;},refreshSelectedProfile:async()=>{profileRefreshes++;},
    modal(...args){review=args;for(const match of args[1].matchAll(/id="([^"]+)"/g))inputs.set(match[1],makeNode());node('dialog-actions').lastElementChild={disabled:!!args[2].at(-1)?.[3]};},
    api:async(action,body)=>{
      calls.push([action,JSON.parse(JSON.stringify(body))]);
      if(action==='stage')return normal;
      if(action==='install-preview'){if(ui.previewFailure)throw ui.previewFailure;return ui.plan;}
      if(action==='install-batch'){
        if(ui.installFailure)throw ui.installFailure;
        return ui.batchResult || {results:ui.plan.packages.map(p=>({...p,status:p.status==='ready'?'installed':p.status}))};
      }
      throw Error('Unexpected ZIP request: '+action);
    }});
  const execute=script=>vm.runInContext(script,ui);
  const installCalls=()=>calls.filter(([action])=>action==='install-batch');
  const changeChoice=(index,value)=>node('import-choice-'+index).events.get('change')({target:{value}});
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('async function stagePath'),source.indexOf('function compareView')));
  await execute('reviewPackages(fixture,[])');assert.deepEqual(calls.at(-1),['install-preview',{tokens:['zip-review']}]);
  assert.equal(installCalls().length,0,'review only reads the current files');
  assert.match(review[1],/Local &lt;mod&gt;/,'mod names are escaped in the review');
  review[2][0][1]();assert.equal(installCalls().length,0,'cancelling a review leaves files untouched');
  await execute('reviewPackages(fixture,[])');await review[2].at(-1)[1]();
  assert.deepEqual(installCalls().at(-1),['install-batch',{tokens:['zip-review'],choices:{},digest:'plan-1',acknowledge_duplicates:false}]);
  assert.equal(refreshed,1);assert.equal(profileRefreshes,1,'confirmed installation rechecks profile progress');
  assert.equal(review[0],'ZIP installation complete');assert.match(review[1],/1 mod installed/);
  assert.equal(focused.at(-1),node('dialog-title'),'completion returns focus to its heading');
  ui.fixture=[{...normal,status:'already_installed'}];ui.plan={packages:ui.fixture,conflicts:[],digest:'skipped'};await execute('reviewPackages(fixture,[])');
  assert.match(review[1],/Already installed · skipped/);assert.ok(!review[2].some(([label])=>label.startsWith('Install')),'an identical archive cannot be installed again from review');
  ui.fixture=[normal,{...other,status:'already_selected'}];ui.plan={packages:ui.fixture,conflicts:[],digest:'selection'};await execute('reviewPackages(fixture,[])');
  assert.match(review[1],/Identical ZIP in this selection · skipped/,'different archives containing the same payload are counted separately but installed once');
  assert.equal(review[2].at(-1)[0],'Install 1 mod');
  ui.fixture=[{...normal,requires_duplicate_ack:true,duplicates:[{name:'Steam <copy>',source:'Steam',enabled:true}]}];ui.plan={packages:ui.fixture,conflicts:[],digest:'duplicates'};await execute('reviewPackages(fixture,[])');
  assert.match(review[1],/Steam &lt;copy&gt;/);assert.ok(review[2].at(-1)[3],'duplicate install requires a separate choice');
  const beforeDuplicate=installCalls().length;
  await assert.rejects(review[2].at(-1)[1](),/Confirm the duplicate copy/);assert.equal(installCalls().length,beforeDuplicate);
  node('import-duplicate-accept').checked=true;node('import-duplicate-accept').events.get('change')({target:{checked:true}});
  assert.equal(node('dialog-actions').lastElementChild.disabled,false);await review[2].at(-1)[1]();
  assert.deepEqual(installCalls().at(-1),['install-batch',{tokens:['zip-review'],choices:{},digest:'duplicates',acknowledge_duplicates:true}]);

  const conflict={path:'BepInEx/plugins/shared.dll',requires_choice:true,identical:false,
    owners:[{id:'active',name:'Installed <active>',enabled:true,kind:'package',hash:'old'},{id:'disabled',name:'Installed <disabled>',enabled:false,kind:'package',hash:'disabled'}],
    incoming:[{token:normal.token,name:normal.name,hash:'new'},{token:other.token,name:other.name,hash:'different'}],
    choices:[{value:'keep_existing',label:'Keep existing file'},{value:normal.token,label:'Use '+normal.name},{value:other.token,label:'Use '+other.name}]};
  const secondConflict={...conflict,path:'BepInEx/config/shared.cfg'};
  ui.fixture=[normal,other];ui.plan={packages:ui.fixture,conflicts:[conflict,secondConflict],digest:'conflict-plan'};
  await execute('reviewPackages(fixture,[])');
  assert.match(review[1],/Installed &lt;active&gt;<\/strong><span>enabled/);
  assert.match(review[1],/Installed &lt;disabled&gt;<\/strong><span>disabled/);
  assert.match(review[1],/Use Second &lt;mod&gt;/,'every incoming mod has a named choice');
  assert.match(review[1],/Keeping a disabled copy does not enable it/);
  assert.equal(node('dialog-actions').lastElementChild.disabled,true);
  const beforeConflict=installCalls().length;await assert.rejects(review[2].at(-1)[1](),/Choose which file/);assert.equal(installCalls().length,beforeConflict);
  changeChoice(0,'keep_existing');assert.equal(node('dialog-actions').lastElementChild.disabled,true,'every differing path requires a choice');
  changeChoice(1,'not-a-reviewed-mod');assert.equal(node('dialog-actions').lastElementChild.disabled,true,'unreviewed choice values cannot unlock installation');
  changeChoice(1,other.token);assert.equal(node('dialog-actions').lastElementChild.disabled,false);
  ui.batchResult={results:[{...normal,status:'kept_existing',reason:'The existing DLL was kept.'},{...other,status:'installed',excluded_paths:['BepInEx/plugins/shared.dll']}]};
  await review[2].at(-1)[1]();
  assert.deepEqual(installCalls().at(-1),['install-batch',{tokens:['zip-review','second-zip'],choices:{'BepInEx/plugins/shared.dll':'keep_existing','BepInEx/config/shared.cfg':'second-zip'},digest:'conflict-plan',acknowledge_duplicates:false}]);
  assert.match(review[1],/1 mod installed · 1 skipped/);assert.match(review[1],/The existing DLL was kept/);assert.match(review[1],/shared files kept from your selection/);
  ui.batchResult=null;

  ui.fixture=[{...normal,requires_duplicate_ack:true,duplicates:[{name:'Steam copy',source:'Steam',enabled:false}]}];ui.plan={packages:ui.fixture,conflicts:[conflict],digest:'both-gates'};
  await execute('reviewPackages(fixture,[])');changeChoice(0,normal.token);
  assert.equal(node('dialog-actions').lastElementChild.disabled,true,'file selection never bypasses the Steam duplicate acknowledgement');
  node('import-duplicate-accept').checked=true;node('import-duplicate-accept').events.get('change')({target:node('import-duplicate-accept')});
  assert.equal(node('dialog-actions').lastElementChild.disabled,false);
  changeChoice(0,'');assert.equal(node('dialog-actions').lastElementChild.disabled,true,'acknowledgement never bypasses a missing file choice');

  ui.fixture=[normal,other];ui.plan={packages:ui.fixture,conflicts:[{...conflict,requires_choice:false,identical:true}],digest:'same-bytes'};
  await execute('reviewPackages(fixture,[])');assert.equal(node('dialog-actions').lastElementChild.disabled,false,'identical shared bytes need no decision');
  assert.match(review[1],/identical bytes · no choice needed/);assert.doesNotMatch(review[1],/import-choice-|class="import-conflict"/);
  assert.match(review[1],/can be shared\. Removing or disabling one mod keeps the file available to the other/,'identical files keep shared ownership rather than excluding a mod’s assets');
  assert.doesNotMatch(review[1].match(/<details class="import-shared">[\s\S]*?<\/details>/)[0],/class="warning"|class="danger"/,'same-byte information is not a danger or overwrite warning');
  await review[2].at(-1)[1]();assert.deepEqual(installCalls().at(-1)[1].choices,{},'same-byte paths use the backend automatic resolution');

  ui.plan={packages:ui.fixture,conflicts:[conflict],digest:'old-plan'};await execute('reviewPackages(fixture,["Broken <archive>: no DLL"])');changeChoice(0,normal.token);
  ui.installFailure=Error('The installed files changed. No files changed.');
  const beforeFailure=messages.length;await review[2].at(-1)[1]();
  assert.equal(review[0],'Review ZIP installation');assert.equal(messages.length,beforeFailure,'a rejected batch never reports success');
  assert.match(node('import-review-error').textContent,/No files changed.*Review the ZIPs again/);assert.equal(node('import-review-error').hidden,false);
  assert.equal(node('import-review-error').scrolled.block,'nearest','a rejected batch brings its recovery message into view');assert.equal(focused.at(-1),node('import-review-error'));
  assert.equal(node('dialog-actions').lastElementChild.textContent,'Review again');assert.equal(node('dialog-actions').lastElementChild.disabled,false);
  const beforeReviewAgain=installCalls().length;ui.installFailure=null;ui.plan={...ui.plan,digest:'fresh-plan'};await review[2].at(-1)[1]();
  assert.equal(installCalls().length,beforeReviewAgain,'retry first gets a fresh preview instead of replaying a stale digest');
  assert.equal(node('dialog-actions').lastElementChild.disabled,true,'fresh review requires a new explicit file choice');
  assert.match(review[1],/Broken &lt;archive&gt;/,'preparation errors remain visible after re-review');changeChoice(0,other.token);await review[2].at(-1)[1]();
  assert.equal(installCalls().at(-1)[1].digest,'fresh-plan');assert.match(review[1],/These ZIPs were not installed/);

  ui.previewFailure=Error('The staged ZIP is missing.');await execute('reviewPackages(fixture,[])');assert.equal(review[0],'ZIP review needs attention');
  assert.match(review[1],/ZIPs have not been installed/);ui.previewFailure=null;await review[2].at(-1)[1]();assert.equal(review[0],'Review ZIP installation');
  ui.fixture=[normal];ui.plan={packages:ui.fixture,conflicts:[],digest:'staged'};
  const stages=calls.filter(([action])=>action==='stage').length;
  await execute('stagePaths(["C:/fixture/mod.zip","C:/fixture/mod.zip","C:/fixture/notes.txt"])');
  assert.equal(calls.filter(([action])=>action==='stage').length,stages+1,'repeated drop paths are staged once');
  assert.match(review[1],/Only ZIP files can be imported/);
  await assert.rejects(execute('stagePaths([])'),/Choose 1–100/);
  const previews=calls.filter(([action])=>action==='install-preview').length;
  await execute('stagePaths(["C:/fixture/notes.txt"])');assert.equal(calls.filter(([action])=>action==='install-preview').length,previews,'invalid files never generate a batch or reach installation');
  assert.ok(!review[2].some(([label])=>label.startsWith('Install')));
}

async function workshopSetupChecks() {
  const inputs=new Map(),calls=[],messages=[],rescans=[];let copied=0,profileRefreshes=0,failure;
  const node=id=>{if(!inputs.has(id))inputs.set(id,{textContent:'',innerHTML:'',classList:{toggle(){}}});return inputs.get(id);};
  const setup={game_found:true,loader_installed:true,workshop_loader:{kind:'none'},workshop_setup:{state:'ready',installed:false,can_install:true,message:'Download <Workshop Loader> from GitHub.',source:'GitHub',workshop_id:'3807346541'}};
  const ui=vm.createContext({$:node,state:setup,toast:(...args)=>messages.push(args),
    work:async(_,task)=>{try{return await task();}catch(error){messages.push([error.message,true]);return null;}},
    refresh:async(...args)=>{rescans.push(args);vm.runInContext('renderWorkshopSetup()',ui);},
    refreshSelectedProfile:async()=>{profileRefreshes++;},
    api:async(action,body)=>{calls.push([action,JSON.parse(JSON.stringify(body))]);
      if(action==='workshop-loader-setup'){
        if(failure)throw failure;
        if(setup.workshop_setup.state==='ready'){
          copied++;setup.workshop_loader.kind='workshop';setup.workshop_setup={...setup.workshop_setup,state:'installed',installed:true,can_install:false,message:'Workshop loader installed. Launch the game to confirm mods load.'};
        }
        return setup.workshop_setup;
      }
      throw Error('Unexpected setup action: '+action);
    }});
  const execute=script=>vm.runInContext(script,ui);
  execute(source.slice(source.indexOf('function renderWorkshopSetup'),source.indexOf('function modDuplicates')));
  execute(source.slice(source.indexOf("$('workshop-loader-install').onclick"),source.indexOf("$('setup-zip').onclick")));
  execute('renderWorkshopSetup()');
  assert.equal(calls.length,0,'startup/status rendering never installs a loader');
  assert.equal(node('workshop-setup-banner').hidden,false);
  assert.equal(node('workshop-loader-install').disabled,false,'missing Workshop Loader can download from GitHub without a Steam cache');
  assert.equal(node('workshop-loader-status').textContent,'Download <Workshop Loader> from GitHub.');
  assert.equal(node('workshop-loader-status').innerHTML,'','backend messages only use textContent');
  setup.settings={workshop_enabled:false};execute('renderWorkshopSetup()');
  assert.equal(node('workshop-setup-banner').hidden,true,'skipping Workshop in Quick Setup avoids a persistent setup prompt');
  assert.equal(calls.length,0,'skipping Workshop does not install or remove anything');
  setup.settings.workshop_enabled=true;execute('renderWorkshopSetup()');
  assert.equal(node('workshop-setup-banner').hidden,false);
  await node('workshop-loader-install').onclick();
  assert.deepEqual(calls.at(-1),['workshop-loader-setup',{}]);
  assert.deepEqual(rescans.at(-1),[false,true]);
  assert.equal(profileRefreshes,1,'setup refresh also recomputes the selected profile');
  assert.equal(copied,1);
  assert.equal(node('workshop-setup-banner').hidden,true);
  assert.equal(node('workshop-loader-install').hidden,true);
  assert.match(node('workshop-loader-status').textContent,/Launch the game to confirm/);
  await execute('installWorkshopLoader()');assert.equal(copied,1,'an installed current loader is preserved on another refresh');
  const readOnlyCalls=calls.length;
  setup.loader_installed=false;setup.workshop_setup={state:'missing_bepinex',installed:false,can_install:false,message:'Install BepInEx first.'};execute('renderWorkshopSetup()');
  assert.equal(node('workshop-setup-banner').hidden,true,'foundation banner covers the first prerequisite');
  assert.equal(node('workshop-loader-install').disabled,true);
  setup.game_found=false;execute('renderWorkshopSetup()');assert.equal(node('workshop-loader-install').disabled,true);
  setup.game_found=setup.loader_installed=true;
  setup.workshop_loader.kind='none';setup.workshop_setup={state:'disabled',installed:false,can_install:false,message:'Enable your existing loader in My mods.'};execute('renderWorkshopSetup()');
  assert.equal(node('workshop-loader-install').hidden,true);
  assert.equal(node('workshop-loader-mods').hidden,false);
  assert.match(node('workshop-loader-mods').textContent,/Enable the existing loader/);
  setup.workshop_setup={state:'blocked',installed:false,can_install:false,message:'Resolve the managed loader ownership in My mods.'};execute('renderWorkshopSetup()');
  assert.equal(node('workshop-loader-mods').hidden,false,'managed ownership can need review when no active loader is detected');
  for(const kind of ['legacy','conflict']){
    setup.workshop_loader.kind=kind;setup.workshop_setup={state:'blocked',installed:false,can_install:false,message:'Resolve the existing <loader> first.'};execute('renderWorkshopSetup()');
    assert.equal(node('workshop-loader-install').hidden,true);
    assert.match(node('workshop-loader-status').textContent,/Resolve the existing/);
  }
  assert.equal(calls.length,readOnlyCalls,'disabled/legacy/conflict status never enables or overwrites a loader');
  setup.workshop_loader.kind='none';setup.workshop_setup={state:'ready',installed:false,can_install:true,message:'Ready to install.'};failure=Error('GitHub download failed. Try again.');
  const beforeFailure=profileRefreshes;await node('workshop-loader-install').onclick();
  assert.equal(profileRefreshes,beforeFailure+1);
  assert.equal(node('workshop-loader-install').disabled,false,'a failed copy remains retryable after refreshed status');
  assert.equal(node('workshop-setup-banner').hidden,false);
  assert.deepEqual(messages.at(-1),['GitHub download failed. Try again.',true]);
  failure=null;await node('workshop-loader-install').onclick();assert.equal(copied,2,'retry installs only after the failed download is resolved');
  assert.ok(calls.every(([action])=>action==='workshop-loader-setup'),'loader setup does not open Steam, connect Nexus, or request subscriptions');
  assert.match(html,/STEP 1 · MODDING FOUNDATION/);
  assert.match(html,/STEP 2 · STEAM WORKSHOP/);
  assert.match(html,/id="workshop-loader-install"[^>]*>.*Install Workshop Loader/);
  assert.doesNotMatch(html,/id="workshop-subscribe"/);
  assert.match(html,/id="foundation-note"[^>]*>Pinned GitHub downloads\. No account or API key needed/);
  assert.doesNotMatch(source,/Install your chosen loader separately/);
}

async function foundationSetupChecks(){
  const inputs=new Map(),calls=[],busy=[];let review,refreshed=0,result={files:12,message:'BepInEx installed from GitHub. Existing configs kept.'},failure;
  const node=id=>{if(!inputs.has(id))inputs.set(id,{value:'',close(){}});return inputs.get(id);};
  const ui=vm.createContext({$:node,modal:(...args)=>{review=args;},toast(){},refresh:async()=>{refreshed++;},work:async(message,task)=>{busy.push(message);return task();},
    api:async(action,body)=>{calls.push([action,JSON.parse(JSON.stringify(body))]);if(action!=='setup')throw Error('Unexpected setup action: '+action);if(failure)throw failure;return result;}});
  const execute=script=>vm.runInContext(script,ui);
  execute(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')));
  execute(source.slice(source.indexOf('function setupArchive('),source.indexOf('function importOwners(')));
  await execute('runSetup()');assert.deepEqual(calls.at(-1),['setup',{}],'normal foundation setup needs no account key, archive or Nexus file ID');assert.match(busy.at(-1),/GitHub/);
  assert.equal(review[0],'BepInEx setup ready');assert.match(review[1],/12 files installed/);assert.doesNotMatch(review[1],/undefined|Original files backed up to/);
  result={files:0,message:'Detected BepInEx kept unchanged.'};await execute('runSetup()');
  assert.match(review[1],/kept unchanged/);assert.doesNotMatch(review[1],/0 files installed|<pre>/,'preserved setup does not invent installation counts or a backup path');
  execute('setupArchive()');assert.match(review[1],/explicit alternative can replace existing foundation files with a backup/);
  const beforeConfirm=calls.length;node('setup-archive').value='C:/fixtures/Nexus-BepInEx.zip';
  result={files:14,message:'Nexus bundle installed.',backup:'C:/fixture/backups/replacement'};await review[2].find(([label])=>label==='Install replacement ZIP')[1]();
  assert.equal(calls.length,beforeConfirm+1);assert.deepEqual(calls.at(-1),['setup',{archive:'C:/fixtures/Nexus-BepInEx.zip'}]);assert.match(review[1],/Recovery backup/);
  assert.equal(refreshed,3);
  failure=Error('GitHub download unavailable. Try again.');await assert.rejects(execute('runSetup()'),/download unavailable/);assert.equal(refreshed,3,'failed download does not announce a completed setup');
}

async function uninstallChecks() {
  const mods=[
    {id:'steam',name:'Subscribed',workshop_id:'123',can_uninstall:true},
    {id:'foundation',name:'BepInEx',can_uninstall:false,uninstall_reason:'Keep the <loader> installed.'},
    {id:'nexus',name:'Nexus release',nexus_mod_id:12,can_uninstall:true},
    {id:'manual',name:'Manual release',can_uninstall:true},
  ];
  const calls=[],messages=[],classes=new Set();
  let review,closed=0,refreshed=0,failure=null;
  const preview={token:'review-token',id:'manual',name:'Unsafe <name>',files:['BepInEx/plugins/<mod>.dll'],
    restored:['BepInEx/plugins/old & shared.dll'],preserved:['BepInEx/config/<settings>.cfg'],warnings:['Keep <other> mods.']};
  preview.removed=[...preview.files];
  preview.files.push(...preview.restored);
  $('dialog').close=()=>{closed++;};
  $('busy').querySelector=()=>({textContent:''});
  const removal=vm.createContext({$,working:false,state:{mods},modal(...args){review=args;},
    document:{querySelectorAll:()=>[],body:{classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}}},
    toast:(message,error)=>messages.push([message,error]),refresh:async()=>{refreshed++;},
    api:async(action,body)=>{calls.push([action,JSON.parse(JSON.stringify(body))]);if(failure)throw failure;
      if(action==='uninstall-preview')return preview;
      if(action==='uninstall')return {message:'Mod uninstalled.',backup:'C:\\GK2MT\\backups\\<mod>'};
      throw Error('Unexpected uninstall request.');}});
  vm.runInContext(source.slice(source.indexOf('const escapeHTML'),source.indexOf('function toast')),removal);
  vm.runInContext(source.slice(source.indexOf('function modDuplicates'),source.indexOf('function renderDuplicates')),removal);
  vm.runInContext(source.slice(source.indexOf('async function work('),source.indexOf('function page(')),removal);
  vm.runInContext(source.slice(source.indexOf('function details('),source.indexOf('function renderRules(')),removal);
  const check=script=>vm.runInContext(script,removal);
  check('details("steam")');
  assert.equal(review[2].some(([label])=>label==='Uninstall mod'),false,'Steam removal stays with subscriptions');
  assert.match(review[1],/View Workshop item/);
  check('details("foundation")');
  let action=review[2].find(([label])=>label==='Uninstall mod');
  assert.equal(action[2],'danger');
  assert.equal(action[3],mods[1].uninstall_reason,'blocked removal explains why it is disabled');
  assert.match(review[1],/Keep the &lt;loader&gt; installed\./);
  await action[1]();
  assert.equal(calls.length,0,'disabled actions never request a preview');
  for(const id of ['nexus','manual']) {
    check(`details(${JSON.stringify(id)})`);
    action=review[2].find(([label])=>label==='Uninstall mod');
    assert.equal(action[3],'');
    await action[1]();
    assert.deepEqual(calls.at(-1),['uninstall-preview',{id}]);
    assert.equal(review[0],'Review mod uninstall');
    assert.match(review[1],/Unsafe &lt;name&gt;|Files to remove|Files to restore|Files to preserve/);
    assert.match(review[1],/plugins\/&lt;mod&gt;\.dll/);
    assert.match(review[1],/old &amp; shared\.dll/);
    assert.equal((review[1].match(/old &amp; shared\.dll/g)||[]).length,1,'restored paths are not also listed as removed');
    assert.match(review[1],/&lt;settings&gt;\.cfg/);
    assert.match(review[1],/Keep &lt;other&gt; mods\./);
    assert.doesNotMatch(review[1],/<name>|<mod>|<settings>|<other>/);
    assert.match(review[1],/backs up files before changing them/);
    const beforeCancel=calls.length;
    review[2].find(([label])=>label==='Cancel')[1]();
    assert.equal(calls.length,beforeCancel,'cancel never sends removal');
  }
  await check('uninstallPreview("manual")');
  await review[2].find(([label])=>label==='Uninstall mod')[1]();
  assert.deepEqual(calls.at(-1),['uninstall',{token:'review-token'}],'confirmed removal sends only the reviewed token');
  assert.equal(refreshed,1,'successful uninstall refreshes inventory');
  assert.deepEqual(messages.at(-1),['Mod uninstalled.',undefined]);
  assert.equal(review[0],'Mod uninstalled');
  assert.match(review[1],/C:\\GK2MT\\backups\\&lt;mod&gt;/,'success retains the full backup path');
  await check('uninstallPreview("manual")');
  const beforeFailure=closed;
  failure=Error('Files changed. Review uninstall again.');
  await review[2].find(([label])=>label==='Uninstall mod')[1]();
  assert.deepEqual(messages.at(-1),[failure.message,true],'stale preview errors are shown to the user');
  assert.equal(closed,beforeFailure+1,'failed confirmation closes the expired preview');
  assert.equal(refreshed,1);
  assert.equal(classes.has('working'),false);
  assert.equal($('busy').hidden,true,'the interface is usable after an uninstall failure');
}

async function main() {
  modSourceChecks();
  libraryConflictChecks();
  await workshopRefreshChecks();
  await desktopBridgeChecks();
  navigationChecks();
  await deckRememberChecks();
  await nexusBrowserChecks();
  await profileChecks();
  await zipImportChecks();
  await workshopSetupChecks();
  await foundationSetupChecks();
  await uninstallChecks();
  run('renderUpdates()');
  assert.equal(state.downloads.length,0);assert.equal($('download-list').innerHTML,'');
  assert.equal($('update-log').hidden,true,'an empty update log stays hidden before any result or download');
  assert.equal($('updates-connections').open,false,'connected accounts collapse their setup panel');
  $('updates-connections').open=true;run('renderUpdates()');
  assert.equal($('updates-connections').open,true,'a user-expanded connection panel stays open');
  assert.equal($('nexus-remember-option').hidden,true,'empty optional credential fields hide the remember choice');
  $('nexus-key').value='dummy';run('renderUpdates()');assert.equal($('nexus-remember-option').hidden,false);$('nexus-key').value='';
  const updatesHeader=html.match(/<section id="page-updates"[\s\S]*?<details id="updates-connections"/)[0];
  assert.ok(updatesHeader.includes('id="check-updates"') && updatesHeader.includes('id="download-updates"'),'check and update-all actions precede the collapsible account panel together');
  assert.match($('updates-list').innerHTML, /data-download-panel="mod1"/);
  assert.match($('updates-list').innerHTML, /data-update-archive="mod1"/);
  assert.doesNotMatch($('updates-list').innerHTML, /<button[^>]*disabled/);
  assert.equal($('download-updates').disabled, true);
  assert.match($('updates-help').textContent, /Free/);
  state.updates._stale=true;
  run('renderUpdates()');
  assert.match($('updates-help').textContent, /Installed mods changed.*check for updates again/);
  assert.match($('updates-list').innerHTML, /data-download-panel="mod1"/);
  delete state.updates._stale;
  assert.match($('updates-list').innerHTML, /Latest changes/);
  assert.match($('updates-list').innerHTML, /No changelog published for this version\./);
  assert.doesNotMatch($('updates-list').innerHTML, /Choose the Free download|Nexus Premium is required/);
  update.changelog = 'Fixed <script>alert("unsafe")</script> & input.\nAdded keyboard support.';
  update.changelog_version = '<2.0>';
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /Latest changes · &lt;2\.0&gt;/);
  assert.match($('updates-list').innerHTML, /Fixed &lt;script&gt;alert\(&quot;unsafe&quot;\)&lt;\/script&gt; &amp; input\.\nAdded keyboard support\./);
  assert.doesNotMatch($('updates-list').innerHTML, /<script>|<2\.0>/);
  assert.match($('updates-list').innerHTML, /<details class="update-changelog"><summary>Latest changes/,'release notes use a keyboard-accessible native disclosure');
  assert.doesNotMatch($('updates-list').innerHTML, /<details class="update-changelog"[^>]*\bopen\b/,'release notes start compact');
  update.changelog = '';
  update.changelog_error = 'Service error <private details>';
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /Changelog unavailable\./);
  assert.doesNotMatch($('updates-list').innerHTML, /No changelog published|private details/);
  delete update.changelog_error;
  update.changelog = 'Fixed keyboard input.';
  update.changelog_version = '2.0';
  update.manual_installable = false;
  for(const reason of ['Steam manages Workshop updates.', 'Choose the matching file variant', 'Unknown version; automatic update skipped']) {
    update.blocked_reason = reason;
    run('renderUpdates()');
    assert.ok($('updates-list').innerHTML.includes(reason), 'keep the reason a release cannot be installed');
    assert.match($('updates-list').innerHTML, /Fixed keyboard input\./);
    assert.match($('updates-list').innerHTML, /Review details/);
  }
  update.manual_installable = true;
  update.blocked_reason = 'Nexus Premium is required for automatic downloads';
  const resolvedUpdate = {...update};
  Object.assign(update, {file_id: null, version: '', known_version: false, manual_installable: false,
    changelog: '', changelog_version: '', blocked_reason: 'Choose the matching file variant', choices: [
      {file_id: 35, name: 'Standard <build>', version: '2.0'},
      {file_id: 36, name: 'Alternate & UI', version: '<2.1>'} ]});
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /1\.0 → Choose file/);
  assert.match($('updates-list').innerHTML, /Standard &lt;build&gt; · 2\.0/);
  assert.match($('updates-list').innerHTML, /Alternate &amp; UI · &lt;2\.1&gt;/);
  assert.match($('updates-list').innerHTML, /Release notes appear once the matching file is identified\./);
  assert.match($('updates-list').innerHTML, /Review details/);
  assert.doesNotMatch($('updates-list').innerHTML, /→ Unknown|No changelog published|<build>|<2\.1>|data-update(?:-archive)?=|data-download-panel=/);
  assert.equal($('download-updates').disabled, true);
  update.choices.pop();
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /1\.0 → Choose file/);
  assert.doesNotMatch($('updates-list').innerHTML, /Multiple files|Alternate/);
  delete update.choices;
  update.blocked_reason = 'Unknown version; automatic update skipped';
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /1\.0 → Unknown/);
  assert.doesNotMatch($('updates-list').innerHTML, /Choose file|matching file is identified/);
  Object.assign(update, resolvedUpdate);
  run('updateArchive("mod1")');
  assert.match(dialog[1], /mods\/12\?tab=files&file_id=34/);
  $('update-archive-path').value = 'C:\\Downloads\\update.zip';
  await dialog[2].find(([label]) => label === 'Review update')[1]();
  assert.deepEqual(requests[0], ['update-archive-preview', {id: 'mod1', path: 'C:\\Downloads\\update.zip'}]);
  assert.equal(dialog[0], 'Review mod update');
  await dialog[2].find(([label]) => label === 'Install update')[1]();
  assert.deepEqual(requests[1], ['update-archive', {token: 'preview123'}]);
  assert.match($('download-list').innerHTML, /1 mod updated/);
  assert.equal(state.downloads.length,0,'a direct ZIP update has no download-panel job');
  assert.equal($('update-log').hidden,false,'direct ZIP completion reveals its results without a panel job');
  assert.equal($('update-log').open,true,'direct ZIP completion expands the result log');
  run('renderDownloads()');
  assert.equal($('update-log').hidden,false,'ordinary download rendering retains direct update results');
  assert.equal($('update-log').open,true,'refreshing an unchanged log preserves its expanded result');
  assert.equal($('download-log-summary').textContent,'Last update results');

  assert.match($('download-panel-message').textContent, /Slow Download/);
  assert.equal($('download-folder').textContent, 'C:\\Local\\GK2MT\\nexus-downloads');
  const beforePanel = workingCalls;
  await run('downloadPanelAction("download-panel", "mod1")');
  assert.deepEqual(requests.at(-2), ['download-panel', {id: 'mod1'}]);
  assert.equal(workingCalls, beforePanel, 'the panel must not leave the main app blocked');
  assert.match($('download-jobs').innerHTML, /Waiting for your download/);
  assert.match($('download-jobs').innerHTML, /data-download-cancel="job1"/);
  assert.doesNotMatch($('updates-list').innerHTML, /data-download-panel=/, 'do not open a duplicate panel');
  assert.match($('updates-list').innerHTML, /Download in progress/);
  assert.match($('updates-list').innerHTML, /Choose the download in the panel\./,'the update row shows the active job’s next step');
  assert.match($('updates-list').innerHTML, /Fixed keyboard input\./, 'notes remain visible during download');
  assert.equal(timers.size, 1, 'poll only while a download is active');
  jobs = [{...jobs[0], state: 'downloading', received: 50, total: 100}];
  await run('pollDownloads()');
  assert.match($('download-jobs').innerHTML, /value="50"/);
  jobs = [{...jobs[0], state:'error',message:'Deployment blocked by <Framework file>',retryable:true,cancellable:false}];
  await run('pollDownloads()');
  assert.match($('updates-list').innerHTML, /data-download-retry="job1" class="primary">Retry installation/,'the update row offers reuse of the retained ZIP');
  assert.match($('updates-list').innerHTML, /class="small warning">Deployment blocked by &lt;Framework file&gt;<\/p>/,'the failed installation is visible and escaped beside its retry action');
  assert.doesNotMatch($('updates-list').innerHTML,/data-download-panel=|Download in progress/,'a recoverable failure does not ask for another download');
  assert.match($('download-jobs').innerHTML,/data-download-retry="job1"/);assert.equal(timers.size,0,'failed installations wait for an explicit retry');
  const panelsBeforeRetry=requests.filter(([action])=>action==='download-panel').length;
  await run('downloadPanelAction("download-panel-retry", "job1")');
  assert.deepEqual(requests.at(-2),['download-panel-retry',{id:'job1'}]);
  assert.equal(requests.filter(([action])=>action==='download-panel').length,panelsBeforeRetry,'retry keeps the existing job rather than opening a new panel');
  jobs = [{...jobs[0], state: 'waiting_game', retryable: true, cancellable: true}];
  await run('pollDownloads()');
  assert.match($('updates-list').innerHTML,/data-download-retry="job1"/,'the update row itself offers the game-closed retry');
  assert.match($('updates-list').innerHTML,/Downloaded\. Close the game, then install\./);
  assert.match($('download-jobs').innerHTML, /data-download-retry="job1"/);
  assert.equal(timers.size, 0, 'waiting for the game to close is resumed by the user');
  await run('downloadPanelAction("download-panel-retry", "job1")');
  assert.deepEqual(requests.at(-2), ['download-panel-retry', {id: 'job1'}]);
  assert.equal(timers.size, 1);
  const beforeCompletion = refreshes;
  jobs = [{...jobs[0], state: 'completed', cancellable: false, retryable: false}];
  await run('pollDownloads()');
  assert.equal(refreshes, beforeCompletion + 1, 'refresh installed mods on completion');
  assert.match($('download-jobs').innerHTML, /Updated/);
  assert.equal(timers.size, 0, 'stop polling after completion');
  await run('pollDownloads()');
  assert.equal(refreshes, beforeCompletion + 1, 'one completed download refreshes once');
  await run('downloadPanelAction("download-panel", "mod1")');
  await run('downloadPanelAction("download-panel-cancel", "job1")');
  assert.deepEqual(requests.at(-2), ['download-panel-cancel', {id: 'job1'}]);
  assert.equal(timers.size, 0);
  retryState = 'completed';
  const beforeRetryCompletion = refreshes;
  await run('downloadPanelAction("download-panel-retry", "job1")');
  assert.equal(refreshes, beforeRetryCompletion + 1, 'a synchronous successful retry refreshes inventory too');
  state.download_panel = {available: false, folder: 'private folder', message: 'Install the download panel runtime.'};
  run('renderUpdates()');
  assert.doesNotMatch($('updates-list').innerHTML, /data-download-panel=/);
  assert.match($('updates-list').innerHTML, /data-update-archive="mod1"/);
  assert.equal($('download-panel-message').textContent, 'Install the download panel runtime.');
  state.download_panel.available = true;
  state.downloads = [];

  state.updates.account.premium = true;
  update.downloadable = true;
  delete update.blocked_reason;
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /data-update="mod1"/);
  assert.equal($('download-updates').disabled, false);
  update.known_version = false;
  run('renderUpdates()');
  assert.match($('updates-list').innerHTML, /Review details/);
  assert.doesNotMatch($('updates-list').innerHTML, /data-update(?:-archive)?=|data-download-panel=/);
  assert.equal($('download-updates').disabled, true, 'unknown versions must stay out of bulk updates');
  state.nexus_connected = false;
  state.updates = null;
  run('renderUpdates()');
  assert.equal($('updates-connections').open,true,'missing connection opens only the required setup panel');
  assert.match($('nexus-status').textContent, /Not connected/);
  assert.equal($('check-updates').disabled, true);
  assert.equal($('download-updates').disabled, true);
  assert.equal($('nexus-forget').hidden, true);
  assert.equal($('nexus-connect').textContent, 'Connect Nexus');
  assert.ok(/id="nexus-remember"[^>]*type="checkbox"[^>]*checked/.test(html),'Nexus credential storage is offered as an opt-in checkbox, enabled by default');
  assert.doesNotMatch(html + source, /key stays in memory|API key is cleared when GK2MT closes|Reconnect after closing/);
  state.nexus_connected = state.nexus_saved = true;
  run('renderUpdates()');
  assert.match($('nexus-status').textContent, /Saved key restored.*Check for updates/);
  assert.equal($('nexus-connect').textContent, 'Replace key');
  assert.equal($('nexus-forget').hidden, false);
  assert.equal($('check-updates').disabled, false);
  assert.match($('updates-list').innerHTML, /Check for updates to find/);
  assert.doesNotMatch($('updates-list').innerHTML, /Connect Nexus/);
  assert.equal($('nexus-key').value, '', 'a restored key is never filled into the input');
  state.nexus_connected = false;
  state.nexus_error = 'The saved key could not be restored. Replace it or forget it.';
  run('renderUpdates()');
  assert.equal($('nexus-status').textContent, state.nexus_error);
  assert.equal($('nexus-forget').hidden, false, 'an unreadable saved key can still be forgotten');
  assert.equal($('check-updates').disabled, true);
  const beforeNexus = refreshes;
  $('nexus-key').value = 'test-only-nexus-key';
  await $('nexus-connect').onclick();
  assert.deepEqual(requests.at(-1), ['nexus-connect', {key:'test-only-nexus-key',remember_key:true}]);
  assert.equal(refreshes, beforeNexus + 1);
  nexusError = Error('API key rejected');
  $('nexus-key').value = 'test-only-invalid-key';
  await assert.rejects($('nexus-connect').onclick(), /API key rejected/);
  assert.equal($('nexus-key').value, '', 'failed validation does not leave the key in the DOM');
  nexusError = null;
  $('nexus-key').value = 'unused-replacement';
  await $('nexus-forget').onclick();
  assert.deepEqual(requests.at(-1), ['nexus-forget', undefined]);
  assert.equal(refreshes, beforeNexus + 2, 'forget refreshes the account controls');
  state.nexus_saved = false;
  state.nexus_error = '';
  run('renderUpdates()');
  assert.equal($('nexus-forget').hidden, true);
  assert.equal($('nexus-connect').textContent, 'Connect Nexus');
  assert.match($('nexus-status').textContent, /Not connected/);

  $('nexus-remember').checked=false;$('nexus-key').value='test-session-key';
  await $('nexus-connect').onclick();
  assert.deepEqual(requests.at(-1),['nexus-connect',{key:'test-session-key',remember_key:false}],'the unchecked choice asks for a session-only connection');
  assert.equal($('nexus-key').value,'');assert.equal($('nexus-remember-option').hidden,true);
  $('nexus-remember').checked=true;

  deckUITesting=true;run('deckFieldsFill(state.settings.deck); renderDeckConnection()');
  assert.equal($('deck-preview').disabled, false);
  assert.equal($('deck-proton-setup').disabled, true,'installation waits for a verified comparison and known incomplete loading status');
  assert.match($('deck-connect').innerHTML,/<strong>Reconnect<\/strong><span>Deck connected · game folders found<\/span>/,'the top Connect card contains both action and connection progress');
  assert.match($('deck-preview').innerHTML,/<strong>Compare<\/strong><span>Verify game versions and review mod files<\/span>/);
  assert.match($('deck-proton-setup').innerHTML,/Unlocks after a matching comparison/);
  const beforeSetup = requests.length;
  $('deck-workshop_path').value = '';
  run('renderDeckConnection()');
  assert.equal($('deck-preview').disabled, true);
  assert.equal($('deck-proton-setup').disabled, true, 'a comparison needs both folders before it can unlock installation');
  assert.equal(requests.length, beforeSetup, 'rendering never writes or checks the Deck automatically');
  protonResult = {configured:false, loader_installed:false, prefix:'/Steam/steamapps/compatdata/4358690/pfx'};
  await run('configureDeckProton()');
  assert.equal(requests.at(-1)[0], 'deck-proton-status');
  assert.equal(requests.at(-1)[1].deck.workshop_path, '');
  assert.equal('password' in requests.at(-1)[1].deck, false);
  assert.match($('deck-proton-status').innerHTML, /not configured/);
  assert.match($('deck-proton-status').innerHTML, /files are missing/);
  $('deck-workshop_path').value='/workshop';
  deckResult={digest:'before-proton',game_version:{matched:true,build_id:'12345'},counts:{additions:1,changes:0,extras:0,local_files:1},additions:['game/mod.dll'],changes:[],extras:[]};
  await run('compareDeck()');
  assert.equal($('deck-proton-setup').disabled,false,'matching versions and an incomplete setting unlock installation');
  protonResult = {...protonResult, configured:true, changed:true, backup:'/backup/<registry>.reg'};
  deckResult={...deckResult,digest:'after-proton'};
  await run('configureDeckProton(true)');
  assert.equal(requests.findLast(([action])=>action==='deck-proton-setup')[0], 'deck-proton-setup');
  assert.equal(context.comparison.digest,'after-proton','Proton installation replaces the invalidated comparison');
  assert.equal($('sync-now').disabled,false,'sync supplies missing loader files after loading is configured');
  assert.match($('deck-proton-setup').innerHTML,/<strong>BepInEx configured<\/strong><span>Loading configured · files copied during sync<\/span>/);
  assert.match($('sync-now').innerHTML,/<strong>Deck Sync<\/strong><span>1 new · 0 changed · 0 extras<\/span>/,'the actionable Sync card reports the reviewed transfer');
  assert.match($('deck-proton-status').innerHTML, /override configured/);
  assert.match($('deck-proton-status').innerHTML, /files are missing/, 'configuration is separate from installed loader files');
  assert.match($('deck-proton-status').innerHTML, /&lt;registry&gt;/);
  protonResult = {...protonResult, loader_installed:true};
  await run('configureDeckProton()');
  assert.match($('deck-proton-status').innerHTML, /Launch the game to confirm/);
  $('deck-game_path').value = '/other-game';
  run('renderDeckConnection()');
  assert.doesNotMatch($('deck-proton-status').innerHTML, /override configured/);
  $('deck-game_path').value = '/game';
  protonResult = Error('Launch the game once through Proton, then close it.');
  await assert.rejects(run('configureDeckProton()'), /Launch the game once/);
  assert.match($('deck-proton-status').innerHTML, /Launch the game once/);
  assert.doesNotMatch($('deck-proton-status').innerHTML, /override configured/);
  let finishProton;
  protonResult = () => new Promise(resolve => {finishProton=resolve;});
  const pendingSetup=run('configureDeckProton()');
  assert.equal($('deck-proton-check').disabled, true);
  run('resetDeckProton()');
  finishProton({configured:true,loader_installed:true,prefix:'/old-target'});
  await pendingSetup;
  assert.doesNotMatch($('deck-proton-status').innerHTML, /override configured|old-target/, 'discard responses for invalidated connection fields');
  state.deck_connection.connected=false;
  run('renderDeckConnection()');
  assert.equal($('deck-proton-setup').disabled, true);
  assert.equal($('deck-proton-check').disabled, true);
  await assert.rejects(run('configureDeckProton(true)'), /Connect to your Deck/);
  state.deck_connection.connected=true;
  $('deck-workshop_path').value = '/workshop';
  run('renderDeckConnection()');
  $('deck-host').value = '192.168.1.51';
  context.comparison = {digest: 'old-preview'};
  run('renderDeckConnection()');
  assert.equal($('deck-preview').disabled, true);
  assert.equal(context.comparison, null, 'changing the Deck invalidates the sync preview');
  $('deck-host').value = '192.168.1.50';
  $('deck-password').value = 'test-only-password';
  assert.deepEqual(Object.keys(run('locationsBody()')).sort(), ['game', 'import_folder', 'workshop']);
  assert.equal('password' in run('deckFields()'), false);
  const before = refreshes;
  await assert.rejects(run('connectDeck(deckFields(), "test-host-key")'), /simulated authentication error/);
  assert.equal(requests.at(-1)[1].password, 'test-only-password');
  assert.equal(requests.at(-1)[1].expected_key, 'test-host-key');
  assert.equal($('deck-password').value, '');
  assert.equal(refreshes, before + 1, 'refresh connection status after a failed login');
  deckResult = {digest:'verified', game_version:{matched:true,build_id:'12345'},
    counts:{additions:0,changes:1,extras:0,local_files:1}, additions:[],changes:['game/mod.dll'],extras:[]};
  protonResult={configured:true,loader_installed:true,prefix:'/game-prefix'};
  await run('compareDeck()');
  assert.match($('deck-comparison').innerHTML,/Game version verified.*12345/);
  assert.equal($('sync-now').disabled,false,'a verified comparison unlocks the single grouped Sync control');
  assert.match($('deck-preview').innerHTML,/<strong>Compare again<\/strong><span>Same Steam build 12345 · file changes reviewed<\/span>/,'verified build progress remains on the Compare action card');
  assert.doesNotMatch($('deck-comparison').innerHTML,/id="sync-now"/,'comparison results do not duplicate the grouped control');
  deckResult = Error('Game versions differ: PC 12345, Deck 11111.');
  await assert.rejects(run('compareDeck()'),/versions differ/);
  assert.equal(context.comparison,null);
  assert.match($('deck-comparison').innerHTML,/Comparison stopped/);
  assert.equal($('sync-now').disabled,true,'a mismatched build locks Sync');
  assert.match($('deck-preview').innerHTML,/Comparison stopped · see the message below/,'the action card points to the blocked comparison result');
  assert.doesNotMatch($('deck-comparison').innerHTML,/id="sync-now"/);
  deckResult = {digest:'unverified'};
  await assert.rejects(run('compareDeck()'),/version was not verified/);
  assert.equal(context.comparison,null);
  assert.equal($('sync-now').disabled,true,'an unverified build locks Sync');
  assert.doesNotMatch($('deck-comparison').innerHTML,/id="sync-now"/);
  await run('fullDeckGuide()');
  assert.equal(requests.at(-1)[0],'deck-guide');
  assert.match(dialog[1],/# Setup\n&lt;guide&gt; &amp; recovery/);
  let prevented=false;
  context.linkEvent={target:{closest:()=>({getAttribute:()=> 'https://www.nexusmods.com/graveyardkeeper2/mods/48'})},preventDefault(){prevented=true;}};
  assert.equal(run('openLink(linkEvent)'),true);
  assert.equal(prevented,true,'external pages never replace the app document');
  assert.deepEqual(requests.at(-1),['open-external',{url:'https://www.nexusmods.com/graveyardkeeper2/mods/48'}]);
  const beforeInvalidLink=requests.length;
  context.linkEvent.target.closest=()=>({getAttribute:()=> 'file:///untrusted'});
  assert.equal(run('openLink(linkEvent)'),true);
  assert.equal(requests.length,beforeInvalidLink,'only HTTPS links reach the external opener');
  console.log('Web checks passed: account-free missing-only foundation/loader setup, prerequisite and existing-loader guards, retry recovery, batch ZIP conflict review, shared-file presentation, duplicate warnings, profiles, uninstall review, Steam/Nexus/Manual sources, Workshop rescans, native bridge, Nexus browsing and downloads, update changelogs, remembered credentials, Deck trust and target isolation, Proton setup, and mandatory game-version comparison.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });

// Run: node test_quick_setup_web.js. UI logic only; no browser, network or saved credentials.
'use strict';
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const html=fs.readFileSync('web/index.html','utf8'), source=fs.readFileSync('web/setup.js','utf8');
const nodes=new Map(), requests=[], messages=[], timers=new Map(), pages=[];
let timer=0, checkResult, setupResult={requires_download:false,files:0,message:'Detected BepInEx kept.'}, setupFailure, workshopFailure, selectedZip='C:\\fixtures\\BepInEx.zip';
function $(id){
  if(!nodes.has(id)){
    const classes=new Set(), listeners=new Map();let value='';
    nodes.set(id,{textContent:'',hidden:false,checked:false,disabled:false,open:false,tabIndex:0,
      get value(){return value;},set value(next){value=String(next);},
      classList:{toggle(name,on){if(on)classes.add(name);else classes.delete(name);},contains(name){return classes.has(name);}},
      addEventListener(event,callback){listeners.set(event,callback);},
      emit(event){return listeners.get(event)?.({target:this});},
      showModal(){this.open=true;},close(){this.open=false;this.emit('close');},
      querySelector(){return $('heading-mods');},focus(){this.focused=true;}});
  }
  return nodes.get(id);
}
const deck={host:'192.0.2.50',user:'deck',port:22,key:'',game_path:'/home/deck/game/GK2',workshop_path:'/home/deck/workshop/content/4358690'};
const state={settings:{game:'C:\\Steam\\game',workshop:'C:\\Steam\\workshop\\4358690',import_folder:'C:\\imports',deck},
  nexus_connected:true,nexus_saved:true,deck_connection:{connected:false,host:deck.host,password_saved:true}};
const context=vm.createContext({$,state,working:false,currentPage:'mods',
  document:{addEventListener(){}},window:{addEventListener(){}},
  toast:(message,error)=>messages.push({message,error}),
  focusAfterWork:element=>{element.tabIndex=-1;element.focus({preventScroll:true});},
  setTimeout(callback){timers.set(++timer,callback);return timer;},clearTimeout(id){timers.delete(id);},
  work:async(_,task)=>task(),refresh:async()=>{},page:name=>pages.push(name),stagePaths:async()=>{},
  api:async(action,body)=>{
    requests.push([action,JSON.parse(JSON.stringify(body))]);
    if(action==='setup-check')return checkResult;
    if(action==='settings'){Object.assign(state.settings,body);return {};}
    if(action==='quick-setup-complete'){state.quick_setup_completed=true;state.settings.quick_setup_completed=true;state.settings.workshop_enabled=body.workshop_enabled;return {};}
    if(action==='browse')return {path:selectedZip};
    if(action==='setup'){if(setupFailure)throw setupFailure;if(!setupResult.requires_download && setupResult.files)checkResult={...checkResult,loader_installed:true};return setupResult;}
    if(action==='workshop-loader-setup'){
      if(workshopFailure)throw workshopFailure;
      if(checkResult.workshop_setup.state==='ready')checkResult={...checkResult,workshop_setup:{installed:true,state:'installed',can_install:false,message:'Workshop Loader installed from GitHub. Your existing settings are kept.'}};
      return checkResult.workshop_setup;
    }
    if(action==='nexus-connect'){
      assert.equal($('qs-nexus-key').value,'','Do not retain a submitted plaintext key in the input.');
      state.nexus_connected=true;state.nexus_saved=body.remember_key===true;return {};
    }
    throw Error('Unexpected action: '+action);
  }});
vm.runInContext(source,context);
const run=code=>vm.runInContext(code,context);
const ready=(overrides={})=>({game:{path:state.settings.game,status:'ready',valid:true,message:'Game found.'},
  workshop:{path:state.settings.workshop,status:'ready',valid:true,message:'Folder found.'},
  import_folder:{path:state.settings.import_folder,status:'ready',valid:true,message:'Folder found.'},
  game_found:true,workshop_found:true,loader_installed:true,workshop_setup:{installed:true,state:'installed'},...overrides});

async function deferredFocus(){
  const appSource=fs.readFileSync('web/app.js','utf8'), focusCalls=[], focusNodes=new Map();
  const main={inert:false}, sidebar={inert:true}, dialog={inert:false}, setup={inert:false};
  for(const name of ['mods','updates']){
    const heading={tabIndex:0,focus(){assert.equal(main.inert,false,'Do not focus inside an inert main region.');focusCalls.push(name);}};
    focusNodes.set('page-'+name,{id:'page-'+name,hidden:false,querySelector:()=>heading});
  }
  for(const id of ['busy','download-status','breadcrumb'])focusNodes.set(id,{querySelector:()=>({textContent:''})});
  const focusContext=vm.createContext({working:false,currentPage:'mods',$:id=>focusNodes.get(id),
    location:{hash:''},window:{scrollTo(){}},toast(){},
    document:{body:{classList:{add(){},remove(){}}},querySelectorAll(selector){
      if(selector==='main, .sidebar, #dialog, #quick-setup')return [main,sidebar,dialog,setup];
      if(selector==='.page')return [...focusNodes.values()].filter(node=>node.id?.startsWith('page-'));
      if(selector==='[data-page]')return [];
      throw Error('Unexpected selector: '+selector);
    }},assert,focusCalls,main});
  vm.runInContext(appSource.slice(appSource.indexOf('async function work('),appSource.indexOf('function modal(')),focusContext);
  await vm.runInContext(`work('Closing Quick Setup',async()=>{
    focusAfterWork($('page-mods').querySelector('h1'));
    page('updates');
    await Promise.resolve();
    assert.equal(main.inert,true);
    assert.equal(focusCalls.length,0,'Focus should wait for the entire async operation.');
  })`,focusContext);
  assert.deepEqual(focusCalls,['updates'],'The latest requested page gets focus after work completes.');
  assert.equal(main.inert,false);assert.equal(sidebar.inert,true,'Keep a previously inert region inert.');
  assert.equal(focusNodes.get('page-updates').querySelector().tabIndex,-1);
  vm.runInContext("page('mods')",focusContext);
  assert.deepEqual(focusCalls,['updates','mods'],'Normal navigation focuses immediately.');
  await vm.runInContext("work('No focus requested',async()=>Promise.resolve())",focusContext);
  assert.equal(focusCalls.length,2,'A stale queued focus must not leak into later work.');
}

async function main(){
  await deferredFocus();
  // Legacy settings have no completion flag. Opening must prefill paths without reconnecting,
  // resubmitting credentials or pretending a remembered Deck password is plaintext.
  checkResult=ready();await run('openQuickSetup()');
  assert.equal(state.quick_setup_completed,undefined);
  assert.equal($('quick-setup').open,true);
  assert.equal($('qs-game').value,state.settings.game);
  assert.equal($('qs-workshop').value,state.settings.workshop);
  assert.equal($('qs-import').value,state.settings.import_folder);
  assert.equal($('qs-deck-host').value,deck.host);
  assert.equal($('qs-nexus-key').value,'');assert.equal($('qs-deck-password').value,'');
  assert.equal($('qs-nexus-enabled').checked,true);
  assert.equal($('qs-nexus-remember-option').hidden,true);
  assert.equal($('qs-nexus-field').classList.contains('field-ready'),true);
  assert.equal($('qs-deck-enabled').checked,false);
  assert.equal($('qs-deck-options').hidden,true);
  assert.equal($('qs-deck-remember').checked,true);
  assert.equal($('qs-finish').disabled,false);
  assert.equal($('qs-bep-install').hidden,true);assert.equal($('qs-bep-advanced').hidden,true,'detected BepInEx does not offer replacement in first-run setup');
  assert.equal($('qs-workshop-install').hidden,true,'detected Workshop Loader is kept');
  assert.deepEqual(requests.map(([action])=>action),['setup-check']);
  assert.equal(state.nexus_saved,true);assert.equal(state.deck_connection.password_saved,true);

  // Optional sections only disclose their controls after selection and credential entry.
  $('qs-nexus-enabled').checked=false;$('qs-nexus-enabled').emit('change');
  assert.equal($('qs-nexus-options').hidden,true);assert.equal(state.nexus_connected,true);
  $('qs-nexus-enabled').checked=true;$('qs-nexus-enabled').emit('change');
  $('qs-nexus-key').value='fixture-replacement-key';$('qs-nexus-key').emit('input');
  assert.equal($('qs-nexus-remember-option').hidden,false);
  assert.equal($('qs-finish').disabled,true,'A typed replacement must be connected or explicitly skipped.');
  $('qs-nexus-remember').checked=false;await $('qs-nexus-connect').onclick();
  const connect=requests.find(([action])=>action==='nexus-connect');
  assert.deepEqual(connect[1],{key:'fixture-replacement-key',remember_key:false});
  assert.equal(state.nexus_saved,false);assert.equal($('qs-finish').disabled,false);
  $('qs-deck-enabled').checked=true;$('qs-deck-enabled').emit('change');
  assert.equal($('qs-deck-options').hidden,false);
  assert.equal($('qs-finish').disabled,true,'Selected disconnected Deck requires connection or explicit skip.');
  state.deck_connection.password_saved=false;run('renderQuickSetup()');
  assert.equal($('qs-deck-remember-option').hidden,true);
  $('qs-deck-password').value='fixture-password';$('qs-deck-password').emit('input');
  assert.equal($('qs-deck-remember-option').hidden,false);
  $('qs-deck-password').value='';state.deck_connection.connected=true;
  state.settings.deck.game_path='';run('renderQuickSetup()');
  assert.doesNotMatch($('qs-deck-host-note').textContent,/folder.*detected/);
  assert.match($('qs-deck-host-note').textContent,/choose game folders/);
  $('qs-deck-enabled').checked=false;$('qs-deck-enabled').emit('change');

  // Required checks block completion. An unchecked Workshop does not block a Nexus/manual setup.
  checkResult=ready({loader_installed:false,workshop_setup:{installed:false,state:'missing_bepinex'}});
  await run('checkQuickSetup()');assert.equal($('qs-finish').disabled,true);
  let count=requests.filter(([action])=>action==='quick-setup-complete').length;
  await assert.rejects($('qs-finish').onclick(),/Install BepInEx/);
  assert.equal(requests.filter(([action])=>action==='quick-setup-complete').length,count);
  checkResult=ready({workshop_setup:{installed:false,state:'ready',can_install:true,source:'GitHub'}});
  await run('checkQuickSetup()');assert.equal($('qs-finish').disabled,true);
  $('qs-workshop-enabled').checked=false;$('qs-workshop-enabled').emit('change');
  assert.equal($('qs-workshop-options').hidden,true);assert.equal($('qs-finish').disabled,false);
  await $('qs-finish').onclick();
  assert.deepEqual(requests.find(([action])=>action==='quick-setup-complete')[1],{workshop_enabled:false});
  assert.equal(state.quick_setup_completed,true);assert.equal($('quick-setup').open,false);
  assert.equal($('heading-mods').tabIndex,-1);assert.equal($('heading-mods').focused,true);
  assert.equal(state.nexus_connected,true,'Skipping an optional section must not disconnect an existing account.');

  // Both automatic and ZIP routes must respect a manual/ambiguous-file result and avoid false success.
  checkResult=ready({loader_installed:false});await run('openQuickSetup()');
  setupResult={requires_download:true,message:'Choose the matching MAIN file.',choices:[{file_id:1,name:'Windows bundle'}]};
  const successBefore=messages.filter(({message})=>message.includes('BepInEx installed')).length;
  await $('qs-bep-install').onclick();
  assert.equal($('qs-bep-download').hidden,false);assert.match($('qs-bep-download-message').textContent,/matching MAIN/);
  assert.equal($('qs-bep-advanced').open,true,'an unexpected fallback result reveals the advanced alternative');
  await $('qs-bep-zip').onclick();
  assert.equal($('qs-bep-download').hidden,false);
  assert.equal(messages.filter(({message})=>message.includes('BepInEx installed')).length,successBefore);
  assert.equal($('qs-finish').disabled,true);
  setupResult={requires_download:false,files:3,message:'BepInEx installed from the selected Nexus ZIP.'};await $('qs-bep-zip').onclick();
  assert.equal($('qs-bep-download').hidden,true);
  assert.equal(messages.filter(({message})=>message.includes('BepInEx installed')).length,successBefore+1);
  assert.equal($('qs-bep-advanced').hidden,true,'the ZIP alternative folds away once installation is detected');

  // Fresh setup downloads missing components without Nexus, Steam loader subscription,
  // or an already-created Workshop content folder. Failure leaves a normal retry.
  state.nexus_connected=false;state.settings.workshop_enabled=true;
  checkResult=ready({loader_installed:false,workshop_found:false,
    workshop:{path:state.settings.workshop,status:'attention',valid:false,message:'Steam will create this folder after your first Workshop download.'},
    workshop_setup:{installed:false,state:'missing_bepinex',can_install:false,source:'GitHub'}});
  await run('openQuickSetup()');assert.equal($('qs-nexus-enabled').checked,false);
  assert.equal($('qs-bep-install').disabled,false);assert.equal($('qs-workshop-install').disabled,true);
  const foundationStart=requests.length;setupFailure=Error('GitHub download unavailable. Try again.');
  await assert.rejects($('qs-bep-install').onclick(),/GitHub download unavailable/);
  assert.equal($('qs-error').hidden,false);assert.equal($('qs-bep-install').disabled,false);assert.equal($('qs-finish').disabled,true);
  setupFailure=null;setupResult={requires_download:false,files:12,message:'BepInEx installed from GitHub. Existing files kept.'};
  checkResult.workshop_setup={installed:false,state:'ready',can_install:true,source:'GitHub',message:'Workshop Loader is ready to download from GitHub.'};
  await $('qs-bep-install').onclick();
  assert.equal($('qs-error').hidden,true);assert.equal($('qs-bep-install').hidden,true);assert.equal($('qs-workshop-install').disabled,false);
  assert.deepEqual(requests.filter(([action])=>action==='setup').at(-1)[1],{},'the default setup does not send an archive, file ID or Nexus key');
  assert.ok(requests.slice(foundationStart).every(([action])=>['setup-check','settings','setup'].includes(action)),'account-free foundation setup never opens Nexus or connects an account');
  const workshopStart=requests.length;workshopFailure=Error('Workshop Loader download failed. Try again.');
  await assert.rejects($('qs-workshop-install').onclick(),/download failed/);
  assert.equal($('qs-error').hidden,false);assert.equal($('qs-workshop-install').disabled,false);assert.equal($('qs-finish').disabled,true);
  workshopFailure=null;await $('qs-workshop-install').onclick();
  assert.equal($('qs-error').hidden,true);assert.equal($('qs-workshop-install').hidden,true);assert.equal($('qs-finish').disabled,false,'missing Steam content folder does not block installed mod support');
  assert.ok(requests.slice(workshopStart).every(([action])=>['setup-check','settings','workshop-loader-setup'].includes(action)),'loader setup does not subscribe, open Steam or poll for a Workshop download');
  assert.equal(timers.size,0,'no obsolete loader download poll remains');
  await $('qs-finish').onclick();assert.equal(state.settings.workshop_enabled,true);assert.equal($('quick-setup').open,false);
  for(const blockedState of ['disabled','blocked']){
    checkResult=ready({workshop_setup:{installed:false,state:blockedState,can_install:false,message:'Resolve the existing loader in My mods.'}});
    const beforeOpen=requests.length;await run('openQuickSetup()');
    assert.equal($('qs-workshop-install').hidden,true);assert.equal($('qs-workshop-resolve').hidden,false);assert.equal($('qs-finish').disabled,true);
    assert.ok(requests.slice(beforeOpen).every(([action])=>action==='setup-check'),'opening setup never enables or replaces an old loader');
    $('qs-workshop-resolve').onclick();assert.equal($('quick-setup').open,false);assert.equal(pages.at(-1),'mods');
  }
  for(const [action,body] of requests.filter(([action])=>action==='settings')){
    assert.deepEqual(Object.keys(body).sort(),['game','import_folder','workshop']);
    assert.doesNotMatch(JSON.stringify(body),/fixture-replacement-key|fixture-password/);
  }
  for(const id of nodes.keys())if(id!=='heading-mods')assert.ok(html.includes(`id="${id}"`),`Missing Quick Setup element: ${id}`);
  assert.doesNotMatch(html,/id="qs-subscribe"/);assert.match(html,/GK2MT · v0\.1\.2/);
  console.log('Quick Setup account-free missing-only installs, existing-loader guards, retry, optional services, completion gates and advanced Nexus fallback passed.');
}
main().catch(error=>{console.error(error);process.exitCode=1;});

'use strict';
let quickSetupCheck=null, quickSetupRevision=0, quickSetupTimer, quickWorkshopTimer, quickWorkshopWaiting=false, quickDeckTrust=null;

function quickPaths(){return {game:$('qs-game').value.trim(),workshop:$('qs-workshop').value.trim(),import_folder:$('qs-import').value.trim()};}
function setupField(id,note,status,message){
  $(id).classList.toggle('field-ready',status==='ready');
  $(id).classList.toggle('field-attention',status==='attention');
  if(note)$(note).textContent=message || '';
}
function setupBadge(id,ready,message,optional=false){
  $(id).textContent=message;$(id).classList.toggle('green',ready);$(id).classList.toggle('warning',!ready && !optional);
}
function quickDeckReady(){return state.deck_connection?.connected===true && state.deck_connection.host===$('qs-deck-host').value.trim() && !$('qs-deck-password').value;}
function renderQuickSetup(){
  if(!$('quick-setup').open)return;
  const check=quickSetupCheck || {},workshop=$('qs-workshop-enabled').checked;
  const game=check.game_found===true,bep=check.loader_installed===true,loader=!workshop || check.workshop_setup?.installed===true;
  const nexusSelected=$('qs-nexus-enabled').checked,deckSelected=$('qs-deck-enabled').checked;
  const nexusReady=!!state.nexus_connected && !$('qs-nexus-key').value.trim(),deckReadyNow=quickDeckReady();
  for(const [key,field] of [['game','qs-game'],['workshop','qs-workshop'],['import_folder','qs-import']]){
    const result=check[key] || {};
    setupField(field+'-field',field+'-note',result.status || 'empty',result.message);
  }
  setupBadge('qs-folders-status',game,game?'Game found':'Choose game folder');
  setupBadge('qs-support-status',bep && loader,bep && loader?'Ready':!bep?'Install BepInEx':'Finish Workshop setup');
  $('qs-bep-message').textContent=bep?'Installed and detected.':'Required to load BepInEx mods. Existing files are backed up.';
  $('qs-bep-install').hidden=bep;$('qs-bep-zip').hidden=bep;
  $('qs-bep-install').disabled=!game;$('qs-bep-zip').disabled=!game;
  $('qs-workshop-options').hidden=!workshop;
  $('qs-workshop-message').textContent=check.workshop_setup?.message || 'Subscribe to the loader in Steam, then refresh. GK2MT copies it to the game’s patchers folder.';
  $('qs-workshop-message').classList.toggle('green',loader && workshop);
  const setup=check.workshop_setup || {};
  $('qs-subscribe').hidden=setup.installed===true;$('qs-workshop-install').hidden=setup.installed===true;
  $('qs-subscribe').disabled=!game || !bep;
  $('qs-workshop-install').disabled=!game || !bep || ['blocked','disabled'].includes(setup.state);
  $('qs-nexus-options').hidden=!nexusSelected;$('qs-deck-options').hidden=!deckSelected;
  $('qs-nexus-remember-option').hidden=!$('qs-nexus-key').value.trim();
  $('qs-nexus-connect').disabled=!$('qs-nexus-key').value.trim();
  setupBadge('qs-nexus-status',nexusReady,nexusSelected?(nexusReady?'Connected':'Connect or skip'):'',!nexusSelected);
  setupField('qs-nexus-field','qs-nexus-note',nexusSelected?(nexusReady?'ready':'attention'):'empty',nexusReady?(state.nexus_saved?'Connected · key saved securely':'Connected for this session'):'');
  $('qs-deck-remember-option').hidden=!$('qs-deck-password').value && !state.deck_connection?.password_saved;
  setupBadge('qs-deck-status',deckReadyNow,deckSelected?(deckReadyNow?'Connected':'Connect or skip'):'',!deckSelected);
  setupField('qs-deck-host-field','qs-deck-host-note',deckSelected?(deckReadyNow?'ready':'attention'):'empty',deckReadyNow?(state.settings.deck?.game_path?'Connected · game folder detected':'Connected · choose game folders in Steam Deck → Advanced'):'');
  $('qs-deck-connect').disabled=!$('qs-deck-host').value.trim();
  const readyCount=Number(game)+Number(bep)+Number(loader);
  $('qs-progress').textContent=`${readyCount} of 3 required checks ready`;$('qs-progress-bar').value=readyCount;
  const reason=!game?'Choose the game folder to continue.':!bep?'Install BepInEx to continue.':!loader?'Finish Workshop setup, or leave Workshop unchecked for now.':nexusSelected && !nexusReady?'Connect Nexus, or leave it unchecked to set up later.':deckSelected && !deckReadyNow?'Connect your Deck, or leave it unchecked to set up later.':'';
  $('qs-finish').disabled=!!reason;$('qs-finish-note').textContent=reason || 'Ready. Nexus and Deck can also be set up later.';
}
async function checkQuickSetup(){
  const revision=++quickSetupRevision;
  const result=await api('setup-check',quickPaths());
  if(revision!==quickSetupRevision || !$('quick-setup').open)return;
  quickSetupCheck=result;
  if(!$('qs-workshop').value && result.workshop?.path)$('qs-workshop').value=result.workshop.path;
  renderQuickSetup();
}
async function quickWork(message,task){
  $('qs-error').hidden=true;
  return work(message,async()=>{
    try{await task();await refresh();await checkQuickSetup();}
    catch(error){$('qs-error').textContent=error.message;$('qs-error').hidden=false;throw error;}
  });
}
async function saveQuickFolders(){
  await checkQuickSetup();
  if(!quickSetupCheck?.game_found)throw new Error(quickSetupCheck?.game?.message || 'Choose the Graveyard Keeper 2 folder.');
  const result=await api('settings',quickPaths());
  await refresh(true);return result;
}
async function openQuickSetup(){
  const config=state.settings || {};
  quickSetupCheck=null;
  $('qs-game').value=config.game || '';$('qs-workshop').value=config.workshop || '';$('qs-import').value=config.import_folder || '';
  $('qs-workshop-enabled').checked=config.workshop_enabled!==false;
  $('qs-nexus-enabled').checked=state.nexus_connected===true;
  $('qs-nexus-key').value='';$('qs-nexus-remember').checked=true;
  $('qs-deck-enabled').checked=state.deck_connection?.connected===true;
  $('qs-deck-host').value=config.deck?.host || '';$('qs-deck-password').value='';
  $('qs-deck-remember').checked=state.deck_connection?.password_saved===true;
  $('qs-deck-note').textContent='Connect once to remember the address and find its Steam folders. Sync is reviewed separately in Steam Deck.';
  $('qs-deck-trust').hidden=true;$('qs-bep-download').hidden=true;$('qs-error').hidden=true;
  $('quick-setup').showModal();
  await checkQuickSetup();
  $('qs-folders').open=!quickSetupCheck?.game_found;
  $('qs-support').open=!quickSetupCheck?.loader_installed || ($('qs-workshop-enabled').checked && !quickSetupCheck?.workshop_setup?.installed);
}
function clearQuickTrust(){
  if(quickDeckTrust)quickDeckTrust.password='';quickDeckTrust=null;$('qs-deck-trust').hidden=true;
}
function closeQuickSetup(){clearQuickTrust();$('quick-setup').close();focusAfterWork($('page-'+currentPage).querySelector('h1'));}
function pollQuickWorkshop(){
  clearTimeout(quickWorkshopTimer);
  if(!quickWorkshopWaiting || !$('quick-setup').open || !$('qs-workshop-enabled').checked)return;
  quickWorkshopTimer=setTimeout(async()=>{
    if(working){pollQuickWorkshop();return;}
    try{
      await checkQuickSetup();
      if(quickSetupCheck?.workshop_setup?.state==='ready'){
        quickWorkshopWaiting=false;
        await quickWork('Installing the downloaded Workshop loader…',()=>api('workshop-loader-setup',{}));
      }else if(quickSetupCheck?.workshop_setup?.installed){quickWorkshopWaiting=false;}
      else pollQuickWorkshop();
    }catch(error){quickWorkshopWaiting=false;$('qs-error').textContent=error.message;$('qs-error').hidden=false;}
  },3000);
}
async function connectQuickDeck(trusted=false){
  if(trusted && !quickDeckTrust)return;
  const saved=state.settings.deck || {};
  const target=trusted?quickDeckTrust.deck:{...saved,host:$('qs-deck-host').value.trim(),user:saved.user || 'deck',port:saved.port || 22};
  const credentials=trusted?quickDeckTrust:{deck:target,password:$('qs-deck-password').value,remember_password:$('qs-deck-remember').checked};
  $('qs-deck-password').value='';
  await quickWork('Connecting to your Deck…',async()=>{
    if(!trusted){
      const probe=await api('deck-probe',{deck:target});
      if(!probe.known){
        quickDeckTrust={...credentials,expected_key:probe.key};
        $('qs-deck-fingerprint').textContent=`${probe.host}:${probe.port} · ${probe.algorithm} · ${probe.fingerprint}`;
        $('qs-deck-fingerprint-command').textContent=`sudo ssh-keygen -lf /etc/ssh/ssh_host_${probe.algorithm==='ssh-ed25519'?'ed25519':probe.algorithm==='ssh-rsa'?'rsa':'ecdsa'}_key.pub`;
        $('qs-deck-trust').hidden=false;$('qs-deck-note').textContent='Confirm the Deck’s fingerprint to finish connecting.';
        return;
      }
    }
    try{
      const result=await api('deck-connect',{deck:target,password:credentials.password,remember_password:credentials.remember_password,...(trusted?{expected_key:credentials.expected_key}:{})});
      $('qs-deck-note').textContent=result.credential_warning || (result.discovery?.found?'Connected. Game folders found. Compare and sync from Steam Deck when you’re ready.':result.discovery?.message || 'Connected. Choose folders in Steam Deck → Advanced.');
      clearQuickTrust();
    }catch(error){clearQuickTrust();throw error;}finally{credentials.password='';}
  });
}
$('quick-setup-open').onclick=()=>work('Checking your setup…',openQuickSetup);
for(const id of ['quick-setup-close','qs-later'])$(id).onclick=closeQuickSetup;
$('quick-setup').addEventListener('close',()=>{clearTimeout(quickSetupTimer);clearTimeout(quickWorkshopTimer);quickWorkshopWaiting=false;quickSetupRevision++;clearQuickTrust();$('qs-nexus-key').value='';$('qs-deck-password').value='';});
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-qs-browse]');if(!button)return;
  quickWork('Choose a folder…',async()=>{const result=await api('browse',{kind:'folder'});if(result.path)$(button.dataset.qsBrowse).value=result.path;});
});
for(const id of ['qs-game','qs-workshop','qs-import'])$(id).addEventListener('input',()=>{
  quickSetupRevision++;quickSetupCheck=null;renderQuickSetup();clearTimeout(quickSetupTimer);
  quickSetupTimer=setTimeout(()=>checkQuickSetup().catch(error=>{$('qs-error').textContent=error.message;$('qs-error').hidden=false;}),450);
});
for(const id of ['qs-workshop-enabled','qs-nexus-enabled','qs-deck-enabled'])$(id).addEventListener('change',()=>{renderQuickSetup();if(id==='qs-workshop-enabled')pollQuickWorkshop();if(id==='qs-deck-enabled' && !$(id).checked)clearQuickTrust();});
for(const id of ['qs-nexus-key','qs-deck-password','qs-deck-host'])$(id).addEventListener('input',()=>{if(id.startsWith('qs-deck'))clearQuickTrust();renderQuickSetup();});
$('qs-detect').onclick=()=>quickWork('Finding Steam folders…',async()=>{const result=await api('discover',{});if(result.game)$('qs-game').value=result.game;if(result.workshop)$('qs-workshop').value=result.workshop;});
$('qs-folders-save').onclick=()=>quickWork('Confirming folders…',async()=>{await saveQuickFolders();$('qs-folders').open=false;$('qs-support').open=true;});
$('qs-bep-install').onclick=()=>quickWork('Preparing BepInEx…',async()=>{
  await saveQuickFolders();const result=await api('setup',{});
  if(result.requires_download){$('qs-bep-download-message').textContent=result.message || 'Choose the BepInEx bundle ZIP from Nexus #48.';$('qs-bep-download').hidden=false;}
  else{$('qs-bep-download').hidden=true;toast('BepInEx installed. Original files backed up.');}
});
$('qs-bep-zip').onclick=()=>quickWork('Choose the BepInEx bundle ZIP…',async()=>{const selected=await api('browse',{kind:'zip'});if(!selected.path)return;await saveQuickFolders();const result=await api('setup',{archive:selected.path});if(result.requires_download){$('qs-bep-download-message').textContent=result.message || 'Choose the complete BepInEx bundle ZIP from Nexus #48.';$('qs-bep-download').hidden=false;}else{$('qs-bep-download').hidden=true;toast('BepInEx installed. Original files backed up.');}});
$('qs-subscribe').onclick=()=>quickWork('Opening the Workshop loader in Steam…',async()=>{await saveQuickFolders();await api('steam-workshop',{workshop_id:'3807346541'});quickWorkshopWaiting=true;pollQuickWorkshop();});
$('qs-workshop-install').onclick=()=>quickWork('Checking Steam and installing the loader…',async()=>{await saveQuickFolders();const result=await api('workshop-loader-setup',{});quickWorkshopWaiting=!result.installed;pollQuickWorkshop();toast(result.message || 'Workshop setup checked.');});
$('qs-nexus-connect').onclick=()=>quickWork('Validating the Nexus key…',async()=>{const key=$('qs-nexus-key').value.trim(),remember_key=$('qs-nexus-remember').checked;if(!key)throw new Error('Paste a Nexus key, or leave Nexus unchecked.');$('qs-nexus-key').value='';await api('nexus-connect',{key,remember_key});});
$('qs-deck-connect').onclick=()=>connectQuickDeck();
$('qs-deck-trust-confirm').onclick=()=>connectQuickDeck(true);
$('qs-deck-trust-cancel').onclick=()=>{clearQuickTrust();$('qs-deck-note').textContent='Connection cancelled. No password was sent.';renderQuickSetup();};
$('qs-finish').onclick=()=>quickWork('Finishing Quick Setup…',async()=>{renderQuickSetup();if($('qs-finish').disabled)throw new Error($('qs-finish-note').textContent);await saveQuickFolders();await api('quick-setup-complete',{workshop_enabled:$('qs-workshop-enabled').checked});closeQuickSetup();page('mods');toast('Setup complete. Drop a ZIP or browse Nexus to add mods.');});

let fileDragDepth=0;
function zipDropFiles(event){return Array.from(event.dataTransfer?.files || []);}
document.addEventListener('dragenter',event=>{if(!Array.from(event.dataTransfer?.types || []).includes('Files'))return;event.preventDefault();fileDragDepth++;if(!working && !$('quick-setup').open && !$('dialog').open)$('drop-zone').hidden=false;});
document.addEventListener('dragover',event=>{if(Array.from(event.dataTransfer?.types || []).includes('Files')){event.preventDefault();event.dataTransfer.dropEffect='copy';}});
document.addEventListener('dragleave',()=>{fileDragDepth=Math.max(0,fileDragDepth-1);if(!fileDragDepth)$('drop-zone').hidden=true;});
document.addEventListener('drop',event=>{
  event.preventDefault();fileDragDepth=0;$('drop-zone').hidden=true;
  if(working || $('quick-setup').open || $('dialog').open){toast('Finish the current setup or review before dropping mods.',true);return;}
  const files=zipDropFiles(event);
  if(!files.length || files.some(file=>!/\.zip$/i.test(file.name))){toast('Drop mod ZIP files. Other file types are not installed.',true);return;}
  if(!window.chrome?.webview?.postMessageWithAdditionalObjects){toast('This runtime cannot read dropped files. Choose Install ZIP instead.',true);return;}
  window.chrome.webview.postMessageWithAdditionalObjects('GK2MT:zip-drop',files);
});
window.addEventListener('gk2mt-drop',event=>{
  if(event.detail?.error){toast(event.detail.error,true);return;}
  if(working || $('quick-setup').open || $('dialog').open){toast('Finish the current setup or review before dropping mods.',true);return;}
  work('Checking dropped mod ZIPs…',()=>stagePaths(event.detail?.paths || []));
});

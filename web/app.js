'use strict';
const $ = id => document.getElementById(id);
const bridgeReady = new Promise(resolve => {
  if(window.pywebview?.api?.request)resolve();
  else window.addEventListener('pywebviewready',resolve,{once:true});
});
let state, comparison, currentPage = 'mods', toastTimer, working = false;
const escapeHTML = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const esc = escapeHTML;
const sourceLabel = mod => mod.workshop_id || mod.source === 'Steam Workshop' ? 'Steam' : mod.nexus_mod_id ? 'Nexus' : 'Manual';
const sourceClass = mod => sourceLabel(mod).toLowerCase();
function actionCard(id,number,label,status,description,disabled=false){
  const button=$(id);
  button.innerHTML=`<span class="guided-step-marker" aria-hidden="true">${status==='done'?'✓':number}</span><span class="deck-action-copy"><strong>${esc(label)}</strong><span>${esc(description)}</span><span class="sr-only">${status==='done'?'Complete':status==='action'?'Needs action':'Upcoming'}</span></span>`;
  button.disabled=disabled;button.classList.toggle('primary',false);button.classList.toggle('guided-step',true);
  for(const name of ['done','action','waiting'])button.classList.toggle(name,status===name);
}
function toast(message, error=false) {
  $('toast').textContent = message; $('toast').hidden=false; $('toast').classList.toggle('error',error);
  clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,error?14000:6500);
}
async function api(action, body = null) {
  await bridgeReady;
  const response=await window.pywebview.api.request(action,body);
  if(response?.ok!==true)throw new Error(response?.error || 'Request failed.');
  return response.result;
}
async function work(message, task) {
  if(working)return null;
  working=true;
  $('busy').hidden=false; $('busy').querySelector('span').textContent=message;
  const regions=[...document.querySelectorAll('main, .sidebar, #dialog, #quick-setup')].map(element=>[element,element.inert]);
  regions.forEach(([element])=>element.inert=true);
  document.body.classList.add('working');
  try { return await task(); } catch(error) { toast(error.message,true); return null; }
  finally { $('busy').hidden=true; regions.forEach(([element,inert])=>element.inert=inert);document.body.classList.remove('working');working=false;const target=pendingFocus;pendingFocus=null;target?.focus({preventScroll:true}); }
}
let pendingFocus=null;
function focusAfterWork(element){if(!element)return;element.tabIndex=-1;if(working)pendingFocus=element;else element.focus({preventScroll:true});}
function page(name) {
  const changed=currentPage!==name;
  currentPage=name; document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!=='page-'+name);
  $('download-status').hidden=!['updates','browse','profiles'].includes(name);
  document.querySelectorAll('[data-page]').forEach(b=>{b.classList.toggle('active',b.dataset.page===name);if(b.dataset.page===name)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $('breadcrumb').textContent={mods:'My mods',browse:'Browse Nexus',profiles:'Mod profiles',updates:'Updates',rules:'Conflict rules',deck:'Steam Deck',settings:'Locations & setup'}[name];
  location.hash=name;
  if(changed){window.scrollTo({top:0,left:0,behavior:'instant'});focusAfterWork($('page-'+name).querySelector('h1'));}
}
function modal(title, html, actions=[]) {
  $('dialog-title').textContent=title; $('dialog-body').innerHTML=html; $('dialog-actions').replaceChildren();
  for(const [label, callback, style, disabledReason] of actions){const button=document.createElement('button');button.textContent=label;button.className=style===true?'primary':style || '';button.disabled=!!disabledReason;if(disabledReason)button.title=disabledReason;button.addEventListener('click',callback);$('dialog-actions').append(button);}
  if(!$('dialog').open)$('dialog').showModal();
}
function fields(config) {
  $('game-path').value=config.game || ''; $('workshop-path').value=config.workshop || ''; $('import-path').value=config.import_folder || '';
  deckFieldsFill(config.deck);
  $('data-path').textContent='Settings, downloads, and original file backups: '+state.data_path;
}
async function refresh(fill=false,scan=false){state=await api(scan?'rescan':'state',scan?{force_workshop_titles:!fill}:null);if(fill)fields(state.settings);render();const warnings=[state.workshop_title_warning,state.nexus_discovery_warning].filter(Boolean);if(warnings.length)toast(warnings.join(' '),true);}
function render() {
  $('nav-count').textContent=state.mods.length; $('total').textContent=state.mods.length;
  $('enabled').textContent=state.mods.filter(m=>m.enabled).length;
  $('workshop-count').textContent=new Set(state.mods.filter(m=>m.workshop_id).map(m=>m.workshop_id)).size;
  $('conflict-count').textContent=state.conflicts.filter(conflict=>conflict.identical!==true).length;
  $('loader-status').textContent=state.loader_installed?'BepInEx installed':'BepInEx not detected';
  $('loader-status').classList.toggle('missing',!state.loader_installed);
  $('foundation-banner').hidden=!state.game_found || state.loader_installed;
  $('foundation-banner').classList.toggle('missing',!state.loader_installed);
  $('foundation-message').textContent=state.loader_installed?'BepInEx detected. Your modding foundation is installed.':'BepInEx is missing or incomplete. Install it to load your mods.';
  $('foundation-install').hidden=state.loader_installed;
  $('foundation-install').disabled=!state.game_found;
  $('foundation-note').hidden=state.loader_installed;
  renderWorkshopSetup();
  $('notice').hidden=state.game_found && state.locations_confirmed;
  $('notice').textContent=state.game_found?'Confirm the detected folders in Quick Setup to get started.':'Open Quick Setup to find the game and prepare mod support.';
  const category=$('category-filter').value;
  $('category-filter').innerHTML='<option value="">All categories</option>'+[...new Set(state.mods.map(m=>m.category || 'Uncategorized'))].sort().map(c=>`<option>${esc(c)}</option>`).join('');
  $('category-filter').value=category;
  renderMods();renderDuplicates();renderRules();renderUpdates();renderDeckConnection();
}
function renderWorkshopSetup(){
  const setup=state.workshop_setup || {},installed=setup.installed===true,ready=state.game_found && state.loader_installed;
  const canRefresh=ready && ['ready','waiting_workshop'].includes(setup.state);
  const message=setup.message || 'Refresh the mod inventory to check Workshop loader setup.';
  $('workshop-setup-banner').hidden=!ready || installed || state.settings?.workshop_enabled===false;
  $('workshop-setup-title').textContent=['blocked','disabled'].includes(setup.state)?'Workshop loader needs attention':'Set up Steam Workshop mods';
  $('workshop-setup-message').textContent=message;
  $('workshop-loader-status').textContent=message;
  $('workshop-loader-status').classList.toggle('green',installed);
  $('workshop-loader-install').hidden=installed || ['blocked','disabled'].includes(setup.state);
  $('workshop-loader-install').disabled=!canRefresh;
  $('workshop-loader-install').title=canRefresh?'':message;
  $('workshop-subscribe').hidden=installed || ['blocked','disabled'].includes(setup.state);
  $('workshop-subscribe').disabled=!canRefresh;
  $('workshop-subscribe').title=canRefresh?'':message;
  $('workshop-loader-mods').hidden=!ready || !['blocked','disabled'].includes(setup.state);
  $('workshop-loader-mods').textContent=setup.state==='disabled'?'Enable the existing loader in My mods':'Review existing loaders in My mods';
}
async function subscribeWorkshopLoader(){return work('Opening the Workshop loader in Steam…',async()=>{const result=await api('steam-workshop',{workshop_id:'3807346541'});toast(result.message || 'Subscribe in Steam, wait for the download, then select Refresh & install loader.');});}
async function installWorkshopLoader(){return work('Checking the Steam download and setting up the Workshop loader…',async()=>{
  let result;try{result=await api('workshop-loader-setup',{});}finally{await refresh(false,true);await refreshSelectedProfile();}
  toast(result.message || 'Workshop loader setup checked. Launch the game to confirm Workshop mods load.');
});}
function modDuplicates(id){return (state.duplicates || []).filter(group=>(group.row_ids || []).includes(id));}
function duplicateMessage(group){return group.enabled_count>1?'Multiple copies are enabled. Disable or uninstall the extra copy before launching.':group.disabled_count?'A disabled duplicate is still installed.':'Another installed copy matches this mod.';}
function renderDuplicates(){
  const groups=state.duplicates || [];$('duplicates-banner').hidden=!groups.length;
  $('duplicates-banner').innerHTML=groups.map(group=>`<div class="duplicate-group"><div><strong class="${group.enabled_count>1?'warning':'gold'}">${group.enabled_count>1?'Multiple copies enabled':'Duplicate copies found'}</strong><p class="small muted">${esc(duplicateMessage(group))} ${esc(group.reason || '')}</p></div><div class="actions">${(group.row_ids || []).map(id=>state.mods.find(mod=>mod.id===id)).filter(Boolean).map(mod=>`<button data-details="${esc(mod.id)}">${esc(mod.name)} <span class="small muted">· ${sourceLabel(mod)} · ${mod.enabled?'enabled':'disabled'}</span></button>`).join('')}</div></div>`).join('');
}
function renderMods(){
  const query=$('search').value.toLowerCase(),source=$('source-filter').value,status=$('state-filter').value,category=$('category-filter').value;
  const rows=state.mods.filter(m=>(!query || `${m.name} ${sourceLabel(m)} ${m.workshop_id || ''}`.toLowerCase().includes(query))&&(!source||sourceLabel(m)===source)&&(!status||m.enabled===(status==='enabled'))&&(!category||(m.category||'Uncategorized')===category));
  $('list-count').textContent=rows.length; $('empty').hidden=rows.length>0;
  $('filtered-count').textContent=`Showing ${rows.length} of ${state.mods.length}.`;
  $('empty-message').textContent=state.mods.length?'No mods match these filters.':'Your library is ready. Drop a mod ZIP, or choose Install ZIP to add your first mod.';
  $('clear-filters').hidden=!state.mods.length;
  $('mod-list').innerHTML=rows.map(m=>`<tr><td><input type="checkbox" aria-label="Enable ${esc(m.name)}" data-toggle="${esc(m.id)}" ${m.enabled?'checked':''} ${m.can_toggle===false?'disabled':''}></td><td class="mod-name"><div class="mod-entry"><span class="mod-icon"><svg class="icon" aria-hidden="true"><use href="#i-${sourceLabel(m)==='Steam'?'workshop':'star'}"/></svg></span><div class="mod-copy"><span class="mod-title">${esc(m.name)}</span><span class="mod-sub">${m.workshop_id?'Workshop #'+esc(m.workshop_id):m.nexus_mod_id?'Nexus #'+esc(m.nexus_mod_id):esc(m.paths?.[0]?.split('/').slice(0,2).join('/') || 'Local package')}</span>${modDuplicates(m.id).length?`<span class="mod-sub warning">Duplicate copy${modDuplicates(m.id).some(group=>group.enabled_count>1)?' · multiple enabled':' · extra copy installed'}</span>`:''}</div></div></td><td class="muted">${esc(m.display_version ?? m.version ?? 'Unknown')}</td><td class="muted">${esc(m.category || 'Uncategorized')}</td><td><span class="badge ${sourceClass(m)}">${sourceLabel(m)}</span></td><td><span class="status ${!m.enabled?'off':''}">${esc(m.status || (m.enabled?'Enabled':'Disabled'))}</span></td><td class="right"><button class="row-action" data-details="${esc(m.id)}" aria-label="Details for ${esc(m.name)}"><svg class="icon" aria-hidden="true"><use href="#i-more"/></svg></button></td></tr>`).join('');
}
function details(id){
  const m=state.mods.find(m=>m.id===id);if(!m)return;
  const input=(key,label,value,type='text')=>`<label>${label}<input id="detail-${key}" type="${type}" value="${esc(value || '')}"></label>`;
  const removable=sourceLabel(m)!=='Steam',blocked=removable && m.can_uninstall!==true?(m.uninstall_reason || 'No removable mod files were detected.'):'';
  const pluginVersion=m.plugin_version ?? m.version;
  const releaseVersion=m.display_version && m.display_version!==pluginVersion?`<p class="footnote">Installed release: <strong>${esc(m.display_version)}</strong>. The plugin reports ${esc(pluginVersion || 'Unknown')}; update checks use the release version.</p>`:'';
  const warnings=[...new Set([...(m.warnings || []),...modDuplicates(id).map(group=>duplicateMessage(group)+' '+(group.reason || ''))])];
  modal(m.name,`<p><span class="badge ${sourceClass(m)}">${sourceLabel(m)}</span> &nbsp; ${esc(m.status || '')}</p>${warnings.length?`<ul class="warning">${warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}<div class="form-grid">${input('name','Mod name',m.name)}${input('version','Version',m.display_version ?? m.version)}${input('category','Category',m.category)}${input('nexus_mod_id','Nexus mod ID (optional)',m.nexus_mod_id,'number')}${input('file_id','Nexus file ID (optional, preserves release variant)',m.file_id,'number')}</div>${releaseVersion}<p class="footnote">Metadata is editable. An unknown version is never assumed up to date.</p><details><summary>Installed file paths (${m.paths?.length || 0})</summary><pre>${esc((m.paths||[]).join('\n') || 'No deployed files detected.')}</pre></details>${m.workshop_id?`<p><a href="https://steamcommunity.com/sharedfiles/filedetails/?id=${encodeURIComponent(m.workshop_id)}" target="_blank" rel="noreferrer">View Workshop item ↗</a></p>`:''}${m.nexus_mod_id?`<p><a href="https://www.nexusmods.com/graveyardkeeper2/mods/${Number(m.nexus_mod_id)}" target="_blank" rel="noreferrer">View Nexus mod ↗</a></p>`:''}${blocked?`<p class="warning">${esc(blocked)}</p>`:''}`,[['Cancel',()=> $('dialog').close()],...(removable?[['Uninstall mod',()=>{if(!blocked)return uninstallPreview(id);},'danger',blocked]]:[]),['Save details',()=>work('Saving mod details…',async()=>{const body={id};for(const k of ['name','version','category','nexus_mod_id','file_id'])body[k]=$('detail-'+k).value;await api('metadata',body);$('dialog').close();await refresh();toast('Mod details saved.');}),true]]);
}
function uninstallPreview(id){return work('Preparing uninstall preview…',async()=>{
  const preview=await api('uninstall-preview',{id});
  const paths=(label,files)=>files?.length?`<h3>${label} (${files.length})</h3><pre>${esc(files.join('\n'))}</pre>`:'';
  modal('Review mod uninstall',`<p>Uninstall <strong>${esc(preview.name)}</strong>?</p><p class="muted">Close the game and avoid deploying from Vortex during removal. GK2MT backs up files before changing them.</p>${paths('Files to remove',preview.removed ?? preview.files)}${paths('Files to restore',preview.restored)}${paths('Files to preserve',preview.preserved)}${preview.warnings?.length?`<ul class="warning">${preview.warnings.map(warning=>`<li>${esc(warning)}</li>`).join('')}</ul>`:''}<p class="footnote">The backup is kept in GK2MT’s data folder so removed files can be recovered.</p>`,[['Cancel',()=>$('dialog').close()],['Uninstall mod',()=>work('Backing up and uninstalling the mod…',async()=>{
    let result;
    try{result=await api('uninstall',{token:preview.token});}catch(error){$('dialog').close();throw error;}
    $('dialog').close();await refresh();toast(result.message);
    if(result.backup)modal('Mod uninstalled',`<p>${esc(result.message)}</p><p class="footnote">Files backed up to:</p><pre>${esc(result.backup)}</pre>`,[['Done',()=>$('dialog').close(),true]]);
  }),'danger']]);
});}
function renderRules(){
  const packages=state.mods.filter(m=>m.source==='GK2MT');const options=packages.map(m=>`<option value="${esc(m.id)}">${esc(m.name)}</option>`).join('');
  $('rule-first').innerHTML=options;$('rule-second').innerHTML=options;if(packages.length>1)$('rule-second').selectedIndex=1;
  const name=id=>state.mods.find(m=>m.id===id)?.name || id;
  const conflicts=state.conflicts.filter(conflict=>conflict.identical!==true),shared=state.conflicts.filter(conflict=>conflict.identical===true);
  $('rules-list').innerHTML='<h2>Your rules</h2>'+(state.rules.length?state.rules.map((r,i)=>`<div class="item-line"><span>${esc(name(r.after))} <strong class="gold">after</strong> ${esc(name(r.before))}</span><button data-remove-rule="${i}">Remove</button></div>`).join(''):'<p class="muted">No rules yet. Import ZIP packages to manage their file conflicts.</p>');
  $('conflicts-list').innerHTML=(conflicts.length?conflicts.map(c=>`<div class="item-line"><div><code>${esc(c.path)}</code><div class="footnote">${c.packages.map(name).map(esc).join(' → ')}</div></div><span class="badge">${esc(name(c.winner))} wins</span></div>`).join(''):'<p class="muted">No differing file overlaps between enabled imported packages.</p>')+(shared.length?`<details class="import-shared"><summary>${shared.length} identical shared file${shared.length===1?'':'s'} · no file conflict</summary><p class="small muted">These enabled packages contain the same bytes and share the files.</p>${shared.map(c=>`<div class="import-shared-file"><code class="import-path">${esc(c.path)}</code><div class="footnote">${c.packages.map(name).map(esc).join(' · ')}</div></div>`).join('')}</details>`:'')+'<p class="footnote">Installed file overlaps only. Mods can still conflict when the game runs.</p>';
}
function updateReason(update){
  if(update.blocked_reason)return update.blocked_reason;
  if(update.choices?.length)return 'Choose the matching release on Nexus, then install its ZIP.';
  if(update.known_version!==true)return 'The installed or available version is unknown. Automatic update is skipped.';
  if(!update.file_id)return 'Choose a matching Nexus file before updating.';
  if(!update.downloadable)return update.reason || (state.updates?.account?.premium===false?'Automatic updates require Nexus Premium. Download on Nexus, then install the ZIP.':update.status || 'This package cannot be updated automatically.');
  return '';
}
const activeDownloadStates = new Set(['starting','waiting','downloading','verifying','installing','awaiting_review']);
let updateConnectionsNeed=null, lastDownloadLog='';
let downloadTimer, pollingDownloads=false;
function renderNexusBrowser(){
  const connected=!!state.nexus_connected,panel=state.download_panel,pending=(state.downloads || []).some(job=>activeDownloadStates.has(job.state) || job.state==='waiting_game');
  const reason=!connected?'Connect Nexus on Updates first so GK2MT can verify downloaded mods.':!panel?.available?panel?.message || 'The Nexus browser is unavailable.':state.game_found===false?'Choose your game folder in Locations & setup first.':pending?'Finish, retry, or cancel the current download below before opening another session.':'';
  $('nexus-browser-open').disabled=!!reason;
  $('nexus-browser-open').title=reason;
  $('nexus-browser-connect').hidden=connected;
  $('nexus-browser-status').textContent=reason || 'Browse, search, and filter the official Nexus site here. Choose Manual Download, then Slow Download if using a Free account. GK2MT installs the verified ZIP and adds the mod to My mods.';
}
async function openNexusBrowser(){return work('Opening the Nexus mod browser…',async()=>{await api('nexus-browser',{});await refresh();await pollDownloads();});}
function renderUpdates(){
  const result=state.updates,updates=result?.updates || [];
  const connected=!!state.nexus_connected,saved=!!state.nexus_saved,account=result?.account,panel=state.download_panel;
  $('nexus-connect').textContent=saved?'Replace key':'Connect Nexus';
  $('nexus-forget').hidden=!(saved || connected);
  const connectionsNeed=!connected || !!state.nexus_error;
  if(updateConnectionsNeed!==connectionsNeed){$('updates-connections').open=connectionsNeed;updateConnectionsNeed=connectionsNeed;}
  $('connection-summary').textContent=connectionsNeed?(state.nexus_error?'Nexus needs attention':'Connect Nexus to check updates'):`Nexus connected${account?' · '+(account.premium?'Premium':'Free'):''} · Steam handles Workshop`;
  $('nexus-remember-option').hidden=!$('nexus-key').value.trim();
  $('nexus-status').textContent=state.nexus_error || (!connected?'Not connected. Paste your key once to connect Nexus.':account?`Connected: ${account.name || 'Nexus user'} · ${account.premium?'Premium · automatic downloads available':panel?.available?'Free account · use the GK2MT download panel':'Free account · update from a downloaded ZIP'}${saved?' · Key saved securely':''}`:saved?'Saved key restored. Check for updates to verify your account and find available releases.':'Connected. Check for updates to refresh your account and available releases.');
  $('check-updates').disabled=!connected;
  $('check-updates').title=connected?'':'Connect Nexus first to check for updates.';
  $('update-count').textContent=updates.length || '';
  const automatic=connected && updates.some(update=>!updateReason(update));
  $('download-updates').disabled=!automatic;
  $('updates-help').textContent=!connected?'Connect Nexus above to check for updates. Your key will be remembered securely on this Windows account.':account?.premium===false?`Free accounts approve each download${panel?.available?' in the GK2MT panel':''}. Download & update all requires Premium. Unknown versions and unresolved releases are skipped.`:automatic?'Download & update all installs the eligible releases and backs up replaced files. Unknown versions and unresolved releases are skipped. Close the game first.':'No releases are ready for automatic updating. Check for updates, or review the file/version details below.';
  if(state.updates?._stale)$('updates-help').textContent='Installed mods changed since this check; check for updates again to refresh the results. '+$('updates-help').textContent;
  renderNexusBrowser();
  renderProfiles();
  renderDownloads();
  if(!result){$('updates-list').innerHTML=`<p class="muted">${connected?'Check for updates to find available releases.':'Connect Nexus, then check for updates.'} Add a mod ID and optional file ID in each mod’s details to track a specific release.</p>`;return;}
  const errors=[...(result.errors || []),...(result.metadata_errors || [])];
  $('updates-list').innerHTML=(updates.length?updates.map(u=>{
    const reason=updateReason(u),manual=u.manual_installable===true && u.known_version===true && !!u.file_id;
    const job=(state.downloads || []).find(job=>job.update_id===u.id && (activeDownloadStates.has(job.state) || job.state==='waiting_game' || (job.state==='error' && job.retryable)));
    const pending=!!job;
    const zip=`<button data-update-archive="${esc(u.id)}" aria-label="Update ${esc(u.name)} from ZIP">Update from ZIP</button>`;
    const action=!connected?'<button data-nexus-prompt>Connect Nexus</button>':pending?job.state==='waiting_game'?`<button data-download-retry="${esc(job.id)}" class="primary">Game closed · Install</button>`:job.state==='error'?`<button data-download-retry="${esc(job.id)}" class="primary">Retry installation</button>`:job.state==='awaiting_review'?`<button data-download-approve="${esc(job.id)}" class="primary">Review duplicate</button>`:'<span class="badge">Download in progress</span>':!reason?`<button data-update="${esc(u.id)}" class="primary" aria-label="Update ${esc(u.name)}">Update</button>`:manual?`${panel?.available?`<button data-download-panel="${esc(u.id)}" class="primary" aria-label="Download and update ${esc(u.name)}">Download & update</button>`:''}${zip}`:`<button data-details="${esc(u.id)}">Review details</button>`;
    const notes=typeof u.changelog==='string'?u.changelog.trim():'';
    const choices=u.choices || [];
    const release=choices.length?'Choose file':u.version || 'Unknown';
    const notesFallback=choices.length?'Release notes appear once the matching file is identified.':u.changelog_error?'Changelog unavailable.':'No changelog published for this version.';
    const status=pending?(job.state==='waiting_game'?'Downloaded. Close the game, then install.':job.message || 'See download activity below.'):manual && reason==='Nexus Premium is required for automatic downloads'?'':reason;
    return `<div class="item-line update-row"><div><strong>${esc(u.name)}</strong><div class="update-version">${esc(u.installed_version || 'Unknown')} → ${esc(release)}</div>${choices.length?`<ul class="small muted">${choices.map(file=>`<li>${esc(file.name || 'File #'+file.file_id)} · ${esc(file.version || 'Unknown')}</li>`).join('')}</ul>`:''}<details class="update-changelog"><summary>Latest changes${u.changelog_version?' · '+esc(u.changelog_version):''}</summary><p class="small muted changelog-notes">${esc(notes || notesFallback)}</p></details>${status?`<p class="small ${pending && job.state!=='error'?'muted':'warning'}">${esc(status)}</p>`:''}</div><div class="actions update-actions">${action}<a href="${nexusFileURL(u)}" target="_blank" rel="noreferrer">View files ↗</a></div></div>`;
  }).join(''):'<p class="muted">No eligible updates found for the linked mods. Unlinked mods were not checked.</p>')+(errors.length?`<ul class="warning">${errors.map(e=>`<li>${esc(typeof e==='string'?e:JSON.stringify(e))}</li>`).join('')}</ul>`:'');
}
function renderDownloads(){
  const panel=state.download_panel,jobs=state.downloads || [];
  $('update-log').hidden=!jobs.length && !$('download-list').innerHTML.trim();
  $('download-activity').hidden=!jobs.length;
  const active=jobs.filter(job=>activeDownloadStates.has(job.state) || ['waiting_game','error','closed'].includes(job.state));
  $('activity-count').textContent=active.length || '';
  $('download-log-summary').textContent=active.length?`${active.length} in progress or need attention`:jobs.length?`${jobs.length} completed or cancelled`:'Last update results';
  const logSignature=jobs.map(job=>job.id+':'+job.state).join('|');
  if(logSignature!==lastDownloadLog && jobs.length){$('update-log').open=true;lastDownloadLog=logSignature;}
  $('download-panel-info').hidden=!panel;
  $('download-panel-message').textContent=panel?.available?'Choose Manual Download, then Slow Download in the GK2MT panel. Completed ZIPs are checked and installed automatically. Close the game first.':panel?.message || 'The download panel is unavailable. You can still install downloaded ZIPs.';
  $('download-folder').textContent=panel?.folder || '';
  $('download-jobs').hidden=!jobs.length;
  const labels={starting:'Opening panel',waiting:'Waiting for your download',downloading:'Downloading',verifying:'Checking archive',installing:'Installing update',waiting_game:'Close the game to continue',awaiting_review:'Duplicate copy · review first',completed:'Updated',error:'Needs attention',closed:'Panel closed',cancelled:'Cancelled'};
  $('download-jobs').innerHTML=jobs.map(job=>{
    const label=job.kind==='install' && job.state==='completed'?'Installed':job.kind==='install' && job.state==='waiting'?'Browsing Nexus':labels[job.state] || job.state;
    const received=Math.max(0,Number(job.received)||0),total=Math.max(0,Number(job.total)||0),progress=total>0?Math.min(100,Math.floor(received/total*100)):null;
    const moving=['downloading','verifying','installing'].includes(job.state);
    return `<article class="download-job ${job.state==='completed'?'download-complete':job.state==='error'?'download-error':''}"><div class="download-job-heading"><div><strong>${esc(job.name)}</strong>${job.version?`<span class="small muted"> · ${esc(job.version)}</span>`:''}</div><span class="badge">${esc(label)}</span></div><p class="small ${job.state==='error'?'warning':'muted'}">${esc(job.message || label || '')}</p>${moving?`<div class="download-progress"><progress aria-label="${esc(labels[job.state] || 'Download')} ${esc(job.name)}" max="100" ${job.state==='downloading' && progress!==null?`value="${progress}"`:''}></progress>${job.state==='downloading'?`<span>${progress!==null?progress+'%':(received/1048576).toFixed(1)+' MB'}</span>`:''}</div>`:''}${job.retryable || job.cancellable || job.review_required?`<div class="actions">${job.review_required?`<button data-download-approve="${esc(job.id)}" class="primary">Review duplicate</button>`:''}${job.retryable?`<button data-download-retry="${esc(job.id)}" class="primary">Retry installation</button>`:''}${job.cancellable?`<button data-download-cancel="${esc(job.id)}">Cancel</button>`:''}</div>`:''}</article>`;
  }).join('');
  clearTimeout(downloadTimer);
  if(jobs.some(job=>activeDownloadStates.has(job.state) && job.state!=='awaiting_review'))downloadTimer=setTimeout(pollDownloads,1500);
}
async function pollDownloads(){
  if(pollingDownloads)return;
  pollingDownloads=true;
  try{
    const previous=state.downloads || [],result=await api('downloads');
    state.download_panel=result.download_panel;state.downloads=result.downloads || [];
    $('download-poll-error').hidden=true;
    renderUpdates();
    if(state.downloads.some(job=>job.state==='completed' && !previous.some(old=>old.id===job.id && old.state==='completed'))){await refresh();await refreshSelectedProfile();}
  }catch(error){
    $('download-poll-error').textContent='Could not read download status. '+error.message;
    $('download-poll-error').hidden=false;
    renderDownloads();
  }finally{pollingDownloads=false;}
}
async function downloadPanelAction(action,id,button,acknowledge=false){
  if(button)button.disabled=true;
  try{
    const job=await api(action,{id,...(acknowledge?{acknowledge_duplicates:true}:{})});
    if(job?.id)state.downloads=[...(state.downloads || []).filter(old=>old.id!==job.id),job];
    renderUpdates();
    if(job?.state==='completed'){await refresh();await refreshSelectedProfile();}
    else await pollDownloads();
  }catch(error){toast(error.message,true);await pollDownloads();}
  finally{if(button)button.disabled=false;}
}
function runUpdates(id){return work('Downloading and installing updates…',async()=>{
  const result=await api(id===undefined?'download-updates':'update',id===undefined?{}:{id});
  await refresh();await refreshSelectedProfile();
  showUpdateResult(result);
});}
function showUpdateResult(result){
  const updated=result.updated || [],skipped=result.skipped || [],errors=result.errors || [],warnings=result.warnings || [];
  $('download-list').innerHTML=`<article class="card" role="status"><h2>${updated.length} mod${updated.length===1?'':'s'} updated</h2>${updated.length?`<ul>${updated.map(mod=>`<li>${esc(mod.name)} · ${esc(mod.version)}</li>`).join('')}</ul>`:''}${skipped.length?`<h3>Skipped</h3><ul class="muted">${skipped.map(mod=>`<li>${esc(mod.name)}: ${esc(mod.reason)}</li>`).join('')}</ul>`:''}${errors.length?`<h3>Could not update</h3><ul class="warning">${errors.map(error=>`<li>${esc(error)}</li>`).join('')}</ul>`:''}<p class="footnote">Updates take effect on the next game launch. Original files are backed up.</p></article>`;
  if(warnings.length)$('download-list').insertAdjacentHTML('beforeend',`<article class="card"><h3>Update notes</h3><ul class="warning">${warnings.map(warning=>`<li>${esc(warning)}</li>`).join('')}</ul></article>`);
  toast(result.message || `${updated.length} mod(s) updated.`,errors.length>0);
  $('update-log').hidden=false;$('update-log').open=true;
  $('download-list').tabIndex=-1;$('download-list').focus?.({preventScroll:true});$('download-list').scrollIntoView?.({block:'nearest'});
}
function nexusFileURL(update){return `https://www.nexusmods.com/graveyardkeeper2/mods/${Number(update.nexus_mod_id)}?tab=files${update.file_id?'&file_id='+Number(update.file_id):''}`;}
function updateArchive(id){
  const update=state.updates?.updates?.find(item=>item.id===id);if(!update || update.manual_installable!==true)return;
  modal(`Update ${update.name} from ZIP`,`<p>${state.updates.account?.premium===false?'Nexus Free accounts download files in the browser. ':''}Download <strong>${esc(update.name)} · ${esc(update.version)}</strong> from the linked Nexus release, then choose that ZIP below.</p><p><a href="${nexusFileURL(update)}" target="_blank" rel="noreferrer">Download this release on Nexus ↗</a></p><label>Downloaded ZIP path<input id="update-archive-path" placeholder="Paste the full path to the downloaded ZIP"></label><p class="footnote">GK2MT checks that the archive matches this mod before showing the changes. Original files are backed up when you install.</p>`,[
    ['Cancel',()=>$('dialog').close()],
    ['Browse…',()=>work('Choose the downloaded update ZIP…',async()=>{const result=await api('browse',{kind:'zip'});if(result.path)$('update-archive-path').value=result.path;})],
    ['Review update',()=>work('Checking the update archive…',async()=>{
      const path=$('update-archive-path').value.trim();if(!path)throw new Error('Choose the downloaded ZIP first.');
      const preview=await api('update-archive-preview',{id,path});
      modal('Review mod update',`<h2>${esc(preview.name)} · ${esc(preview.version)}</h2><p>${Number(preview.files)} files in the update.</p>${preview.conflicts?.length?`<details><summary>${preview.conflicts.length} existing files will be replaced</summary><pre>${esc(preview.conflicts.join('\n'))}</pre></details>`:''}${preview.warnings?.length?`<ul class="warning">${preview.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}<p class="footnote">Close the game before installing. Replaced files are backed up and the mod’s enabled state is preserved.</p>`,[['Cancel',()=>$('dialog').close()],['Install update',()=>work('Installing the mod update…',async()=>{const result=await api('update-archive',{token:preview.token});$('dialog').close();await refresh();showUpdateResult(result);}),true]]);
    }),true]
  ]);
}
let selectedProfile='',profileComparison=null,profileError='',profileLoading=false,profileRevision=0,profileCompletion=null;
const profileWorkshopPending=new Set();
const profileIssue=issue=>typeof issue==='string'?issue:issue?.reason || issue?.name || 'This mod could not be resolved.';
const profileReleaseSignature=result=>JSON.stringify((result.mismatches || []).map(entry=>[entry.key,entry.installed_version,entry.installed_file_id]));
function profileApplied(result=profileComparison){return result?.id===selectedProfile && profileCompletion?.id===selectedProfile && !result.blockers?.length && !(result.changes || []).some(change=>!change.enabled || !profileCompletion.pending?.some(item=>profileIssue(item)===change.name)) && profileCompletion.releaseSignature===profileReleaseSignature(result);}
function revealProfileMods(){const list=$('profile-entries'),target=list.querySelector('.guided-action') || list.querySelector('h2') || list;target.scrollIntoView({block:'nearest',behavior:'instant'});focusAfterWork(target);}
function newProfile(){modal('Save a mod profile','<p class="muted small">Save the currently enabled mod set so you can switch back or share it later.</p><label>Profile name<input id="profile-name" placeholder="For example, Cozy farming" maxlength="100" autocomplete="off"></label><p class="footnote">This saves a list of mods and enabled states. Mod files and configurations stay shared.</p>',[['Cancel',()=>$('dialog').close()],['Save profile',()=>saveProfile(),true]]);$('profile-name').focus();}
function renderProfiles(){
  const profiles=state.profiles || [];
  const pending=(state.downloads || []).some(job=>activeDownloadStates.has(job.state) || job.state==='waiting_game');
  if(!profiles.some(profile=>profile.id===selectedProfile)){selectedProfile=profiles.find(profile=>profile.id===state.active_profile)?.id || profiles[0]?.id || '';profileComparison=null;profileError='';}
  $('profile-select').innerHTML=profiles.length?profiles.map(profile=>`<option value="${esc(profile.id)}">${esc(profile.name)}${profile.active?' · last applied':''}</option>`).join(''):'<option value="">No saved profiles yet</option>';
  $('profile-select').value=selectedProfile;$('profile-select').disabled=!profiles.length;
  for(const id of ['profile-replace','profile-export','profile-delete'])$(id).disabled=!selectedProfile || profileLoading;
  const profile=profiles.find(item=>item.id===selectedProfile),result=!profileLoading && !profileError && profileComparison?.id===selectedProfile?profileComparison:null;
  const blockers=result?.blockers || [],mismatches=result?.mismatches || [],entries=result?.entries || [];
  const missing=entries.filter(entry=>entry.enabled && entry.status==='missing'),applied=!!result && profileApplied(result),accepted=applied && profileCompletion.acceptedVersions;
  const needsMods=blockers.length>0 || (mismatches.length>0 && !accepted),approval=applied && !!profileCompletion.pending?.length;
  const steamWaiting=missing.some(entry=>entry.workshop_id && profileWorkshopPending.has(String(entry.workshop_id)));
  $('profile-selection').hidden=!profiles.length;$('profile-workflow').hidden=!selectedProfile;$('profile-options').hidden=!selectedProfile;
  $('profile-summary').textContent=profile?`${Number(profile.mod_count) || 0} gameplay mods · ${Number(profile.enabled_count) || 0} enabled`:'Create a profile from your current mod setup, or import one shared with you.';
  actionCard('profile-refresh',1,profileLoading?'Checking…':steamWaiting?'Refresh Steam mods':result?'Check again':'Check mods',result && !steamWaiting?'done':selectedProfile && !profileLoading?'action':'waiting',profileLoading?'Checking installed mods…':steamWaiting?'Finish Steam Downloads, then check here':result?`${entries.length} profile mods checked`:profileError?'Check failed · try again':'Compare this profile with installed mods',!selectedProfile || profileLoading);
  actionCard('profile-install',2,!result || needsMods?'Get mods ready':'Mods ready',!result?'waiting':needsMods?'action':'done',!result?'Unlocks after checking mods':blockers.length?`${missing.length} missing · review highlighted mods`:accepted?'You accepted the installed releases':mismatches.length?`${mismatches.length} release differences · review below`:'All required mods and releases found',!result || !needsMods);
  actionCard('profile-apply',3,approval?'Launch to approve':applied?'Profile applied':'Review & apply',!result || blockers.length || pending?'waiting':approval || !applied?'action':'done',!result?'Check mods first':pending?'Finish the current download first':approval?'Approve Workshop mods in the game':applied?'Enabled states match this profile':blockers.length?'Get the required mods ready first':mismatches.length?'Review releases and enabled state changes':'Review enabled state changes before applying',!result || !!blockers.length || pending || (applied && !approval));
  $('profile-install').title='Show missing mods, ambiguous copies, and release differences below. Choose the install action for each mod.';
  $('profile-apply').title=pending?'Finish, retry, or cancel the current download before applying a profile.':!result?'Check mods first.':blockers.length?'Get the required mods ready first.':approval?'Launch the game and approve the listed Workshop mods.':'';
  $('profile-status').textContent=profileLoading?'Checking this profile…':profileError?profileError+' Use Check mods to try again.':!result?'Choose Check mods to see what this profile needs.':steamWaiting?'Finish the Steam download, then use Refresh Steam mods above.':pending?'A download needs attention. Finish, retry, or cancel it in Download activity & results below.':blockers.length?'Install or resolve the highlighted mods below, then check again.':approval?'Profile applied. Launch to approve Workshop mods, then return and Check again.':applied?'Your profile is ready. Compare your Deck separately before syncing.':mismatches.length?'Choose the matching releases below, or explicitly accept the installed releases during review.':'Required mods are ready. Review & apply shows every enabled state change before saving.';
  if(profileLoading){$('profile-entries').innerHTML='<p class="muted">Comparing the saved profile with installed mods…</p>';return;}
  if(profileError){$('profile-entries').innerHTML=`<p class="warning" role="alert">${esc(profileError)}</p>`;return;}
  if(!result){$('profile-entries').innerHTML=selectedProfile?'<h2>Profile mods</h2><p class="muted">Choose Check mods above to find missing mods and release differences.</p>':'<h2>Your first profile</h2><p class="muted">Choose New profile to save your current mod set, or Import profile to use a shared list.</p>';return;}
  const downloadReason=!state.nexus_connected?'Connect Nexus on Updates first.':pending?'Finish, retry, or cancel the current download first.':'';
  const completion=applied?`<div class="guided-result ${approval?'action':'done'}" role="status"><strong>${approval?'Profile applied · Workshop approval needed':'Profile applied'}</strong><p>${approval?'Use Launch to approve above. Approve these mods in the Workshop loader, close the game, then Check again.':'Your mod enabled states now follow this profile. Updates and configuration files remain shared.'}</p>${approval?`<ul>${profileCompletion.pending.map(item=>`<li>${esc(profileIssue(item))}</li>`).join('')}</ul>`:''}</div>`:'';
  const attention=entry=>entry.enabled && entry.status==='missing' || entry.status==='ambiguous' || entry.version_mismatch;
  const ordered=[...entries].sort((a,b)=>Number(!!attention(b))-Number(!!attention(a)));
  $('profile-entries').innerHTML=`<h2>Profile mods <span class="badge">${entries.length}</span></h2>${completion}${steamWaiting?'<p class="warning small">Waiting for Steam. Finish the Workshop download, then use Refresh Steam mods above.</p>':''}${blockers.length?`<details class="guided-details"><summary>${blockers.length} items need attention</summary><ul>${blockers.map(issue=>`<li>${esc(profileIssue(issue))}</li>`).join('')}</ul></details>`:''}${entries.length?ordered.map(entry=>{
    const needsInstall=entry.enabled && entry.status==='missing',needsVersion=entry.version_mismatch===true;
    const source=sourceLabel(entry),actions=[];
    if((needsInstall || needsVersion) && source==='Nexus')actions.push(`<button data-profile-download="${esc(entry.key)}" class="primary"${downloadReason?` disabled title="${esc(downloadReason)}"`:''}>${needsVersion?'Download profile version':'Install from Nexus'}</button>`);
    if((needsInstall || needsVersion) && source==='Nexus' && !state.nexus_connected)actions.push('<a href="#updates">Connect Nexus</a>');
    if((needsInstall || needsVersion) && source==='Steam' && entry.workshop_id)actions.push(`<button data-profile-workshop="${esc(entry.workshop_id)}">Open Steam Workshop</button>`);
    if((needsInstall || needsVersion) && source==='Manual')actions.push('<button data-profile-zip>Install ZIP</button>');
    if(entry.status==='ambiguous')actions.push('<button data-page="mods">Review installed copies</button>');
    const status=entry.status==='ambiguous'?'Choose one matching copy in My mods':entry.status==='missing'?entry.enabled?'Install this mod to continue':'Optional · disabled in profile':needsVersion?'Choose a release · installed version differs':'Ready';
    return `<div class="item-line profile-entry ${needsInstall || needsVersion || entry.status==='ambiguous'?'guided-action':'guided-ready'}"><div><strong>${esc(entry.name)}</strong><div class="profile-entry-meta"><span class="badge ${source.toLowerCase()}">${source}</span><span>${entry.enabled?'Enabled':'Disabled'} in profile</span><span>${esc(entry.version || 'Unknown')}${entry.installed_version && needsVersion?' · installed '+esc(entry.installed_version):''}${needsVersion && entry.file_id?' · profile file #'+esc(entry.file_id):''}${needsVersion && entry.installed_file_id?' · installed file #'+esc(entry.installed_file_id):''}</span></div><p class="small ${needsInstall || needsVersion || entry.status==='ambiguous'?'warning':'green'}">${esc(status)}${entry.reason?' · '+esc(entry.reason):''}</p></div><div class="actions">${actions.join('')}</div></div>`;
  }).join(''):'<p class="muted">This profile has no gameplay mods.</p>'}`;
}
async function compareProfile(id=selectedProfile){
  if(!id)return null;
  const revision=++profileRevision;profileLoading=true;profileError='';profileComparison=null;renderProfiles();
  try{const result=await api('profile-compare',{id});if(id===selectedProfile && revision===profileRevision){profileComparison=result;for(const entry of result.entries || [])if(entry.workshop_id && entry.status==='installed')profileWorkshopPending.delete(String(entry.workshop_id));if(profileCompletion?.id===id)profileCompletion.pending=(profileCompletion.pending || []).filter(item=>{const name=profileIssue(item),mod=state.mods?.find(mod=>mod.workshop_id && mod.name===name);return !mod || !mod.enabled;});return result;}return null;}
  catch(error){if(id===selectedProfile && revision===profileRevision)profileError=error.message;throw error;}
  finally{if(revision===profileRevision){profileLoading=false;renderProfiles();}}
}
async function refreshSelectedProfile(){if(selectedProfile && (state.profiles || []).some(profile=>profile.id===selectedProfile))await compareProfile();}
async function saveProfile(replace=false){
  const profile=(state.profiles || []).find(item=>item.id===selectedProfile),field=$('profile-name'),name=replace?profile?.name:field.value.trim();
  if(!name){toast('Enter a name for the new profile.',true);field?.focus();return;}
  await work('Saving the current mod profile…',async()=>{const result=await api('profile-save',{name,...(replace?{id:selectedProfile}:{})});selectedProfile=result.profile?.id || result.id || selectedProfile;profileComparison=null;profileCompletion=null;if(!replace){field.value='';$('dialog').close();}await refresh();await refreshSelectedProfile();$('profile-workflow').scrollIntoView({block:'nearest',behavior:'instant'});toast(result.message || 'Profile saved from the current mod setup.');});
}
async function importProfile(){return work('Importing a shared mod profile…',async()=>{const result=await api('profile-import',{});if(!result.profile)return;selectedProfile=result.profile.id;profileComparison=null;profileCompletion=null;await refresh();await refreshSelectedProfile();$('profile-workflow').scrollIntoView({block:'nearest',behavior:'instant'});focusAfterWork($('profile-entries').querySelector('h2'));toast('Profile imported. Get its mods ready, then review and apply.');});}
async function openProfileWorkshop(workshop_id){return work('Opening the Steam Workshop item…',async()=>{await api('steam-workshop',{workshop_id});profileWorkshopPending.add(String(workshop_id));renderProfiles();toast('Subscribe in Steam and wait for the download. Then use Refresh Steam mods above.');});}
async function refreshProfileFlow(){return work('Checking the profile’s installed mods…',async()=>{await refresh(false,true);await refreshSelectedProfile();toast(profileComparison?.blockers?.length?'Profile checked. Follow the highlighted items to continue.':'Required mods found. Review & apply the profile when ready.');});}
function deleteProfile(){
  const profile=(state.profiles || []).find(item=>item.id===selectedProfile);if(!profile)return;
  modal('Delete mod profile',`<p>Delete <strong>${esc(profile.name)}</strong>?</p><p class="muted">This removes the saved profile. Installed mods and files are kept.</p>`,[['Cancel',()=>$('dialog').close()],['Delete profile',()=>work('Deleting the saved profile…',async()=>{const result=await api('profile-delete',{id:profile.id});$('dialog').close();selectedProfile='';profileComparison=null;profileCompletion=null;await refresh();toast(result.message || 'Profile deleted.');}),'danger']]);
}
async function downloadProfileMod(key){return work('Preparing the profile mod download…',async()=>{
  const id=selectedProfile,result=await api('profile-download',{id,key});await refresh();
  if(result?.id && result.state){if(!state.downloads?.some(job=>job.id===result.id))state.downloads=[...(state.downloads || []),result];renderUpdates();if(result.state==='completed')await refreshSelectedProfile();else await pollDownloads();}
  else{if(id===selectedProfile)await refreshSelectedProfile();toast(result.message || 'Profile mod installed.');}
});}
async function reviewProfile(){return work('Checking the profile before applying…',async()=>{
  const result=await compareProfile();if(!result)return;
  const blockers=result.blockers || [],mismatches=result.mismatches || [],changes=result.changes || [];
  const blocked=blockers.length?'Resolve the missing or ambiguous enabled mods before applying.':mismatches.length?'Accept the installed version differences to continue.':'';
  const list=(title,items)=>items.length?`<h3>${title}</h3><ul>${items.map(item=>`<li>${esc(typeof item==='string'?item:item.name || profileIssue(item))}${typeof item!=='string'?`${item.version?' · profile '+esc(item.version):''}${item.installed_version?' · installed '+esc(item.installed_version):''}${item.file_id?' · profile file #'+esc(item.file_id):''}${item.installed_file_id?' · installed file #'+esc(item.installed_file_id):''}${item.reason?' · '+esc(item.reason):''}`:''}</li>`).join('')}</ul>`:'';
  modal('Review profile changes',`<p>Apply <strong>${esc(result.name)}</strong> to this PC?</p>${changes.length?`<h3>${changes.length} enabled state changes</h3><ul>${changes.map(change=>`<li>${change.enabled?'Enable':'Disable'} ${esc(change.name)} · ${esc(change.source || '')}</li>`).join('')}</ul>`:'<p class="green">Enabled states already match.</p>'}${list('Resolve before applying',blockers)}${list('Installed release differences',mismatches)}${mismatches.length?'<label class="profile-version-accept"><input id="profile-accept-versions" type="checkbox"> Use the installed releases where versions or file variants differ</label>':''}<p class="footnote">Loaders and configurations remain shared. Review the Deck separately before syncing.</p>`,[['Cancel',()=>$('dialog').close()],['Apply profile',()=>work('Applying the selected mod profile…',async()=>{const accept_versions=mismatches.length>0 && !!$('profile-accept-versions')?.checked;$('dialog').close();const applied=await api('profile-apply',{id:result.id,token:result.token,accept_versions});profileCompletion={id:result.id,pending:applied.pending || [],acceptedVersions:accept_versions,releaseSignature:profileReleaseSignature(result)};await refresh();await refreshSelectedProfile();toast('Profile applied. '+(applied.pending?.length?'Launch the game to approve Workshop mods.':'Your enabled states are ready.'));const warnings=[...new Set((applied.warnings || []).map(profileIssue))],pending=applied.pending || [];modal(pending.length?'Profile applied · one step left':'Profile applied',`<div class="guided-result ${pending.length?'action':'done'}"><strong>${pending.length?'Approve Workshop mods in the game':'Your mod setup is ready'}</strong><p>${pending.length?'Launch the game and approve the listed mods in the Workshop loader. Close the game, then refresh profile status to confirm.':'The selected profile’s enabled states have been applied. Existing configurations stay shared.'}</p>${pending.length?`<ul>${pending.map(item=>`<li>${esc(profileIssue(item))}</li>`).join('')}</ul>`:''}</div>${warnings.length?`<details><summary>Additional notes</summary><ul class="warning">${warnings.map(warning=>`<li>${esc(warning)}</li>`).join('')}</ul></details>`:''}`,[['Back to profile',()=>{$('dialog').close();revealProfileMods();},!pending.length],...(pending.length?[['Launch game to approve',()=>work('Launching the game for Workshop approval…',async()=>{$('dialog').close();toast((await api('launch',{})).message);}),true]]:[])]);}),true,blocked]]);
  if(mismatches.length){const input=$('profile-accept-versions'),button=$('dialog-actions').lastElementChild;input.checked=false;input.addEventListener('change',()=>{button.disabled=!!blockers.length || !input.checked;button.title=blockers.length?'Resolve the missing or ambiguous enabled mods before applying.':input.checked?'':'Accept the installed version differences to continue.';});}
});}
let deckProtonState=null, deckProtonTarget='', deckProtonRevision=0, deckPasswordTarget,deckSyncResult=null,deckComparisonFailure='',deckSettingsInitialized=false;
function locationsBody(){return {game:$('game-path').value,workshop:$('workshop-path').value,import_folder:$('import-path').value};}
function deckFields(){return Object.fromEntries(['host','user','port','key','game_path','workshop_path'].map(key=>[key,$('deck-'+key).value.trim()]));}
function deckFieldsFill(deck={}){for(const key of ['host','user','port','key','game_path','workshop_path'])$('deck-'+key).value=deck?.[key] ?? ({user:'deck',port:22}[key] ?? '');}
function deckPasswordIdentity(deck={}){return JSON.stringify([String(deck.host || '').trim().toLowerCase(),String(deck.user || 'deck').trim(),Number(deck.port || 22)]);}
function deckSavedPassword(){return deckPasswordIdentity(deckFields())===deckPasswordIdentity(state?.settings?.deck) && !!state?.deck_connection?.password_saved;}
function renderDeckPassword(){
  const target=deckPasswordIdentity(deckFields()),saved=deckSavedPassword(),matching=target===deckPasswordIdentity(state?.settings?.deck);
  if(target!==deckPasswordTarget){if(deckPasswordTarget!==undefined)$('deck-password').value='';$('deck-remember-password').checked=saved;deckPasswordTarget=target;}
  $('deck-forget-password').hidden=!saved && !(matching && state?.deck_connection?.password_error);
  $('deck-password').placeholder=saved && $('deck-remember-password').checked?'Saved password · leave blank to use':'Your Deck password';
  $('deck-password-status').textContent=matching && state?.deck_connection?.password_error || (saved?'Password saved securely on this Windows account. Leave it blank to reconnect, or enter a replacement.':'Your last Deck address is remembered. Saving its password is optional; it stays out of settings and mod sync.');
  const remember=$('deck-remember-password').closest?.('label');if(remember)remember.hidden=!saved && !$('deck-password').value;
}
async function forgetDeckPassword(){
  $('deck-password').value='';$('deck-remember-password').checked=false;
  await work('Forgetting the Deck password…',async()=>{const result=await api('deck-forget-password',{});await refresh();toast(result.message || 'Saved Deck password removed.');});
}
function deckReady(){return !!state?.deck_connection?.connected && ['host','user','port','key'].every(key=>String(deckFields()[key])===String(state.settings.deck?.[key] ?? ''));}
function resetDeckPreview(){comparison=null;deckSyncResult=null;deckComparisonFailure='';$('deck-comparison').innerHTML='<h2>Review PC → Deck changes</h2><p class="muted">Connect your Deck, then compare. GK2MT checks the game version before reviewing mod files.</p>';renderDeckActions();}
function resetDeckProton(){deckProtonState=null;deckProtonTarget='';deckProtonRevision++;}
function deckCanInstall(){return deckReady() && !!deckFields().game_path && !!deckFields().workshop_path && comparison?.game_version?.matched===true && !deckProtonState?.loading && deckProtonState?.configured===false;}
function deckCanSync(){return deckReady() && comparison?.game_version?.matched===true && deckProtonState?.configured===true && !deckProtonState.loading && state.loader_installed!==false && ['additions','changes','extras'].some(key=>Number(comparison.counts?.[key])>0);}
function renderDeckActions(){
  const connected=deckReady(),folders=deckFields(),canCompare=connected && !!folders.game_path && !!folders.workshop_path;
  const install=deckCanInstall(),sync=deckCanSync(),proton=deckProtonState;
  const plan=comparison || deckSyncResult?.plan,matched=plan?.game_version?.matched===true,loaderReady=proton?.configured===true;
  const unchanged=plan && !Number(plan.counts?.additions) && !Number(plan.counts?.changes) && !Number(plan.counts?.extras);
  const parity=deckSyncResult?.result?.parity===true || (unchanged && !plan.unsupported?.length);
  const complete=connected && !!folders.game_path && !!folders.workshop_path && proton?.configured===true && proton.loader_installed===true;
  for(const [id,number,label,disabled,status,description] of [
    ['deck-connect',1,connected?'Reconnect':'Connect',false,canCompare?'done':'action',connected?canCompare?'Deck connected · game folders found':'Choose the missing folders in Advanced':'Enter your Deck address and password'],
    ['deck-preview',2,comparison || deckSyncResult?'Compare again':'Compare',!canCompare,matched?'done':canCompare?'action':'waiting',matched?`Same Steam build ${plan.game_version.build_id} · file changes reviewed`:deckComparisonFailure?'Comparison stopped · see the message below':'Verify game versions and review mod files'],
    ['deck-proton-setup',3,loaderReady?proton.loader_installed?'BepInEx ready':'BepInEx configured':'Install BepInEx',!install,loaderReady?'done':install?'action':'waiting',loaderReady?proton.loader_installed?'Loader files and Proton setting detected':'Loading configured · files copied during sync':proton?.loading?'Checking automatically…':proton?.error?'Setup check needs attention':matched?'Prepare the Proton loading setting':'Unlocks after a matching comparison'],
    ['sync-now',4,'Deck Sync',!sync,parity?'done':sync?'action':'waiting',parity?'Managed mod file hashes match':deckSyncResult?'Copied · some custom files need manual setup':unchanged?'No transfer needed · review any custom files':plan?`${Number(plan.counts?.additions) || 0} new · ${Number(plan.counts?.changes) || 0} changed · ${Number(plan.counts?.extras) || 0} extras`:'Back up, copy mods and verify their hashes']
  ])actionCard(id,number,label,status,description,disabled);
  $('deck-preview').title=canCompare?'Verify both game versions and review mod files.':'Connect and find the game folders first.';
  $('deck-proton-setup').title=proton?.configured?'Loading is configured. Deck Sync copies any missing BepInEx files.':install?'Prepare BepInEx loading through Proton.':'Compare matching game versions first.';
  $('sync-now').title=sync?'Back up changed Deck files, sync and verify.':!comparison?'Compare first to review the files.':!proton?.configured?'Install BepInEx loading first.':state.loader_installed===false?'Install BepInEx on your PC first.':'No file transfer is needed.';
  $('deck-proton-info').hidden=!install;
  $('deck-setup-guide').hidden=complete;
  $('deck-guide-message').textContent=!connected?'Turn on SSH and find your Deck’s IP address. The guide walks you through it.':!proton?.configured?'Your Deck is connected. Compare game versions, prepare BepInEx, then sync your PC’s mods.':'BepInEx loading is configured. Deck Sync copies the missing loader files and mods from your PC.';
  const settings=state.settings.deck || {};
  if(!deckSettingsInitialized){$('deck-settings').open=!settings.host;deckSettingsInitialized=true;}
  if(state.deck_connection?.connected && !connected)$('deck-settings').open=true;
  $('deck-settings-summary').textContent=settings.host?`${settings.user || 'deck'}@${settings.host} · saved`:'Enter your connection details';
}
function renderDeckProton(){
  const ready=deckReady() && !!deckFields().game_path;
  if(!ready || (deckProtonTarget && deckProtonTarget!==JSON.stringify(deckFields())))resetDeckProton();
  $('deck-proton-check').disabled=!ready || !!deckProtonState?.loading;
  const result=deckProtonState;
  $('deck-proton-status').innerHTML=!result?`<p class="muted">${ready?'Check the Deck’s BepInEx loading setup, or enable it now.':'Connect to your Deck and select its game folder first.'}</p>`:result.loading?'<p class="muted">Checking the game’s Proton setup…</p>':result.error?`<p class="warning">${esc(result.error)}</p>`:`<p class="${result.configured?'green':'warning'}">${result.configured?'Per-game BepInEx loading override configured.':'Per-game BepInEx loading override is not configured.'}</p><p class="${result.loader_installed?'muted':'warning'}">${result.loader_installed?'Windows BepInEx files detected. Launch the game to confirm mods load.':'Windows BepInEx files are missing or incomplete. Compare and sync your PC mods to install them on the Deck.'}</p><details><summary>Setup details${result.changed?' · saved':''}</summary><p class="footnote">Proton environment</p><pre>${esc(result.prefix)}</pre>${result.backup?`<p class="footnote">Original registry backup</p><pre>${esc(result.backup)}</pre>`:''}</details>`;
  renderDeckActions();
}
async function configureDeckProton(install=false){
  if(!deckReady() || !deckFields().game_path)throw new Error('Connect to your Deck and select its game folder first.');
  if(install && !deckCanInstall())throw new Error('Compare matching game versions before installing BepInEx.');
  if(install)resetDeckPreview();resetDeckProton();
  const revision=deckProtonRevision,deck=deckFields();
  deckProtonTarget=JSON.stringify(deck);deckProtonState={loading:true};renderDeckProton();
  try{
    const result=await api(install?'deck-proton-setup':'deck-proton-status',{deck});
    if(revision===deckProtonRevision)deckProtonState=result;
  }catch(error){if(revision===deckProtonRevision)deckProtonState={error:error.message};throw error;}
  finally{await refresh();renderDeckProton();}
  if(install && deckProtonState?.configured===true)await compareDeck();
}
function renderDeckConnection(){
  const connected=deckReady(),session=state?.deck_connection,folders=deckFields();
  const hasFolders=!!folders.game_path && !!folders.workshop_path;
  $('deck-disconnect').hidden=!session?.connected;
  $('deck-status').classList.toggle('green',connected);
  if(!connected && (comparison || deckSyncResult))resetDeckPreview();
  renderDeckPassword();
  renderDeckProton();
  const next=!hasFolders?'Choose folders in Deck settings, or reconnect to detect them.':deckComparisonFailure?'Resolve the comparison message below.':deckProtonState?.error?'BepInEx check needs attention: '+deckProtonState.error+' Follow the setup guide, then compare again.':deckSyncResult?'Sync finished. Launch the game on Deck to confirm the mods load.':!comparison?'Next: compare to verify the game version.':state.loader_installed===false?'Install BepInEx on your PC in Locations & setup, then compare again.':!deckProtonState?.configured?'Next: Install BepInEx.':deckCanSync()?'Ready to sync. Review the file changes below.':'Managed files already match. Review any custom files below.';
  const disconnected=folders.host && (!session?.message || session.message==='Enter your Deck address to connect.')?'Your Deck address is saved. Connect to continue.':session?.message || 'Connect your Deck to unlock the next step.';
  $('deck-status').textContent=connected?`Connected to ${session.user}@${session.host}. ${next}`:session?.connected?'Connection details changed. Connect again to use this Deck.':disconnected;
}
async function connectDeck(deck,expected_key,credentials={password:$('deck-password').value,remember_password:$('deck-remember-password').checked}){
  const password=credentials.password,remember_password=credentials.remember_password===true;credentials.password='';$('deck-password').value='';$('dialog').close();resetDeckPreview();resetDeckProton();renderDeckProton();
  await work('Connecting to your Deck…',async()=>{
    try{
      const result=await api('deck-connect',{deck,password,remember_password,...(expected_key?{expected_key}:{})});
      deckFieldsFill(result.deck);
      $('deck-settings').open=!result.discovery?.found;
      if(!result.discovery?.found)$('deck-advanced').open=true;
      if(result.credential_warning)toast(result.credential_warning,true);
      else if(!result.discovery?.found)toast(result.discovery?.message || 'Connected. Choose your Deck game and Workshop folders in Advanced.');
      else toast('Deck connected. Game folders found.');
    }finally{await refresh();}
    if(deckReady() && deckFields().game_path)try{await configureDeckProton();}catch(error){toast('Connected. BepInEx setup needs attention: '+error.message,true);}
  });
}
async function stagePath(path){return stagePaths([path]);}
async function stagePaths(paths){
  if(!Array.isArray(paths) || !paths.length || paths.length>100)throw new Error('Choose 1–100 mod ZIP files.');
  const packages=[],errors=[];
  for(const path of [...new Set(paths)]){
    if(typeof path!=='string' || !/\.zip$/i.test(path)){errors.push('Only ZIP files can be imported as mods.');continue;}
    try{packages.push(await api('stage',{path}));}catch(error){errors.push(path.split(/[\\/]/).pop()+': '+error.message);}
  }
  await reviewPackages(packages,errors);
}
function setupArchive(result={}){
  modal('Set up BepInEx from Nexus #48',`<p>${esc(result.message || 'Download the BepInEx for Graveyard Keeper 2 ZIP from Nexus, then select it here. Your current Workshop loader and configs are preserved.')}</p><p><a href="https://www.nexusmods.com/graveyardkeeper2/mods/48?tab=files" target="_blank" rel="noreferrer">Open Nexus download page ↗</a></p>${result.choices?.length?`<label>Available package<select id="setup-file">${result.choices.map(f=>`<option value="${Number(f.file_id)}">${esc(f.name || f.file_name)} · ${esc(f.version || '')}</option>`).join('')}</select></label>`:''}<label>Downloaded ZIP path<input id="setup-archive" placeholder="Paste the full path to the Nexus #48 ZIP"></label>`,[
    ['Browse…',()=>work('Choose the BepInEx ZIP…',async()=>{const result=await api('browse',{kind:'zip'});if(result.path)$('setup-archive').value=result.path;})],
    ...(result.choices?.length?[['Download selected file',()=>runSetup({file_id:Number($('setup-file').value)})]]:[]),
    ['Install ZIP',()=>{const archive=$('setup-archive').value.trim();if(!archive){toast('Choose the downloaded Nexus #48 ZIP first.',true);return;}runSetup({archive});},true]
  ]);
}
function runSetup(body={}){return work('Preparing BepInEx from Nexus #48…',async()=>{
  const result=await api('setup',body);
  if(result.requires_download){setupArchive(result);return;}
  await refresh();
  modal('BepInEx setup complete',`<p>${esc(result.message)}</p><p>${Number(result.files)} files installed.</p><p class="footnote">Original files backed up to:</p><pre>${esc(result.backup)}</pre>`,[['Done',()=>$('dialog').close(),true]]);
});}
function importOwners(conflict){
  const owners=(conflict.owners || []).map(owner=>`<li><strong>${esc(owner.name)}</strong><span>${owner.enabled?'enabled':'disabled'}${owner.kind==='file'?' · current file':owner.kind==='disabled_file'?' · disabled file':''}</span></li>`).join('');
  const incoming=(conflict.incoming || []).map(mod=>`<li><strong>${esc(mod.name)}</strong><span>incoming ZIP</span></li>`).join('');
  return `<ul class="import-owners">${owners}${incoming}</ul>`;
}
function importResult(result,errors=[]){
  const rows=result.results || [],installed=rows.filter(p=>p.status==='installed').length,skipped=rows.length-installed;
  const explanation={already_installed:'Already installed · skipped. Its enabled state and files were kept.',already_selected:'Identical ZIP in this selection · skipped.',kept_existing:'Skipped: the selected existing file was kept. No separate DLL remained to install.'};
  modal('ZIP installation complete',`<div class="import-review"><p role="status">${installed} mod${installed===1?'':'s'} installed${skipped?` · ${skipped} skipped`:''}.</p>${rows.map(p=>`<div class="import-package"><strong>${esc(p.name)}</strong><p class="small ${p.status==='installed'?'green':'muted'}">${esc(p.reason || p.message || explanation[p.status] || 'Installed. Replaced files were backed up.')}</p>${p.excluded_paths?.length?`<details><summary>${p.excluded_paths.length} shared files kept from your selection</summary><ul class="path-list">${p.excluded_paths.map(path=>`<li>${esc(path)}</li>`).join('')}</ul></details>`:''}</div>`).join('')}${errors.length?`<p class="warning">These ZIPs were not installed:</p><ul class="warning">${errors.map(error=>`<li>${esc(error)}</li>`).join('')}</ul>`:''}</div>`,[['Done',()=>$('dialog').close(),true]]);
  $('dialog-body').scrollTop=0;focusAfterWork($('dialog-title'));
  toast(`${installed} mod${installed===1?'':'s'} installed${skipped?` · ${skipped} skipped`:''}.`);
}
async function reviewPackages(packages,errors=[]){
  const tokens=[...new Set(packages.map(p=>p.token))];
  let preview;
  try{preview=tokens.length?await api('install-preview',{tokens}):{packages:[],conflicts:[]};}
  catch(error){
    modal('ZIP review needs attention',`<p class="warning" role="alert">${esc(error.message)}</p><p class="small">The ZIPs have not been installed. Review them again to check the current files.</p>`,[['Cancel',()=>$('dialog').close()],['Review again',()=>work('Checking the ZIPs again…',()=>reviewPackages(packages,errors)),true]]);
    $('dialog-body').scrollTop=0;focusAfterWork($('dialog-title'));return;
  }
  const reviewed=preview.packages || [],ready=reviewed.filter(p=>p.status==='ready'),skipped=reviewed.length-ready.length;
  const conflicts=(preview.conflicts || []).filter(conflict=>conflict.requires_choice),shared=(preview.conflicts || []).filter(conflict=>!conflict.requires_choice);
  const duplicate=ready.some(p=>p.requires_duplicate_ack || p.duplicates?.length);
  const choices={};let needsReview=false,duplicateAcknowledged=false;
  const reason=()=>conflicts.some(conflict=>!choices[conflict.path])?'Choose which file to keep for each shared path.':duplicate && !duplicateAcknowledged?'Confirm the duplicate copy choice above.':'';
  const submit=()=>work(needsReview?'Checking the ZIPs again…':'Installing mods and saving originals…',async()=>{
    if(needsReview)return reviewPackages(packages,errors);
    duplicateAcknowledged=duplicate && $('import-duplicate-accept').checked===true;
    if(reason())throw new Error(reason());
    let result;
    try{result=await api('install-batch',{tokens,choices,digest:preview.digest,acknowledge_duplicates:duplicate});}
    catch(error){
      needsReview=true;const message=$('import-review-error');message.textContent=error.message+' Review the ZIPs again before installing.';message.hidden=false;
      const button=$('dialog-actions').lastElementChild;button.textContent='Review again';button.disabled=false;button.title='';message.scrollIntoView({block:'nearest',behavior:'instant'});focusAfterWork(message);return;
    }
    importResult(result,errors);await refresh();await refreshSelectedProfile();
  });
  modal('Review ZIP installation',`<div class="import-review"><p class="muted">${ready.length} ready to install${skipped?` · ${skipped} already installed or selected and skipped`:''}.${ready.length?' Close the game first. Replaced files are backed up.':' No files need changing.'}</p><div class="import-packages">${reviewed.map(p=>`<div class="import-package"><strong>${esc(p.name)}</strong>${p.status==='already_installed'?'<p class="small green">Already installed · skipped. Its current enabled state is kept.</p>':p.status==='already_selected'?'<p class="small green">Identical ZIP in this selection · skipped.</p>':`<p class="small muted">${Number(p.files)} files${p.validation?.message?' · '+esc(p.validation.message):''}</p>${p.duplicates?.length?duplicatePreview(p.duplicates):''}`}</div>`).join('')}</div>${conflicts.length?`<section class="import-conflicts" aria-labelledby="import-conflicts-title"><h3 id="import-conflicts-title">${conflicts.length} shared file${conflicts.length===1?' needs':'s need'} a choice</h3><p class="small muted">Choose the file to keep at each destination. Other files install normally. Keeping a disabled copy does not enable it; a ZIP with no DLL left is skipped.</p>${conflicts.map((conflict,index)=>`<article class="import-conflict"><label for="import-choice-${index}"><strong>${esc(conflict.path.split(/[\\/]/).pop())}</strong><span class="import-path">${esc(conflict.path)}</span></label>${importOwners(conflict)}<select id="import-choice-${index}" aria-label="File to keep for ${esc(conflict.path)}" aria-required="true"><option value="">Choose which file to keep…</option>${(conflict.choices || []).map(choice=>`<option value="${esc(choice.value)}">${esc(choice.label)}</option>`).join('')}</select></article>`).join('')}</section>`:''}${shared.length?`<details class="import-shared"><summary>${shared.length} shared file${shared.length===1?'':'s'} with identical bytes · no choice needed</summary><p class="small muted">These files match exactly and can be shared. Removing or disabling one mod keeps the file available to the other.</p>${shared.map(conflict=>`<div class="import-shared-file"><span class="import-path">${esc(conflict.path)}</span>${importOwners(conflict)}</div>`).join('')}</details>`:''}${errors.length?`<p class="warning">These ZIPs could not be prepared:</p><ul class="warning">${errors.map(error=>`<li>${esc(error)}</li>`).join('')}</ul>`:''}${duplicate?'<label class="inline-check duplicate-accept"><input id="import-duplicate-accept" type="checkbox"> Install the additional copy. I’ll keep only one copy enabled before launching.</label>':''}<p id="import-review-status" class="small muted" role="status"></p><p id="import-review-error" class="warning" role="alert" hidden></p></div>`,[['Cancel',()=>$('dialog').close()],...(ready.length?[['Install '+ready.length+' mod'+(ready.length===1?'':'s'),submit,true,reason()]]:[['Done',()=>$('dialog').close(),true]])]);
  const updateButton=()=>{if(needsReview)return;duplicateAcknowledged=duplicate && $('import-duplicate-accept').checked===true;const message=reason(),button=$('dialog-actions').lastElementChild;button.disabled=!!message;button.title=message;$('import-review-status').textContent=message || (ready.length?'Ready to install with your file choices.':'');};
  conflicts.forEach((conflict,index)=>$('import-choice-'+index).addEventListener('change',event=>{const value=event.target.value;if((conflict.choices || []).some(choice=>choice.value===value))choices[conflict.path]=value;else delete choices[conflict.path];updateButton();}));
  if(duplicate)$('import-duplicate-accept').addEventListener('change',updateButton);
  updateButton();$('dialog-body').scrollTop=0;focusAfterWork($('dialog-title'));
}
function duplicatePreview(copies){return `<div class="callout duplicate-preview"><strong>Another copy is already installed</strong><ul>${copies.map(copy=>`<li>${esc(copy.name)} · ${esc(copy.source || sourceLabel(copy))} · ${copy.enabled?'enabled':'disabled'}</li>`).join('')}</ul><p class="small">Installing both Steam and Nexus/manual copies can cause conflicts. Cancel to keep the existing copy.</p></div>`;}
function approveDownload(id){
  const job=state.downloads?.find(job=>job.id===id);if(!job?.review_required)return;
  modal('Review duplicate mod',duplicatePreview(job.duplicates || [])+'<label class="inline-check duplicate-accept"><input id="download-duplicate-accept" type="checkbox"> Install this additional copy. I’ll keep only one copy enabled.</label>',[['Cancel',()=>$('dialog').close()],['Install this copy',()=>{if(!$('download-duplicate-accept').checked)return;$('dialog').close();return downloadPanelAction('download-panel-approve',id,null,true);},true,'Confirm the additional copy above.']]);
  const button=$('dialog-actions').lastElementChild;$('download-duplicate-accept').addEventListener('change',event=>{button.disabled=!event.target.checked;});
}
function compareView(result){
  comparison=null;
  if(result.game_version?.matched!==true || !result.game_version.build_id)throw new Error('The game version was not verified. Compare PC and Deck again.');
  comparison=result;deckSyncResult=null;deckComparisonFailure='';
  const unchanged=!Number(result.counts?.additions) && !Number(result.counts?.changes) && !Number(result.counts?.extras);
  $('deck-comparison').innerHTML=`<h2>${unchanged?'Your managed mod files already match':'Review PC → Deck changes'}</h2><p class="green" role="status">Game version verified · Steam build ${esc(result.game_version.build_id)} on PC and Deck</p><div class="summary-chips"><span>${result.counts.additions} new</span><span>${result.counts.changes} changed</span><span>${result.counts.extras} extra on Deck</span><span>${result.counts.local_files} PC files</span></div>${result.unsupported?.length?`<p class="warning">These custom files need manual setup; full parity cannot be claimed:</p><details><summary>${result.unsupported.length} unsupported files</summary><pre>${esc(result.unsupported.join('\n'))}</pre></details>`:''}${unchanged?`<p>${result.unsupported?.length?'The supported files match. Handle the custom files listed above separately.':'Every supported mod file has the same hash on both devices. No transfer is needed.'}</p>`:`<details><summary>Review file differences</summary><pre>${esc(['ADD',...(result.additions || []),'\nREPLACE',...(result.changes || []),'\nBACK UP EXTRAS',...(result.extras || [])].join('\n'))}</pre></details><p class="footnote">Both games must be closed. Replaced and extra Deck files will be backed up. The game version is checked again before copying.</p>`}${result.warnings?.length?`<details class="guided-details"><summary>Sync notes</summary><ul>${result.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`:''}`;
  renderDeckActions();
}
function deckComparisonError(error){comparison=null;deckSyncResult=null;deckComparisonFailure=error.message;$('deck-comparison').innerHTML=`<h2>Comparison stopped</h2><p class="warning" role="alert">${esc(error.message)}</p><p class="footnote">${/build|version/i.test(error.message)?'Finish Steam updates on both devices and use the same game branch. Check the selected folders if a game version is unknown.':'Check the connection and close both games, then compare again to get a fresh preview.'}</p>`;renderDeckActions();}
function revealDeckComparison(){const card=$('deck-comparison');card.scrollIntoView({block:'start',behavior:'instant'});focusAfterWork(card.querySelector('h2'));}
async function compareDeck(){resetDeckPreview();try{compareView(await api('deck-preview',{deck:deckFields()}));try{await configureDeckProton();}catch(error){toast('Game versions verified. BepInEx setup needs attention: '+error.message,true);}}catch(error){deckComparisonError(error);throw error;}finally{await refresh();revealDeckComparison();}}
async function syncDeck(){
  if(!comparison || !deckReady())throw new Error('Connect and compare your Deck before syncing.');
  if(!deckCanSync())throw new Error('Compare matching game versions and prepare BepInEx before syncing. No transfer is needed if the managed files already match.');
  const plan=comparison;
  try{
    const result=await api('deck-sync',{digest:plan.digest});comparison=null;deckSyncResult={plan,result};deckComparisonFailure='';
    $('deck-comparison').innerHTML=`<div class="guided-result ${result.parity?'done':'action'}" role="status"><h2>${result.parity?'Sync complete · mod files match':'Sync complete · manual setup remains'}</h2><p>${Number(result.verified_files) || 0} supported mod files verified. ${result.parity?'PC and Deck now have matching managed mod files.':'Some custom files are outside automatic sync; review the details below.'}</p><p class="footnote">Your save files were not copied. ${result.backups?.length?'Replaced and extra Deck files were preserved in backups.':'No original files needed a backup.'} Launch the game on the Deck to confirm the mods load.</p></div><details class="guided-details"><summary>Sync details & backups</summary><pre>${esc(JSON.stringify(result,null,2))}</pre></details>`;
    toast(result.parity?'Deck sync complete. Managed mod files match.':'Deck sync complete. Some custom files need manual setup.');
    if(deckReady() && deckFields().game_path)try{await configureDeckProton();}catch(error){toast('Files synced. BepInEx setup needs attention: '+error.message,true);}
  }catch(error){deckComparisonError(error);throw error;}
  finally{await refresh();renderDeckActions();revealDeckComparison();}
}
async function fullDeckGuide(){const result=await api('deck-guide');modal('Steam Deck · full setup guide',`<pre>${esc(result.text)}</pre>`,[['Close',()=>$('dialog').close()]]);}
function openLink(event){
  const link=event.target.closest('a[href]');if(!link)return false;
  event.preventDefault();
  const href=link.getAttribute('href');
  if(['#mods','#browse','#profiles','#updates','#rules','#deck','#settings'].includes(href))page(href.slice(1));
  else if(/^https:\/\//i.test(href))api('open-external',{url:href}).catch(error=>toast(error.message,true));
  return true;
}
document.addEventListener('click',event=>{
  if(openLink(event))return;
  const target=event.target.closest('button');if(!target)return;
  if(target.hasAttribute('data-deck-full-guide'))work('Opening the full setup guide…',fullDeckGuide);
  if(target.hasAttribute('data-deck-guide'))$('deck-guide').onclick();
  if(target.dataset.page)page(target.dataset.page);
  if(target.dataset.details)details(target.dataset.details);
  if(target.dataset.profileDownload && !target.disabled)downloadProfileMod(target.dataset.profileDownload);
  if(target.dataset.profileWorkshop)openProfileWorkshop(target.dataset.profileWorkshop);
    if(target.hasAttribute('data-profile-zip'))$('import-zip').onclick();
  if(target.dataset.update && !target.disabled)runUpdates(target.dataset.update);
  if(target.dataset.updateArchive)updateArchive(target.dataset.updateArchive);
  if(target.dataset.downloadPanel && !target.disabled)downloadPanelAction('download-panel',target.dataset.downloadPanel,target);
  if(target.dataset.downloadCancel && !target.disabled)downloadPanelAction('download-panel-cancel',target.dataset.downloadCancel,target);
  if(target.dataset.downloadRetry && !target.disabled)downloadPanelAction('download-panel-retry',target.dataset.downloadRetry,target);
  if(target.dataset.downloadApprove && !target.disabled)approveDownload(target.dataset.downloadApprove);
  if(target.hasAttribute('data-nexus-prompt')){$('updates-connections').open=true;$('nexus-key').focus();}
  if(target.dataset.browse)work('Choose a folder…',async()=>{const result=await api('browse',{kind:'folder'});if(result.path)$(target.dataset.browse).value=result.path;});
  if(target.dataset.removeRule!==undefined)work('Applying rules…',async()=>{const rules=state.rules.filter((r,i)=>i!==Number(target.dataset.removeRule));await api('rules',{rules});await refresh();toast('Rule removed.');});
  if(target.id==='sync-now' && comparison && deckReady())work('Verifying game builds, then backing up and syncing Deck files…',syncDeck);
});
document.addEventListener('change',event=>{
  if(event.target.dataset.toggle){const input=event.target;const desired=input.checked;input.checked=!desired;work('Saving mod state…',async()=>{const result=await api('toggle',{id:input.dataset.toggle,enabled:desired});await refresh();toast(result.message);});}
});
$('close-dialog').onclick=()=>$('dialog').close();
for(const id of ['search','source-filter','state-filter','category-filter'])$(id).addEventListener(id==='search'?'input':'change',renderMods);
$('refresh').onclick=()=>work('Scanning game and Workshop files…',async()=>{await refresh(false,true);if(!state.workshop_title_warning && !state.nexus_discovery_warning)toast('Inventory refreshed.');});
$('launch').onclick=()=>work('Launching through Steam…',async()=>toast((await api('launch',{})).message));
$('steam-downloads').onclick=()=>work('Opening Steam…',async()=>toast((await api('steam',{})).message));
$('save-settings').onclick=()=>work('Saving locations…',async()=>{await api('settings',locationsBody());await refresh(true);toast('Settings saved.');});
$('auto-detect').onclick=()=>work('Looking through Steam libraries…',async()=>{const result=await api('discover',{});if(result.game)$('game-path').value=result.game;if(result.workshop)$('workshop-path').value=result.workshop;toast('Detected paths filled in. Save locations to use them.');});
$('import-zip').onclick=()=>work('Choose a mod ZIP…',async()=>{const result=await api('browse',{kind:'zip'});if(result.path)await stagePath(result.path);});
$('import-folder').onclick=()=>work('Inspecting ZIPs in your import folder…',async()=>{const result=await api('stage-folder',{});await reviewPackages(result.packages,result.errors);});
$('add-rule').onclick=()=>work('Checking and applying rule…',async()=>{const a=$('rule-first').value,b=$('rule-second').value;const rule=$('rule-order').value==='after'?{before:b,after:a}:{before:a,after:b};await api('rules',{rules:[...state.rules,rule]});await refresh();toast('Rule applied.');});
$('nexus-connect').onclick=()=>work('Connecting Nexus…',async()=>{const key=$('nexus-key').value,remember_key=$('nexus-remember').checked;$('nexus-key').value='';$('nexus-remember-option').hidden=true;await api('nexus-connect',{key,remember_key});await refresh();toast(remember_key?'Nexus key saved securely for future launches.':'Nexus connected for this session.');});
$('nexus-key').addEventListener('input',()=>$('nexus-remember-option').hidden=!$('nexus-key').value.trim());
$('nexus-forget').onclick=()=>work('Forgetting Nexus API key…',async()=>{$('nexus-key').value='';await api('nexus-forget');await refresh();toast('Saved Nexus key removed.');});
$('check-updates').onclick=()=>work('Checking linked mods on Nexus…',async()=>{await api('updates',{});await refresh();toast('Update check complete.');});
$('download-updates').onclick=()=>runUpdates();
$('nexus-browser-open').onclick=openNexusBrowser;
$('profile-create').onclick=newProfile;
$('profile-replace').onclick=()=>saveProfile(true);
$('profile-import').onclick=importProfile;
$('profile-export').onclick=()=>work('Exporting the selected mod profile…',async()=>{const result=await api('profile-export',{id:selectedProfile});if(result.message)toast(result.message);});
$('profile-delete').onclick=deleteProfile;
$('profile-refresh').onclick=refreshProfileFlow;
$('profile-install').onclick=revealProfileMods;
$('profile-apply').onclick=()=>profileApplied() && profileCompletion.pending?.length?work('Launching the game for Workshop approval…',async()=>toast((await api('launch',{})).message)):reviewProfile();
$('profile-select').addEventListener('change',()=>{selectedProfile=$('profile-select').value;profileRevision++;profileLoading=false;profileComparison=null;profileCompletion=null;profileError='';renderProfiles();if(selectedProfile)return work('Comparing the selected profile…',()=>compareProfile());});
$('setup').onclick=()=>runSetup();
$('foundation-install').onclick=()=>runSetup();
$('workshop-subscribe').onclick=subscribeWorkshopLoader;
$('workshop-loader-install').onclick=installWorkshopLoader;
$('setup-zip').onclick=()=>setupArchive();
$('deck-preview').onclick=()=>work('Verifying game builds, then comparing PC and Deck…',compareDeck);
$('deck-proton-check').onclick=()=>work('Checking BepInEx loading on Deck…',async()=>{await configureDeckProton();toast(deckProtonState?.configured && deckProtonState?.loader_installed?'BepInEx loading and files are ready.':deckProtonState?.configured?'BepInEx loading is configured. Sync to copy the missing files.':'BepInEx loading needs setup. Compare, then Install BepInEx.');});
$('deck-proton-setup').onclick=()=>work('Backing up and configuring BepInEx loading on Deck…',()=>configureDeckProton(true));
$('deck-connect').onclick=async()=>{
  if(!$('deck-host').value.trim() || (!$('deck-password').value && !$('deck-key').value.trim() && !deckSavedPassword())){$('deck-settings').open=true;const target=$(!$('deck-host').value.trim()?'deck-host':'deck-password');target.scrollIntoView({block:'nearest'});focusAfterWork(target);toast(!$('deck-host').value.trim()?'Enter your Deck address to connect.':'Enter your Deck password, or use a saved password or SSH key.');return;}
  resetDeckPreview();resetDeckProton();renderDeckProton();
  const deck=deckFields(),credentials={password:$('deck-password').value,remember_password:$('deck-remember-password').checked};$('deck-password').value='';
  const probe=await work('Finding your Deck…',async()=>{try{return await api('deck-probe',{deck});}finally{await refresh();if(JSON.stringify(deckFields())===JSON.stringify(deck))deckFieldsFill(state.settings.deck);renderDeckConnection();}});
  if(!probe){credentials.password='';return;}
  if(probe.known){await connectDeck(deck,undefined,credentials);return;}
  modal('Trust this Deck?',`<p>This is the first connection to <strong>${esc(probe.host)}:${Number(probe.port)}</strong>. Confirm that this is your Deck before sending your password.</p><p class="small muted">${esc(probe.algorithm)} fingerprint</p><pre>${esc(probe.fingerprint)}</pre><details><summary>Check the fingerprint on your Deck</summary><p>In Konsole, run this and compare the SHA256 fingerprint:</p><pre>sudo ssh-keygen -lf /etc/ssh/ssh_host_${probe.algorithm==='ssh-ed25519'?'ed25519':probe.algorithm==='ssh-rsa'?'rsa':'ecdsa'}_key.pub</pre></details>`,[['Cancel',()=>$('dialog').close()],['Connect this Deck',()=>connectDeck(deck,probe.key,credentials),true]]);
  $('dialog').addEventListener('close',()=>{credentials.password='';$('deck-password').value='';},{once:true});
};
$('deck-disconnect').onclick=()=>work('Disconnecting from your Deck…',async()=>{resetDeckPreview();resetDeckProton();renderDeckProton();$('deck-password').value='';await api('deck-disconnect',{});await refresh();});
$('deck-forget-password').onclick=forgetDeckPassword;
$('deck-remember-password').addEventListener('change',()=>{const forget=!$('deck-remember-password').checked && !$('deck-forget-password').hidden;renderDeckPassword();if(forget)return forgetDeckPassword();});
for(const key of ['host','user','port','key','game_path','workshop_path','password'])$('deck-'+key).addEventListener('input',()=>{resetDeckPreview();resetDeckProton();renderDeckConnection();});
$('deck-detect-paths').onclick=()=>{$('deck-game_path').value='';$('deck-workshop_path').value='';resetDeckPreview();resetDeckProton();renderDeckConnection();toast('Folder overrides cleared. Connect again to find your Steam libraries.');};
$('deck-guide').onclick=()=>modal('Steam Deck · quick setup',`<p>Put the PC and Deck on the same network. On your Deck, choose <strong>Steam → Power → Switch to Desktop</strong>, then open <strong>Konsole</strong>.</p><h3>1. Set a password, only if you haven’t already</h3><pre>passwd</pre><p class="small muted">Use your existing system password if one is set. Password characters stay invisible while typing.</p><h3>2. Turn on SSH and find your IP address</h3><pre>sudo systemctl enable sshd.service\nsudo systemctl start sshd.service\nip -4 addr show</pre><p>Use the Wi-Fi <code>inet</code> address, such as <strong>192.168.1.50</strong>. Ignore <code>127.0.0.1</code> and leave off the <code>/24</code> suffix.</p><h3>3. Connect here</h3><p>Enter that IP, leave the username as <strong>deck</strong>, and enter your Deck system password. Select <strong>Connect to Deck</strong>. Confirm the Deck’s identity on the first connection; GK2MT finds the game folders for you and remembers the last address. Optionally select <strong>Remember password on this Windows account</strong> to reconnect next time without typing it. Use <strong>Forget password</strong> to remove it.</p><p class="muted">No commands or key setup are needed on Windows.</p><h3>4. Enable BepInEx and sync</h3><p>Launch the Windows game through Proton once, then close all Wine or Proton games on the Deck. Close the PC game too, then select <strong>Compare PC &amp; Deck</strong>. When the game versions match, select <strong>Install BepInEx</strong> if needed. GK2MT prepares loading with a backup and refreshes the comparison automatically. Review the file changes, then select <strong>Deck Sync</strong>.</p><p class="footnote"><button data-deck-full-guide>Full guide, BepInEx setup & recovery notes</button></p>`,[['Enter connection details',()=>{$('dialog').close();$('deck-settings').open=true;$('deck-host').scrollIntoView({block:'nearest'});focusAfterWork($('deck-host'));},true]]);
function showDownloadActivity(){page('updates');$('update-log').open=true;$('update-log').scrollIntoView({block:'nearest'});}
$('download-activity').onclick=showDownloadActivity;
$('clear-filters').onclick=()=>{for(const id of ['search','source-filter','state-filter','category-filter'])$(id).value='';renderMods();$('search').focus();};
document.addEventListener('DOMContentLoaded',()=>bridgeReady.then(()=>work('Reading your mod setup…',async()=>{await refresh(true,true);page(['mods','browse','profiles','updates','rules','deck','settings'].includes(location.hash.slice(1))?location.hash.slice(1):'mods');if(state.quick_setup_completed!==true)await openQuickSetup();})),{once:true});

/* AEGIS: Interactive, framework-free frontend. Data is always fetched from the local FastAPI server. */
'use strict';
const $ = (id) => document.getElementById(id);
const svg = (id) => `<svg aria-hidden="true"><use href="#i-${id}"/></svg>`;
const escapeHTML = (x) => String(x ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const norm = (v) => String(v ?? '').toLowerCase();
function themeRead(){try{return localStorage.getItem('aegis_theme')}catch{return 'light'}}
function themeWrite(value){try{localStorage.setItem('aegis_theme',value)}catch{}}
let data = {emails:[], results:[], stats:{inbox:0,processed:0,escalations:0,high_priority:0,average_confidence:0,priorities:{},categories:{}},status:{},policies:{sections:[],count:0},logs:[]};
let selectedPage='dashboard', inboxFilter='all', lastRunStatus=false, activeDrawer=null, isRefreshing=false;
let draftDirty=false, authReady=false, liveStream=null, lastStreamId=0, latestAnalytics=null, lastAnalyticsFetch=0;

async function request(path, options={}){
  const config={...options,headers:{'Content-Type':'application/json',...(options.headers||{})}};
  let response;
  try {response=await fetch(path,config);} catch(e){throw new Error('Cannot connect to local FastAPI server. Is RUN.bat open?');}
  if(!response.ok){if(response.status===401&&path!=='/api/login'&&path!=='/api/session'){$('login-screen')?.classList.remove('hidden');authReady=false;}const b=await response.json().catch(()=>({}));throw new Error(typeof b.detail==='string' ? b.detail : (b.detail?.message ? b.detail.message+' '+(b.detail.issues||[]).join('; ') : `Request failed (${response.status})`));}
  return response.json();
}
function toast(message,type='info'){
  const el=document.createElement('div'); el.className='toast '+type; el.textContent=message;
  $('toast-region').appendChild(el);setTimeout(()=>{el.style.opacity='0';setTimeout(()=>el.remove(),260)},4500);
}

// In-page confirmations replace browser-native blocking dialogs.
function customDialog(message,{title='Confirm action',confirmText='Confirm',input=false,requiredText=null,danger=false}={}){
 return new Promise(resolve=>{
  const back=document.createElement('div');back.className='aegis-confirm-scrim';
  const dialog=document.createElement('div');dialog.className='aegis-confirm-dialog';
  dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',title);
  const h=document.createElement('h3');h.textContent=title;
  const info=document.createElement('p');info.textContent=message;
  dialog.append(h,info);
  let field=null;
  if(input){field=document.createElement('input');field.className='aegis-confirm-input';field.placeholder=requiredText?'Type '+requiredText:'Enter reason';dialog.appendChild(field);}
  const actions=document.createElement('div');actions.className='aegis-confirm-actions';
  const cancel=document.createElement('button');cancel.className='btn btn-secondary';cancel.textContent='Cancel';cancel.type='button';
  const yes=document.createElement('button');yes.className=danger?'btn aegis-danger':'btn btn-primary';yes.textContent=confirmText;yes.type='button';
  actions.append(cancel,yes);dialog.appendChild(actions);back.appendChild(dialog);document.body.appendChild(back);
  const previous=document.activeElement;let done=false;
  function finish(val){if(done)return;done=true;document.removeEventListener('keydown',keyHandler,true);back.remove();previous?.focus();resolve(val);}
  function submit(){let v=input?field.value.trim():true;
    if(requiredText!==null&&v!==requiredText){field.setCustomValidity('Type '+requiredText+' exactly');field.reportValidity();return;}
    finish(v);
  }
  function keyHandler(e){if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();finish(null);}
   if(e.key==='Enter'){e.preventDefault();e.stopImmediatePropagation();submit();}}
  cancel.onclick=()=>finish(null);yes.onclick=submit;
  back.onclick=e=>{if(e.target===back)finish(null)};
  field?.addEventListener('input',()=>field.setCustomValidity(''));
  document.addEventListener('keydown',keyHandler,true);
  setTimeout(()=>input?field.focus():cancel.focus(),0);
 });
}
async function askConfirm(msg,opts={}){return (await customDialog(msg,opts))===true}
async function askText(msg,opts={}){return customDialog(msg,{...opts,input:true})}

function initials(name){return String(name||'ST').trim().split(/\s+/).slice(0,2).map(w=>w[0]||'').join('').toUpperCase();}
function badge(label,kind){return `<span class="badge ${escapeHTML(kind||'other')}">${escapeHTML(label)}</span>`;}
function dateTime(ts){if(!ts)return '—';try{return new Date(ts).toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}catch{return String(ts);}}
function getResult(id){return data.results.find(x=>x.email_id===id);}
function getCase(id){return data.cases?.[id]||null;}
function humanStatus(c,r){return c?.status|| (r?'draft_ready':'unprocessed');}
function statusBadge(c,r){const st=humanStatus(c,r);return `<span class="case-status ${escapeHTML(st)}">${escapeHTML(({unprocessed:'Awaiting AI',draft_ready:'Draft ready',needs_review:'Needs human review',approved:'Approved · not sent',sent:'Google accepted · delivery unverified',provider_accepted:'Google accepted · delivery unverified',sending:'Sending via Gmail…',send_failed:'Send rejected',send_uncertain:'Delivery uncertain · check Gmail',rejected:'Rejected · revise'})[st]||st)}</span>`;}
function friendlyError(message){const m=String(message||'');if(/Administrator sign-in|required|not authenticated/i.test(m))return 'Your administrator session has expired. Please sign in again.';if(/429|quota|rate limit/i.test(m))return m+' — Gemini quota/rate limit reached. Try later or use another approved model/key.';if(/401|403|invalid api key/i.test(m))return m+' — Verify your API key or Google OAuth permission in Settings.';if(/connection|network|timeout/i.test(m))return m+' — Check internet and that RUN.bat remains open.';return m;}

function go(page){
  selectedPage=page;
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`));
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
  $('page-crumb').textContent=({'dashboard':'Overview','analytics':'Analytics','inbox':'Intelligent Inbox','policy':'Policy Library','delivery':'Review & Delivery','logs':'Audit Trail','settings':'Settings','guide':'Operator guide'})[page]||page;
  $('sidebar').classList.remove('open'); window.scrollTo({top:0,behavior:'instant'});
  renderPage();
}
function renderPage(){
  if(selectedPage==='dashboard')renderDashboard();
  if(selectedPage==='analytics')renderAnalytics();
  if(selectedPage==='inbox')renderInbox();
  if(selectedPage==='delivery')renderDelivery();
  if(selectedPage==='policy')renderPolicy();
  if(selectedPage==='logs')renderLogs();
  if(selectedPage==='settings')renderSettings();
}

function renderGlobal(){
  $('nav-count').textContent=data.emails.length;
  const connected=!!data.status.has_key;
  $('connection').className='status-chip '+(connected?'connected':'offline');
  $('connection').innerHTML=`<span class="status-led"></span><span>${connected?'Key configured':'Gemini key not set'}</span>`;
  const running=!!data.status.running;
  document.querySelectorAll('.start-btn').forEach(b=>{b.disabled=running;b.title=running?'Agent is running':'Start autonomous triage';});
  $('progress-card').hidden=!running;
  $('run-error').hidden=!data.status.last_error;
  if(data.status.last_error)$('run-error').textContent='Run issue: '+friendlyError(data.status.last_error)+' — Open Audit & Reasoning for the exact failing step; drafts requiring human review are not considered delivered.';
  if(running){
    $('progress-fill').style.width=`${data.status.progress||0}%`;
    $('progress-percent').textContent=`${data.status.progress||0}%`;
    $('progress-text').textContent=`${data.status.processed||0} / ${data.status.total||0} analyzed`;
    $('progress-meta').textContent=data.status.current?`${data.status.current}: ${data.status.stage||'processing'} · ${data.status.model}`:'Initializing Gemini reasoning loop…';
  }
  if(data.status.last_error && !running && lastRunStatus){toast('Run issue: '+data.status.last_error,'error');}
  if(lastRunStatus && !running && !data.status.last_error){toast(`Agent finished. ${data.status.last_run_count} draft(s) saved.`,'success');}
  lastRunStatus=running;
}
function renderDashboard(){
  const st=data.stats;
  $('kpi-inbox').textContent=st.inbox;
  $('kpi-processed').textContent=st.processed;
  $('kpi-escalated').textContent=st.escalations;
  $('kpi-confidence').textContent=st.processed?st.average_confidence:'—';
  $('processed-sub').textContent=st.processed?`${Math.round(100*st.processed/Math.max(1,st.inbox))}% of inbox analyzed`:'Ready for processing';
  const p=st.priorities||{}, high=p.high||0, medium=p.medium||0, low=p.low||0,total=high+medium+low;
  const highPct=total?Math.round(100*high/total):0,medPct=total?Math.round(100*medium/total):0;
  $('donut').style.background=total?`conic-gradient(#f37375 0 ${highPct}%, #f8bd67 ${highPct}% ${highPct+medPct}%, #46c7aa ${highPct+medPct}% 100%)`:'conic-gradient(#e9edf3 0 100%)';
  $('donut-total').textContent=total;
  $('priority-legend').innerHTML=[['High',high,'high'],['Medium',medium,'medium'],['Low',low,'low']].map(([name,val,cls])=>`<div class="legend-row"><span class="legend-dot ${cls}"></span> ${name} <strong>${val}</strong></div>`).join('');
  $('recent-list').innerHTML=data.emails.length?data.emails.slice(0,5).map((m,i)=>`<div class="recent-item" data-email="${escapeHTML(m.email_id)}"><div class="person-avatar a${i%4}">${escapeHTML(initials(m.name))}</div><div class="recent-copy"><strong>${escapeHTML(m.subject)}</strong><span>${escapeHTML(m.name)}</span></div><span class="recent-id">${escapeHTML(m.email_id)}</span>${svg('arrow')}</div>`).join(''):`<div class="empty-state">${svg('inbox')}<strong>No emails found</strong><p>Add JSON emails to data/inbox to begin.</p></div>`;
  $('recent-events').innerHTML=data.logs.length?data.logs.slice(-4).reverse().map(e=>`<div class="event-row"><strong>${escapeHTML(friendlyEvent(e.event))}${e.email_id?' · '+escapeHTML(e.email_id):''}</strong><span>${escapeHTML(dateTime(e.timestamp))}</span></div>`).join(''):`<div class="empty-state">${svg('activity')}<strong>No activity yet</strong><p>Start the agent to see an audit trail of its decisions.</p></div>`;
}
function renderInbox(){
  const q=norm($('search-inbox').value).trim();
  const visible=data.emails.filter(m=>{
    const r=getResult(m.email_id);
    if(inboxFilter==='unprocessed'&&r)return false;
    if(inboxFilter==='escalated'&&(!r||!r.escalate))return false;
    if(inboxFilter==='high'&&(!r||r.priority!=='high'))return false;
    if(inboxFilter==='approved'&&getCase(m.email_id)?.status!=='approved')return false;
    if(inboxFilter==='sent'&&!['sent','provider_accepted'].includes(getCase(m.email_id)?.status))return false;
    return !q||norm([m.email_id,m.name,m.subject,m.body,r?.category,r?.priority].join(' ')).includes(q);
  });
  $('table-count').textContent=`Showing ${visible.length} of ${data.emails.length} conversations`;
  $('inbox-tbody').innerHTML=visible.map((m,i)=>{
    const r=getResult(m.email_id), status=statusBadge(getCase(m.email_id),r),unread=!data.seen?.[m.email_id];
    return `<tr data-email="${escapeHTML(m.email_id)}" class="${unread?'row-unread':''}"><td><div class="email-person"><div class="person-avatar a${i%4}">${escapeHTML(initials(m.name))}</div><div><strong>${unread?'<span class="unread-dot" aria-label="Unread"></span>':''}${escapeHTML(m.name)}</strong><small>${escapeHTML(m.email_id)}</small></div></div></td><td><div class="subject-main" title="${escapeHTML(m.subject)}">${escapeHTML(m.subject)}</div><div class="subject-small">${escapeHTML((m.body||'').slice(0,48))}…</div></td><td>${r?badge(r.category,'other'):'<span style="color:#bbc2ce">—</span>'}</td><td>${r?badge(r.priority,r.priority):'<span style="color:#bbc2ce">—</span>'}</td><td>${status}</td><td><span class="row-arrow">${svg('arrow')}</span></td></tr>`;
  }).join('')||`<tr><td colspan="6"><div class="empty-state">${svg('search')}<strong>No matching conversations</strong><p>Try another search or filter.</p></div></td></tr>`;
}
function renderDelivery(){
  const records=data.emails.filter(m=>getResult(m.email_id)||getCase(m.email_id));
  $('delivery-counter').textContent=`${records.length} prepared cases`;
  $('delivery-list').innerHTML=records.map(m=>{
    const c=getCase(m.email_id),r=getResult(m.email_id);
    return `<button class="delivery-case" data-email="${escapeHTML(m.email_id)}"><div><strong>${escapeHTML(m.subject)}</strong><p>${escapeHTML(m.name)} · ${escapeHTML(m.email_id)} · ${r?escapeHTML(r.category):'Awaiting triage'}</p></div><div class="delivery-right">${statusBadge(c,r)} ${svg('arrow')}</div></button>`;
  }).join('')||`<div class="empty-state">${svg('inbox')}<strong>Nothing to review yet</strong><p>Run the agent to create the first draft.</p></div>`;
}
function renderPolicy(){
  const q=norm($('policy-search').value).trim();
  $('policy-counter').textContent=`${data.policies.count||0} rules · policy v${data.policies.version||0}`;
  $('policy-history').innerHTML=(data.policies.history||[]).slice(0,8).map(v=>`<button class="version-chip" data-policy-version="${v.version}">Version ${v.version} · ${escapeHTML(dateTime(v.created_at))} · ${escapeHTML(v.notes)}</button>`).join('');
  $('policy-sections').innerHTML=(data.policies.sections||[]).map(s=>{
    const matches=s.rules.filter(r=>!q||norm([r.code,r.text,s.name].join(' ')).includes(q));
    if(!matches.length&&s.rules.length)return '';
    if(q&&!matches.length&&!norm(s.content).includes(q))return '';
    return `<article class="policy-section"><header><h3>${escapeHTML(s.name)}</h3><span>${matches.length} RULE${matches.length===1?'':'S'}</span></header>${matches.length?matches.map(r=>`<div class="rule-card"><code id="ref-${escapeHTML(r.code)}">${escapeHTML(r.code)}</code><p>${escapeHTML(r.text)}</p></div>`).join(''):`<p class="plain-content">${escapeHTML(s.content)}</p>`}</article>`;
  }).join('')||`<div class="empty-state">${svg('search')}<strong>No matching policies</strong><p>Try a different keyword or code.</p></div>`;
}
function friendlyEvent(event){return ({run_started:'Run started',email_received:'Email received',plan:'Agent planned next steps',policy_lookup:'Policy lookup completed',decision:'Reasoning decision',validated:'Safety checks passed',progress:'Processing queue',email_saved:'Draft saved to outbox',run_finished:'Autonomous run completed',api_retry:'Gemini API retry',processing_error:'Processing error',invalid_input:'Malformed email flagged'})[event]||String(event).replaceAll('_',' ');}
function summarizeEvent(e){const out=[];for(const k of ['email_id','category','priority','policy_refs','references','selected_sections','topics','notes','error','message','method','outbox','processed','escalations']){if(e[k]!==undefined&&e[k]!==null&&e[k]!=='')out.push(`${k.replaceAll('_',' ')}: ${Array.isArray(e[k])?e[k].join(', '):String(e[k])}`);}return out.join(' · ')||'Agent action recorded.';}
function renderLogs(){
  const q=norm($('log-search').value).trim();
  const events=data.logs.slice().reverse().filter(e=>!q||norm(JSON.stringify(e)).includes(q));
  $('log-count').textContent=`${events.length} recorded event${events.length===1?'':'s'}`;
  $('audit-timeline').innerHTML=(data.audit||[]).filter(e=>!q||norm(JSON.stringify(e)).includes(q)).map(e=>`<div class="timeline-item"><div class="timeline-top"><strong>${escapeHTML(friendlyEvent(e.action))}</strong>${e.email_id?`<span class="log-tag">${escapeHTML(e.email_id)}</span>`:''}<time>${escapeHTML(dateTime(e.happened_at))}</time></div><p>${escapeHTML(Object.entries(e.detail||{}).map(([k,v])=>`${k.replaceAll('_',' ')}: ${Array.isArray(v)?v.join(', '):String(v)}`).join(' · ')||'Saved system activity.')}</p></div>`).join('')||'<div class="empty-state">No permanent audit events yet.</div>';
  $('log-timeline').innerHTML=events.map(e=>`<div class="timeline-item ${/error|retry|invalid/.test(e.event)?'warning':''}"><div class="timeline-top"><strong>${escapeHTML(friendlyEvent(e.event))}</strong>${e.email_id?`<span class="log-tag">${escapeHTML(e.email_id)}</span>`:''}<time>${escapeHTML(dateTime(e.timestamp))}</time></div><p>${escapeHTML(summarizeEvent(e))}</p></div>`).join('')||`<div class="empty-state">${svg('activity')}<strong>No matching log entries</strong><p>Start a run to record transparent operational logs.</p></div>`;
}
function renderSettings(){
  const connected=!!data.status.has_key;
  $('settings-key-state').textContent=connected?'KEY LOADED':'NOT CONFIGURED';
  $('settings-key-state').className='connection-label'+(connected?' good':'');
  const g=data.status.gmail||{};const label=g.connected?`Connected as ${g.account||'Google account'}`:g.configured?'Credentials saved · not connected':'Not configured';
  $('gmail-connection').textContent=label+(g.connected&&g.send_permission_granted?' · send permission granted':g.connected?' · read access only':'');
  const toggle=$('toggle-delivery'),info=$('delivery-explain'),scope=$('scope-explain');
  if(g.enterprise_mode){
    toggle.hidden=false;
    toggle.textContent=g.delivery_requested?'Disable live delivery':'Enable live delivery';
    toggle.disabled=!(g.delivery_requested || (g.connected&&g.send_permission_granted));
    toggle.className=g.delivery_requested?'btn btn-secondary':'btn btn-primary';
    info.textContent=g.sending_enabled?'Enabled · Approved replies can be sent with Gmail.':g.delivery_requested?'Reconnect Gmail to restore send permission.':'Disabled · Outgoing mail requires administrator opt-in.';
    scope.textContent=g.connected&&!g.send_permission_granted?'Your current Gmail session has read-only permission. Click Connect / Reauthorize Gmail, approve the send permission and return here.':'Gmail sending requires an approved draft, explicit per-email confirmation and a provider receipt. No automatic bulk sending.';
  }else{
    toggle.hidden=true;info.textContent='Use RUN.bat to launch the protected enterprise workspace.';
    scope.textContent='Read-only local workspace. Enterprise delivery is unavailable in demo mode.';
  }
  $('gmail-connection').classList.toggle('ok',!!g.connected);
  $('gmail-fetch').disabled=!g.connected;
  if($('gmail-sync'))$('gmail-sync').disabled=!g.connected;
  $('gmail-disconnect').disabled=!g.connected;
  if(data.status.model){let option=[...$('model-field').options].find(x=>x.value===data.status.model);if(option)$('model-field').value=data.status.model;}
}
function openDrawer(id,recordOpen=true){
  const m=data.emails.find(x=>x.email_id===id);if(!m)return;
  activeDrawer=id;draftDirty=false;
  const r=getResult(id),c=getCase(id),refs=r?.policy_refs||[],status=humanStatus(c,r);
  const text=c?.draft??r?.reply_draft??'';
  const finalStates=['sent','provider_accepted','sending','send_uncertain'];
  const canEdit=!!c&&!finalStates.includes(status);
  const canApprove=!!c && !finalStates.includes(status) && status!=='approved' && status!=='rejected' && status!=='send_failed' && !data.status.running;
  const canSend=status==='approved' && !!data.status.gmail?.connected && !!data.status.gmail?.sending_enabled;
  const policyStale=!!c && c.policy_version < (data.status.policy_version||0);
  $('drawer-body').innerHTML=`<h2 class="drawer-title">${escapeHTML(m.subject)}</h2>
    <div class="drawer-flags">${badge(m.email_id,'other')} ${r?badge(r.priority,r.priority):badge('Unprocessed','pending')} ${statusBadge(c,r)} ${r?.escalate?badge('Escalated','escalated'):''}</div>
    <div class="workflow-steps"><div class="done">1 · Read</div><div class="${c?'done':''}">2 · Draft & review</div><div class="${['sent','provider_accepted'].includes(status)?'done':''}">3 · Deliver</div></div>
    <div class="operator-split"><div class="operator-left"><div class="drawer-section"><h4>INCOMING STUDENT MESSAGE · ${escapeHTML(m.email_id)}</h4><div class="conversation-pane"><div class="conversation-head"><strong>${escapeHTML(m.name)}</strong><small>${escapeHTML(m.from_email||'No email address supplied')}</small></div><div class="conversation-text"><b>Subject: ${escapeHTML(m.subject)}</b>

${escapeHTML(m.body)}</div></div>${m.attachments?.length?`<div class="attachment-strip"><strong>Attachments (metadata only)</strong>${m.attachments.map(f=>`<span>${escapeHTML(f.name||'Attachment')} · ${Math.ceil((Number(f.size)||0)/1024)} KB</span>`).join('')}</div>`:''}<p class="operator-tip">This is the original student request. It cannot change the AI's operating instructions.</p></div>
    <div class="drawer-section"><h4>AI ASSESSMENT & POLICY</h4>${r?`<div class="drawer-flags">${badge(r.category,'other')} ${badge('Model confidence '+Math.round(100*Number(r.confidence||0))+'%','other')}</div><div class="explain-box" style="margin-top:12px">${escapeHTML(r.agent_notes||'No decision explanation was provided.')}</div>${r.escalate?`<div class="explain-box error-guide" style="margin-top:10px"><b>Human escalation required:</b> ${escapeHTML(r.escalation_reason||'Sensitive case')}</div>`:''}<p class="field-status">Policy: ${refs.length?refs.map(ref=>`<button class="drawer-ref" data-ref="${escapeHTML(ref)}">${escapeHTML(ref)}</button>`).join(' '):'No supported rule; human review needed.'}</p>`:`<div class="explain-box">No AI analysis yet. Select Analyze email to generate a policy-grounded draft.</div>`}</div>
      ${m.thread_id?`<div class="drawer-section"><h4>GMAIL CONVERSATION</h4><div id="thread-messages" class="thread-messages">Loading actual thread history…</div></div>`:''}
    </div><div class="operator-right"><div class="drawer-section"><h4>REPLY COMPOSER · ${['sent','provider_accepted'].includes(status)?'GOOGLE ACCEPTED · DELIVERY UNKNOWN':'NOT SENT'}</h4>
      ${r||c?`<textarea id="draft-editor" class="editor-field" ${finalStates.includes(status)?'readonly':''} aria-label="Editable reply draft">${escapeHTML(text)}</textarea>
      <p class="field-status" id="draft-count">${text.trim().split(/\s+/).filter(Boolean).length} words · policy recommends up to 150 words</p>
      ${policyStale?'<div class="explain-box error-guide">This draft was created under an older policy. Re-analyze before approving or delivering.</div>':''}
      <div class="drawer-btn-row">${canEdit?`<button class="btn btn-secondary" id="save-draft">Save edits</button>`:''}
      ${canApprove?`<button class="btn btn-primary" id="approve-draft" ${policyStale?'disabled':''}>${svg('check')} Approve final draft</button>`:''}
      ${status==='approved'?`<span class="sent-proof">✓ Human-approved. Not sent to recipient.</span>`:''}
      ${canSend?`<button class="btn btn-primary" id="send-gmail">${svg('mail')} Quality check & send</button>`:''}
      ${status==='send_failed'?`<div class="explain-box error-guide">Gmail rejected the send. ${escapeHTML(c?.last_error||'')}<br/><button class="btn btn-secondary" id="retry-send">Review & authorize retry</button></div>`:''}
      ${status==='send_uncertain'?`<div class="explain-box error-guide">Gmail submission outcome is UNKNOWN. Check Gmail Sent folder before any resend. Automatic retries are blocked.</div>`:''}
      ${status==='sending'?`<div class="explain-box">Gmail send request is in progress. Do not refresh or submit again.</div>`:''}
      ${['sent','provider_accepted'].includes(status)?`<div class="sent-proof"><b>✓ Google accepted this message. Recipient inbox delivery is NOT verified.</b><br/>To: ${escapeHTML(c?.sent_to||'')}<br/>Sent: ${escapeHTML(dateTime(c?.sent_at))}<br/>Provider message ID: ${escapeHTML(c?.sent_message_id||'')}</div>`:''}
      <button class="btn btn-secondary" id="copy-reply">${svg('copy')} Copy</button></div>
      <p class="operator-tip">Delivery status comes from SQLite and Gmail API confirmation, never from a hardcoded UI flag. ${data.status.gmail?.sending_enabled?'Live delivery enabled. Confirm the recipient carefully before sending.':'Sending unavailable until Live Email Delivery is enabled and Gmail send permission is granted.'}</p>`:'<div class="explain-box">A draft appears here after you analyze this message.</div>'}
    </div></div></div><div class="drawer-section"><h4>CASE ACTIONS</h4><button class="btn btn-secondary" data-run-one="${escapeHTML(id)}" ${data.status.running?'disabled':''}>${svg('play')} ${r?'Re-analyze with current policy':'Analyze this email'}</button><button class="btn btn-secondary" id="view-case-audit" style="margin-left:8px">View case history</button></div>
    ${c?`<div class="drawer-section review-workspace"><h4>HUMAN REVIEW & ASSIGNMENT</h4>
      <p class="operator-tip">Internal actions are logged but never sent to the student.</p>
      <label for="case-department">Responsible department</label>
      <div class="case-action-row"><select id="case-department"><option value="">Choose office…</option>${['Accounts Office','Hostel Warden','Security','Harassment Committee','Campus Clinic / Counseling','Registrar Office','IT Helpdesk','IT Security','Academic Department','Examination Department','Other university office'].map(d=>`<option value="${escapeHTML(d)}" ${c.assigned_to===d?'selected':''}>${escapeHTML(d)}</option>`).join('')}</select><button class="btn btn-secondary" id="assign-case">Assign</button></div>
      ${c.assigned_to?`<p class="operator-tip">Currently assigned to: <b>${escapeHTML(c.assigned_to)}</b></p>`:''}
      <div class="case-action-row review-actions">${!finalStates.includes(status)?`<button class="btn btn-secondary" id="reject-case">Reject draft</button><button class="btn btn-secondary" id="reopen-case">Reopen case</button>`:''}</div>
      ${c.review_reason?`<div class="explain-box error-guide">Last review decision: ${escapeHTML(c.review_reason)}</div>`:''}
      <label for="case-note">Private case note</label><textarea id="case-note" rows="3" placeholder="Example: Awaiting response from the Warden. This note is for staff only."></textarea><button class="btn btn-secondary" id="add-case-note">Save internal note</button><div id="case-notes-container" class="note-list">Loading case notes…</div></div>`:''}`;
  if(c)loadNotes(id);
  if(m.thread_id)loadThread(id,m.thread_id);
  $('detail-drawer').classList.add('open');$('drawer-scrim').classList.add('open');
  if(recordOpen)request(`/api/cases/${encodeURIComponent(id)}/open`,{method:'POST'}).then(()=>{if(!data.seen)data.seen={};data.seen[id]=new Date().toISOString();}).catch(()=>{});
}
function closeDrawer(){activeDrawer=null;$('detail-drawer').classList.remove('open');$('drawer-scrim').classList.remove('open');}
async function runAgent(only=null){
  if(data.status.running){toast('An agent run is already in progress.','error');return;}
  if(!data.status.has_key){go('settings');toast('Connect your Gemini API key before running the agent.','error');$('api-key').focus();return;}
  closeDrawer();try{const r=await request('/api/run',{method:'POST',body:JSON.stringify({only})});toast(`Agent started: ${r.total} email(s) in autonomous processing.`,'success');go('dashboard');await refresh();}catch(e){toast(e.message,'error');}
}
async function refresh(){
  if(isRefreshing)return;isRefreshing=true;
  try{
    const [overview,policies,logs,audit]=await Promise.all([request('/api/overview'),request('/api/policy'),request('/api/logs'),request('/api/audit')]);
    data={...overview,policies,logs:logs.events||[],audit:audit.events||[]};renderGlobal();renderPage();
    if(activeDrawer&&$('detail-drawer').classList.contains('open')&&!data.status.running&&!draftDirty)openDrawer(activeDrawer,false);
  }catch(e){toast(e.message,'error');}finally{isRefreshing=false;}
}
function startDownload(path){window.location.href=path;}
function setup(){
  document.querySelectorAll('.nav-item').forEach(b=>b.addEventListener('click',()=>go(b.dataset.page)));
  document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>go(b.dataset.goto)));
  document.querySelectorAll('.start-btn').forEach(b=>b.addEventListener('click',()=>runAgent()));
  $('theme-switch').addEventListener('click',toggleTheme);
  $('mobile-menu').addEventListener('click',()=>$('sidebar').classList.toggle('open'));
  $('refresh-btn').addEventListener('click',()=>{refresh().then(()=>toast('Dashboard refreshed','success'));});
  $('search-inbox').addEventListener('input',renderInbox);
  $('policy-search').addEventListener('input',renderPolicy);
  $('log-search').addEventListener('input',renderLogs);
  $('log-refresh').addEventListener('click',refresh);
  $('analytics-refresh').addEventListener('click',()=>loadAnalytics(true));
  $('audit-refresh').addEventListener('click',refresh);
  document.querySelectorAll('#inbox-filters .pill').forEach(b=>b.addEventListener('click',()=>{inboxFilter=b.dataset.filter;document.querySelectorAll('#inbox-filters .pill').forEach(x=>x.classList.toggle('active',x===b));renderInbox();}));
  document.addEventListener('click',(e)=>{
    const row=e.target.closest('[data-email]');if(row)openDrawer(row.dataset.email);
    const ref=e.target.closest('[data-ref]');if(ref){closeDrawer();go('policy');$('policy-search').value=ref.dataset.ref;renderPolicy();}
    const runOne=e.target.closest('[data-run-one]');if(runOne)runAgent(runOne.dataset.runOne);
    if(e.target.closest('#copy-reply')){const draft=$('draft-editor')?.value;if(draft)navigator.clipboard.writeText(draft).then(()=>toast('Reply copied','success')).catch(()=>toast('Copy not supported','error'));}
    if(e.target.closest('#save-draft'))saveDraft();
    if(e.target.closest('#approve-draft'))approveDraft();
    if(e.target.closest('#send-gmail'))sendDraft();
    if(e.target.closest('#retry-send'))retryDraft();
    if(e.target.closest('#assign-case'))assignCase();
    if(e.target.closest('#add-case-note'))addCaseNote();
    if(e.target.closest('#reject-case'))reviewCase('reject');
    if(e.target.closest('#reopen-case'))reviewCase('reopen');
    if(e.target.closest('#view-case-audit')){const id=activeDrawer;closeDrawer();go('logs');$('log-search').value=id||'';renderLogs();}
    const version=e.target.closest('[data-policy-version]');if(version)showPolicyVersion(version.dataset.policyVersion);
  });
  $('drawer-body').addEventListener('input',e=>{if(e.target.id==='draft-editor'){draftDirty=true;$('draft-count').textContent=`${e.target.value.trim().split(/\s+/).filter(Boolean).length} words · save edits before approving`;}});
  $('close-drawer').addEventListener('click',closeDrawer);$('drawer-scrim').addEventListener('click',closeDrawer);
  $('add-btn').addEventListener('click',()=>openModal());
  $('modal-close').addEventListener('click',closeModal);$('modal-cancel').addEventListener('click',closeModal);
  $('modal-scrim').addEventListener('click',e=>{if(e.target===$('modal-scrim'))closeModal();});
  $('compose-form').addEventListener('submit',async e=>{
    e.preventDefault();const payload=Object.fromEntries(new FormData(e.target).entries());if(!payload.email_id)delete payload.email_id;
    try{const result=await request('/api/emails',{method:'POST',body:JSON.stringify(payload)});closeModal();e.target.reset();await refresh();toast(`Email ${result.email_id} added to inbox`,'success');go('inbox');}catch(err){toast(err.message,'error');}
  });
  $('import-btn').addEventListener('click',()=>$('json-file-input').click());
  $('gmail-inbox-btn').addEventListener('click',async()=>{if(!data.status.gmail?.connected){go('settings');toast('Connect Gmail OAuth first to import real Gmail messages.','error');return;}await importGmail();});
  $('json-file-input').addEventListener('change',async e=>{
    const file=e.target.files[0];if(!file)return;try{
      const raw=JSON.parse(await file.text());const result=await request('/api/import',{method:'POST',body:JSON.stringify(raw)});
      await refresh();toast(`Imported ${result.added.length} email(s)${result.errors.length?' · '+result.errors.length+' skipped':''}`,result.added.length?'success':'error');
      if(result.errors.length)toast(result.errors.slice(0,2).join('; '),'error');
    }catch(err){toast('Import failed: '+err.message,'error');}finally{e.target.value='';}
  });
  $('key-toggle').addEventListener('click',()=>{const f=$('api-key');f.type=f.type==='password'?'text':'password';$('key-toggle').textContent=f.type==='password'?'Show':'Hide';});
  $('key-form').addEventListener('submit',async e=>{
    e.preventDefault();const key=$('api-key').value.trim();const model=$('model-field').value;
    try{await request('/api/credentials',{method:'POST',body:JSON.stringify({api_key:key,model})});$('api-key').value='';await refresh();toast('Gemini key saved in memory (verification happens at first run).','success');}catch(err){toast(err.message,'error');}
  });
  setupPro(); setupV3();
  [['export-main','/api/download/outbox'],['export-secondary','/api/download/outbox'],['download-logs','/api/download/logs'],['download-audit','/api/download/audit'],['export-logs-secondary','/api/download/logs']].forEach(([id,url])=>$(id).addEventListener('click',()=>{if(url.includes('outbox')&&!data.results.length)return toast('Run the agent to generate outbox.json','error');if(url.includes('logs')&&!data.logs.length)return toast('No logs to export yet','error');startDownload(url);}));
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeModal();}});
  bootstrap();setInterval(()=>{if(authReady)refresh()},4000);
}
function openModal(){$('modal-scrim').classList.add('open');setTimeout(()=>$('compose-form').elements.from_name.focus(),80);}
function closeModal(){$('modal-scrim').classList.remove('open');}
document.addEventListener('DOMContentLoaded',setup);

async function saveDraft(){
  const id=activeDrawer,value=$('draft-editor')?.value.trim();if(!id||!value)return toast('Write a reply before saving.','error');
  if(value.split(/\s+/).length>150)return toast('Reply exceeds 150 words. Shorten it before saving.','error');
  try{await request(`/api/cases/${encodeURIComponent(id)}/draft`,{method:'PUT',body:JSON.stringify({draft:value})});draftDirty=false;await refresh();openDrawer(id,false);toast('Draft saved. Re-approval is required.','success');}
  catch(e){toast(friendlyError(e.message),'error');}
}
async function approveDraft(){
  const id=activeDrawer;if(!id)return;
  if(draftDirty)return toast('You changed the draft. Save edits before approval.','error');
  if(!await askConfirm('Approve this exact draft? Approval does NOT send an email.',{title:'Approve draft',confirmText:'Approve'}))return;
  try{await request(`/api/cases/${encodeURIComponent(id)}/approve`,{method:'POST'});await refresh();openDrawer(id,false);toast('Draft approved, not sent.','success');}catch(e){toast(friendlyError(e.message),'error');}
}
async function sendDraft(){
  const id=activeDrawer;if(!id)return;
  const selected=data.emails.find(e=>e.email_id===id);
  if(!await askConfirm(`Send the approved reply to ${selected?.from_email||'unknown recipient'}? Gmail will transmit a REAL email.`,{title:'Confirm recipient & content',confirmText:'Continue',danger:true}))return;
  const phrase=await askText('Type SEND to submit this approved reply through Gmail:',{title:'Authorize live delivery',confirmText:'Send email',requiredText:'SEND',danger:true});if(phrase!=='SEND')return toast('Delivery cancelled.','info');
  try{const res=await request(`/api/cases/${encodeURIComponent(id)}/send`,{method:'POST',body:JSON.stringify({confirmation:phrase})});await refresh();openDrawer(id,false);toast(`Google accepted message ${res.provider_message_id}. Inbox delivery is not yet verified.`,'success');}
  catch(e){await refresh();if(activeDrawer===id)openDrawer(id,false);toast('Send not confirmed: '+friendlyError(e.message),'error');}
}
function openPolicyEditor(){ $('policy-modal').classList.add('open');$('policy-text').value=data.policies.raw||'';$('policy-notes').value='';}
function closePolicyEditor(){ $('policy-modal').classList.remove('open'); }
async function showPolicyVersion(version){
  try{const v=await request(`/api/policy/versions/${version}`);openPolicyEditor();$('policy-text').value=v.content;$('policy-notes').value=`Restore or adapt version ${v.version}: ${v.notes}`;toast(`Viewing historical policy v${v.version}. Publishing creates a new version.`,'info');}catch(e){toast(e.message,'error');}
}

async function loadThread(id,threadId){
  try{
    const payload=await request(`/api/gmail/threads/${encodeURIComponent(threadId)}`);
    if(activeDrawer!==id)return;
    const target=$('thread-messages');if(!target)return;
    target.innerHTML=`<p class="operator-tip">${payload.source==='gmail'?'Live Gmail thread':'Cached thread metadata'} · ${payload.messages.length} message(s)</p>`+
      (payload.messages.map(m=>`<article class="thread-note"><strong>${escapeHTML(m.from_name||m.sender||'Sender')}</strong> <small>${escapeHTML(dateTime(m.received_at))}</small><div>${escapeHTML(m.subject||'')}</div><p>${escapeHTML((m.body||m.snippet||'').slice(0,1500))}</p></article>`).join('')||'<p>No previous messages in this conversation.</p>');
  }catch(err){if(activeDrawer===id&&$('thread-messages'))$('thread-messages').textContent='Conversation could not be loaded: '+friendlyError(err.message)}
}
async function syncGmail(){
  try{
    const result=await request('/api/gmail/sync',{method:'POST',body:JSON.stringify({limit:50})});
    await refresh();toast(`Gmail synced · ${result.imported.length} new message(s), ${result.skipped.length} already seen.`, 'success');
  }catch(err){toast('Gmail sync failed: '+friendlyError(err.message),'error')}
}
async function retryDraft(){
  const id=activeDrawer;if(!id)return;
  const reason=await askText('A definitive rejection occurred. Explain why another attempt is safe. Never retry an uncertain send.',
    {title:'Authorize retry after failure',confirmText:'Reset to approved',danger:true});
  if(!reason)return;
  try{
    await request(`/api/cases/${encodeURIComponent(id)}/retry-send`,{method:'POST',body:JSON.stringify({reason})});
    await refresh();openDrawer(id,false);toast('Failure reset for a manual retry. Quality validation will run again.','success');
  }catch(err){toast(friendlyError(err.message),'error')}
}

async function importGmail(){
  if(!await askConfirm('Import the latest 10 messages from connected Gmail inbox? This operation reads messages only; it does not send mail.',{title:'Import Gmail',confirmText:'Import'}))return;
  try{const r=await request('/api/gmail/import',{method:'POST',body:JSON.stringify({limit:10})});await refresh();toast(`Imported ${r.imported.length} Gmail messages; ${r.skipped.length} already existed/skipped.`,'success');go('inbox');}catch(e){toast(friendlyError(e.message),'error');}
}
function setupPro(){
  $('edit-policy-btn').addEventListener('click',openPolicyEditor);
  $('policy-modal-close').addEventListener('click',closePolicyEditor);
  $('policy-cancel').addEventListener('click',closePolicyEditor);
  $('policy-form').addEventListener('submit',async e=>{
    e.preventDefault();if(!await askConfirm('Publish this policy? Older drafts will need re-analysis and approval again.',{title:'Publish policy',confirmText:'Publish'}))return;
    try{const res=await request('/api/policy',{method:'POST',body:JSON.stringify({content:$('policy-text').value,notes:$('policy-notes').value})});closePolicyEditor();await refresh();toast(`Policy v${res.version} published with ${res.count} rules.`,'success');}catch(e){toast(friendlyError(e.message),'error');}
  });
  $('gmail-form').addEventListener('submit',async e=>{
    e.preventDefault();try{await request('/api/gmail/configure',{method:'POST',body:JSON.stringify({client_id:$('gmail-id').value.trim(),client_secret:$('gmail-secret').value.trim()})});$('gmail-secret').value='';await refresh();toast('OAuth credentials stored only in server memory. Press Connect Gmail to authorize.','success');}catch(e){toast(friendlyError(e.message),'error');}
  });
  $('gmail-connect').addEventListener('click',async()=>{try{const r=await request('/api/gmail/authorize');window.location.href=r.url;}catch(e){toast(friendlyError(e.message),'error');}});
  $('gmail-fetch').addEventListener('click',importGmail);
  $('gmail-sync')?.addEventListener('click',syncGmail);
  $('toggle-delivery').addEventListener('click',async()=>{
    const g=data.status.gmail||{};const enabled=!g.delivery_requested;
    if(!await askConfirm(enabled?'Enable REAL Gmail sending for individually approved replies? This grants no automatic sending permission.':'Disable Gmail sending? Existing sent receipts stay in history.',{title:enabled?'Enable live delivery':'Disable live delivery',confirmText:enabled?'Enable':'Disable',danger:enabled}))return;
    try{await request('/api/gmail/delivery',{method:'POST',body:JSON.stringify({enabled})});await refresh();toast(enabled?'Live delivery enabled for approved cases.':'Live delivery disabled.','success');}
    catch(e){toast(friendlyError(e.message),'error');}
  });
  $('gmail-disconnect').addEventListener('click',async()=>{if(!await askConfirm('Disconnect current Gmail session?',{title:'Disconnect Gmail',confirmText:'Disconnect',danger:true}))return;try{await request('/api/gmail/disconnect',{method:'POST'});await refresh();toast('Gmail disconnected.','success');}catch(e){toast(e.message,'error');}});
  const gmailMessage=new URLSearchParams(location.search).get('gmail');if(gmailMessage){setTimeout(()=>{go('settings');toast(gmailMessage==='connected'?'Gmail connected successfully.':gmailMessage==='denied'?'Google authorization was declined.':'Gmail OAuth connection failed. Check credentials and audit log.',gmailMessage==='connected'?'success':'error');},750);history.replaceState(null,'','/');}
}


// V3: persistent statistics, true SSE events, light/dark, policy governance, staff workflow.
function colorIndex(i){return ['#6965e8','#1caa92','#ee9d5c','#df6d8b','#6c9ddd','#9d87df'][i%6];}
function analyticsBars(items){
  const sorted=Object.entries(items||{}).sort((a,b)=>b[1]-a[1]);
  const max=Math.max(1,...sorted.map(x=>x[1]));
  if(!sorted.length)return '<div class="empty-state">No analyzed cases yet. Run the agent to populate this chart.</div>';
  return sorted.map(([label,n],i)=>`<div class="analytics-row"><div class="analytics-label"><strong>${escapeHTML(label.replaceAll('_',' '))}</strong><span>${Number(n)}</span></div><div class="analytics-track"><div class="analytics-fill" style="width:${Math.max(2,100*n/max)}%;background:${colorIndex(i)}"></div></div></div>`).join('');
}
async function loadAnalytics(force=false){
  if(!force&&Date.now()-lastAnalyticsFetch<8000&&latestAnalytics)return;
  try{latestAnalytics=await request('/api/analytics');lastAnalyticsFetch=Date.now();if(selectedPage==='analytics')renderAnalytics();}
  catch(e){if(force)toast(friendlyError(e.message),'error');}
}
function renderAnalytics(){
  if(!latestAnalytics){loadAnalytics();return;}
  const a=latestAnalytics;
  $('an-escalation').textContent=a.escalation_rate+'%';
  $('an-review').textContent=a.human_review;
  $('an-sent').textContent=a.provider_deliveries;
  $('an-errors').textContent=`${a.failures} / ${a.retries}`;
  $('analytics-status-bars').innerHTML=analyticsBars(a.status);
  $('analytics-categories').innerHTML=analyticsBars(a.categories);
  const days=a.daily_activity||[];const max=Math.max(1,...days.map(x=>x.n));
  $('analytics-daily').innerHTML=days.length?days.map(d=>`<div class="day-col" title="${escapeHTML(d.day)} · ${d.n} draft events"><span>${d.n}</span><div style="height:${Math.max(4,d.n/max*128)}px"></div><small>${escapeHTML(d.day.slice(5))}</small></div>`).join(''):'<div class="empty-state">Daily activity appears when actual drafts are generated.</div>';
}
function toggleTheme(){
  const dark=document.documentElement.dataset.theme==='dark';
  document.documentElement.dataset.theme=dark?'light':'dark';
  themeWrite(document.documentElement.dataset.theme);
  $('theme-switch').title=`Switch to ${dark?'dark':'light'} theme`;
}
function liveEntry(e){
  const who=e.email_id?` · ${e.email_id}`:'';
  const info=Object.entries(e.detail||{}).filter(([k])=>!/(token|secret|body|draft|key)/i.test(k)).slice(0,4).map(([k,v])=>`${k.replaceAll('_',' ')}: ${Array.isArray(v)?v.join(', '):typeof v==='object'?JSON.stringify(v):v}`).join(' · ');
  return `<div class="live-entry"><time>${escapeHTML(dateTime(e.happened_at))}</time><div><strong>${escapeHTML(friendlyEvent(e.action.replace(/^agent_/,''))+who)}</strong><p>${escapeHTML(info||'Activity recorded in the audit trail.')}</p></div></div>`;
}
function renderLive(entries){
  const box=$('live-event-list'); if(!box)return;
  box.innerHTML=entries.length?entries.slice(0,7).map(liveEntry).join(''):'<div class="empty-state">Waiting for real backend actions. Start the agent to see decisions.</div>';
}
function connectStream(){
  if(liveStream)liveStream.close();
  const recent=data.audit||[];
  if(recent.length){lastStreamId=Math.max(lastStreamId,...recent.map(e=>Number(e.id)||0));renderLive(recent);}
  liveStream=new EventSource('/api/events?after='+lastStreamId);
  liveStream.onopen=()=>{$('stream-state').textContent='Live connection active';$('stream-state').classList.add('online');};
  liveStream.onerror=()=>{$('stream-state').textContent='Reconnecting to local server…';$('stream-state').classList.remove('online');};
  liveStream.addEventListener('audit',e=>{
    let item;try{item=JSON.parse(e.data)}catch{return;}
    lastStreamId=Math.max(lastStreamId,Number(item.id)||0);
    data.audit=[item,...(data.audit||[]).filter(a=>a.id!==item.id)].slice(0,700);
    renderLive(data.audit);
    if(selectedPage==='logs')renderLogs();
    if(['ai_draft_created','agent_email_saved','operator_approved','gmail_send_confirmed','case_assigned','policy_version_published'].includes(item.action)){loadAnalytics(true);}
  });
}
async function bootstrap(){
  document.documentElement.dataset.theme=themeRead()==='dark'?'dark':'light';
  try{
    const s=await request('/api/session');
    if(s.required&&!s.authenticated){$('login-screen').classList.remove('hidden');authReady=false;return;}
    authReady=true;await refresh();connectStream();loadAnalytics();
  }catch(e){toast('Cannot load session: '+friendlyError(e.message),'error');}
}
function setupV3(){
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();$('login-message').textContent='Signing in…';
    try{await request('/api/login',{method:'POST',body:JSON.stringify({password:$('login-password').value})});$('login-password').value='';$('login-screen').classList.add('hidden');$('login-message').textContent='';authReady=true;await refresh();connectStream();loadAnalytics(true);toast('Administrator signed in','success');}
    catch(err){$('login-message').textContent=friendlyError(err.message);}
  });
  $('download-audit-csv').addEventListener('click',()=>startDownload('/api/download/audit.csv'));
  $('policy-upload-btn').addEventListener('click',()=>$('policy-file-input').click());
  $('policy-file-input').addEventListener('change',async e=>{
    const file=e.target.files[0];if(!file)return;
    try{if(file.size>120000)throw new Error('File exceeds 120 KB.');
      if(file.name.toLowerCase().endsWith('.docx')){
        const buf=new Uint8Array(await file.arrayBuffer());let str='';for(const byte of buf)str+=String.fromCharCode(byte);
        if(!await askConfirm('Import Word policy and publish a new version? The server will validate the rule codes and preserve history.',{title:'Import policy',confirmText:'Import'}))return;
        const response=await request('/api/policy/import-text',{method:'POST',body:JSON.stringify({filename:file.name,content_base64:btoa(str),notes:'Imported and reviewed '+file.name})});await refresh();toast(`Word policy v${response.version} published with ${response.count} rules.`,'success');
      }else{const text=await file.text();openPolicyEditor();$('policy-text').value=text;$('policy-notes').value='Imported and reviewed '+file.name;toast('Policy loaded into editor. Review before publishing.','info');}}
    catch(err){toast(friendlyError(err.message),'error');}finally{e.target.value='';}
  });
  $('policy-diff-btn').addEventListener('click',()=>{
    const history=data.policies.history||[];if(history.length<2)return toast('At least two published policy versions are needed.','error');
    for(const id of ['diff-old','diff-new'])$(id).innerHTML=history.map(h=>`<option value="${h.version}">Version ${h.version} · ${escapeHTML(h.notes)}</option>`).join('');
    $('diff-old').value=history[1].version;$('diff-new').value=history[0].version;
    $('policy-diff-modal').classList.add('open');comparePolicy();
  });
  $('policy-diff-close').addEventListener('click',()=>$('policy-diff-modal').classList.remove('open'));
  $('diff-compare').addEventListener('click',comparePolicy);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape')$('policy-diff-modal').classList.remove('open');
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();go('inbox');$('search-inbox').focus();}
  });
}
async function comparePolicy(){
  try{const d=await request(`/api/policy/diff?old=${$('diff-old').value}&new=${$('diff-new').value}`);
    $('diff-content').textContent=d.diff||'No text difference found.';}
  catch(e){$('diff-content').textContent=friendlyError(e.message);}
}
async function loadNotes(id){
  try{const r=await request(`/api/cases/${encodeURIComponent(id)}/notes`);
    const target=$('case-notes-container');if(!target||activeDrawer!==id)return;
    target.innerHTML=r.notes?.length?r.notes.map(n=>`<div class="case-note"><time>${escapeHTML(dateTime(n.created_at))}</time><p>${escapeHTML(n.text)}</p></div>`).join(''):'<p class="operator-tip">No staff notes yet.</p>';
  }catch(e){const target=$('case-notes-container');if(target&&activeDrawer===id)target.textContent=friendlyError(e.message);}
}
async function assignCase(){
  const id=activeDrawer,department=$('case-department')?.value;
  if(!id||!department)return toast('Choose the responsible department.','error');
  try{await request(`/api/cases/${encodeURIComponent(id)}/assignment`,{method:'PUT',body:JSON.stringify({department})});await refresh();openDrawer(id,false);toast('Case assignment saved and audited.','success');}
  catch(e){toast(friendlyError(e.message),'error');}
}
async function addCaseNote(){
  const id=activeDrawer,text=$('case-note')?.value.trim();if(!text)return toast('Write an internal note.','error');
  try{await request(`/api/cases/${encodeURIComponent(id)}/notes`,{method:'POST',body:JSON.stringify({text})});await loadNotes(id);$('case-note').value='';toast('Private note saved in the case history.','success');}
  catch(e){toast(friendlyError(e.message),'error');}
}
async function reviewCase(action){
  const id=activeDrawer;if(!id)return;
  const reason=await askText(action==='reject'?'Why was this draft rejected?':'Why should this case be reopened?',{title:action==='reject'?'Reject draft':'Reopen case',confirmText:'Save decision'});if(reason===null)return;
  if(reason.trim().length<4)return toast('Explain the reason in at least 4 characters.','error');
  try{await request(`/api/cases/${encodeURIComponent(id)}/review`,{method:'POST',body:JSON.stringify({action,reason})});await refresh();openDrawer(id,false);toast('Review decision saved; nothing was sent.','success');}
  catch(e){toast(friendlyError(e.message),'error');}
}

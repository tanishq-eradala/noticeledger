'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {fund:'Fund',investor:'Investor',notice_type:'Notice type',currency:'Currency',notice_date:'Notice date',effective_date:'Due / payment date',amount:'Total amount',investment_amount:'Investment component',fee_amount:'Fee component',recallable_amount:'Recallable amount'};
const statusNames = {needs_review:'Needs review',approved:'Approved',rejected:'Rejected',duplicate:'Possible duplicate',failed:'Extraction failed'};
const state = {docs:[],snapshot:{counts:{},ledger:[],jobs:[],totals:[]},doc:null,view:'review',filter:'all',search:'',busy:false,actor:sessionStorage.getItem('noticeledger.actor')||''};
let selectSequence = 0, dialogOpener = null, pendingReview = null;
const readableType = (t) => t === 'capital_call' ? 'Capital call' : t === 'distribution' ? 'Distribution' : 'Unclassified';
const amount = (minor,currency) => new Intl.NumberFormat('en-US',{style:'currency',currency:currency||'USD'}).format(minor/100);
const when = (v) => new Date(v).toLocaleString('en-US',{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
function message(text,error=false){$('message').textContent=text;$('message').className=error?'error':'';}
async function api(path,body){
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);
  try{
    const r=await fetch(path,{signal:controller.signal, ...(body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const data=await r.json();if(!r.ok){const e=new Error(data.error||'Request failed.');e.status=r.status;throw e;}return data;
  }catch(e){if(e.name==='AbortError')throw new Error('Request timed out. Retry the action; approvals cannot be recorded twice.');if(e instanceof TypeError)throw new Error('Cannot reach the workspace. Check the local server and retry.');throw e;}finally{clearTimeout(timer);}
}
function badge(d){const cls=d.status==='approved'?'accepted':d.status==='failed'||d.status==='duplicate'||d.issues.length?'warning':d.status==='rejected'?'muted':'';return `<span class="badge ${cls}">${esc(d.status==='needs_review'&&d.issues.length?`${d.issues.length} ${d.issues.length===1?'exception':'exceptions'}`:statusNames[d.status])}</span>`;}
function saveRoute(){const u=new URL(location.href);u.searchParams.set('view',state.view);if(state.doc)u.searchParams.set('notice',state.doc.id);history.replaceState(null,'',u);}
function dirty(){if(!state.doc||!$('fields'))return false;const f=draft();return Object.entries(f).some(([k,v])=>v!==String(state.doc.fields[k]??''));}
function leaveDraft(){return !dirty()||window.confirm('Discard unsaved corrections?');}
async function load(preferred){
  const [docs,snapshot]=await Promise.all([api('/api/documents'),api('/api/snapshot')]);state.docs=docs;state.snapshot=snapshot;
  const selected=docs.find(d=>d.id===preferred)||docs.find(d=>d.status==='needs_review'&&d.issues.length)||docs.find(d=>d.status==='needs_review')||docs[0];
  state.doc=selected?await api(`/api/documents/${selected.id}`):null;render();saveRoute();
}
async function selectDoc(id){
  if(!leaveDraft())return;const seq=++selectSequence;
  try{const d=await api(`/api/documents/${id}`);if(seq!==selectSequence)return;state.doc=d;state.view='review';render();saveRoute();}catch(e){message(e.message,true);}
}
function render(){
  const heads={review:['Notice review','Check the source. Resolve exceptions. Record approved data.'],ledger:['Approved cash flows','Only reviewed and approved notices appear here. Amounts stay separate by currency.'],jobs:['Processing history','Inspect acquisition results and extraction failures before they affect reporting.']};
  $('page-title').textContent=heads[state.view][0];$('page-description').textContent=heads[state.view][1];document.title=`${heads[state.view][0]} | NoticeLedger`;
  document.querySelectorAll('[data-view]').forEach(b=>{if(b.dataset.view===state.view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  $('nav-count').textContent=state.snapshot.counts.needs_review||0;
  if(state.view==='review')renderReview();else if(state.view==='ledger')renderLedger();else renderJobs();
}
function renderReview(){
  const counts=state.snapshot.counts;const exceptions=state.docs.filter(d=>d.status==='needs_review'&&d.issues.length).length;
  $('content').innerHTML=`<div class="summary-strip"><span><strong>${counts.needs_review||0}</strong>awaiting review</span><span class="warn"><strong>${exceptions}</strong>with exceptions</span><span><strong>${counts.approved||0}</strong>approved</span><span><strong>${counts.duplicate||0}</strong>possible duplicates</span></div><div class="review-layout"><section class="queue" aria-label="Notice queue"><div class="queue-controls"><h2>Incoming notices</h2><input id="search" aria-label="Search notices" placeholder="Search fund or source" value="${esc(state.search)}"><select id="status-filter" aria-label="Filter notices"><option value="all">All notices</option><option value="needs_review">Needs review</option><option value="exceptions">With exceptions</option><option value="duplicate">Possible duplicates</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="failed">Extraction failed</option></select></div><div class="queue-list" id="queue-list"></div></section><section class="worksheet" aria-label="Extracted notice fields" id="worksheet"></section><aside class="evidence-panel" aria-label="Source and review history" id="evidence"></aside></div>`;
  $('status-filter').value=state.filter;$('search').oninput=e=>{state.search=e.target.value;renderQueue();};$('status-filter').onchange=e=>{state.filter=e.target.value;renderQueue();};renderQueue();renderWorksheet();renderEvidence();
}
function renderQueue(){
  const query=state.search.trim().toLowerCase();const docs=state.docs.filter(d=>(state.filter==='all'||state.filter==='exceptions'&&d.status==='needs_review'&&d.issues.length||d.status===state.filter)&&`${d.fields.fund||''} ${d.source_name} ${d.fields.investor||''}`.toLowerCase().includes(query));
  $('queue-list').innerHTML=docs.length?docs.map(d=>`<button class="queue-item" data-notice="${esc(d.id)}" aria-pressed="${d.id===state.doc?.id}"><span class="fund">${esc(d.fields.fund||'Unidentified fund')}</span><span class="queue-meta"><span>${esc(readableType(d.fields.notice_type))}</span><span>${esc(d.fields.currency||'')} ${esc(d.fields.amount||'Missing amount')}</span></span>${badge(d)}<small>${esc(d.source_name)}</small></button>`).join(''):'<p class="empty">No matching notices.<br>Change the filter or import a source.</p>';
  $('queue-list').querySelectorAll('[data-notice]').forEach(b=>b.onclick=()=>selectDoc(b.dataset.notice));
}
function fieldHtml(k){
  const d=state.doc;const errors=d.issues.filter(e=>e.field===k);const ev=d.evidence[k]||[];const value=d.fields[k]||'';const original=d.original_fields[k]||'';const editable=['needs_review','duplicate'].includes(d.status);
  let control;
  if(k==='notice_type'||k==='currency'){
    const options=k==='notice_type'?[['','Choose type'],['capital_call','Capital call'],['distribution','Distribution']]:[['','Choose currency'],['USD','USD'],['EUR','EUR'],['GBP','GBP']];
    if(value&&!options.some(([v])=>v===value))options.push([value,value]);
    control=`<select id="field-${k}" name="${k}" ${editable?'':'disabled'} aria-invalid="${!!errors.length}" ${errors.length?`aria-describedby="error-${k}"`:''}>${options.map(([v,t])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(t)}</option>`).join('')}</select>`;
  }else control=`<input id="field-${k}" name="${k}" value="${esc(value)}" maxlength="250" ${editable?'':'readonly'} aria-invalid="${!!errors.length}" ${errors.length?`aria-describedby="error-${k}"`:''} ${k.includes('amount')?'inputmode="decimal"':''}>`;
  return `<div class="field ${k==='fund'||k==='investor'?'wide':''}"><div class="label-row"><label for="field-${k}">${labels[k]}</label>${ev.length?`<button type="button" class="source-link" data-line="${ev[0].line}" aria-label="Source line ${ev[0].line} for ${labels[k]}">Source line ${ev[0].line}</button>`:'<span class="subtle">Not found in source</span>'}</div>${control}${errors.length?`<small class="error" id="error-${k}">${errors.map(e=>esc(e.message)).join(' ')}</small>`:''}${original!==value?`<small class="original">Originally extracted: ${esc(original||'not found')}</small>`:''}</div>`;
}
function renderWorksheet(){
  const d=state.doc;if(!d){$('worksheet').innerHTML='<p class="empty">Import a notice to begin reviewing its values.</p>';return;}
  const editable=['needs_review','duplicate'].includes(d.status);let extra='';
  if(d.status==='duplicate')extra=`<div class="checks"><strong>A possible duplicate was found.</strong><p>Compare the existing notice before approving. Correct distinct transaction details or reject this copy.</p><button id="compare-duplicate" class="quiet">Open matching notice</button></div>`;
  if(d.status==='failed')extra='<div class="checks"><strong>Source extraction failed.</strong><p>Check the original PDF, then import verified text. Scanned notices need OCR, which is outside this prototype.</p></div>';
  const check=d.issues.length?`<div class="checks"><strong>${d.issues.length} ${d.issues.length===1?'check needs':'checks need'} attention</strong>${d.issues.map(e=>`<p><a href="#field-${esc(e.field)}">${esc(labels[e.field]||'Source')}: ${esc(e.message)}</a></p>`).join('')}</div>`:`<div class="checks pass"><strong>Automated checks passed</strong><p>Compare the extracted values with the source before approval.</p></div>`;
  $('worksheet').innerHTML=`<div class="notice-header"><div class="badges">${badge(d)}<span class="badge muted">Version ${d.version}</span></div><h2>${esc(d.fields.fund||'Unidentified fund')}</h2><p>${esc(d.fields.investor||'Investor not found')}</p></div><div class="amount-banner"><div><span>${esc(readableType(d.fields.notice_type))}</span><br><strong>${esc(d.fields.currency||'')} ${esc(d.fields.amount||'Amount not found')}</strong></div><span>${esc(d.fields.effective_date||'Date missing')}</span></div>${extra}${d.status==='failed'?'':check}<form id="fields"><div class="field-grid">${Object.keys(labels).map(fieldHtml).join('')}</div></form>${editable?`<div class="reviewer"><label for="actor">Reviewer name</label><input id="actor" placeholder="Your name" maxlength="80" value="${esc(state.actor)}"><div class="correction-note" id="correction-note"><label for="correction-reason">Reason for corrections or source confirmation</label><textarea id="correction-reason" rows="2" maxlength="1000" placeholder="Explain what changed and why"></textarea></div><p class="hint" id="draft-hint">Approval records data in the ledger; it does not initiate a payment.</p><div class="actions"><button id="save-corrections" disabled>Save corrections</button><button id="approve-open" class="primary" ${d.issues.length||d.status==='duplicate'?'disabled':''}>Approve notice</button><button id="reject-open" class="quiet danger">Reject notice</button></div></div>`:`<p class="hint">${d.status==='approved'?'This notice is approved and immutable. Its cash flow is in the ledger.':d.status==='rejected'?'This notice was rejected. The source and review history are retained.':'Import verified source text to continue.'}</p>`}`;
  $('fields').onsubmit=e=>e.preventDefault();$('fields').oninput=updateDraft;$('fields').onchange=updateDraft;
  $('worksheet').querySelectorAll('[data-line]').forEach(b=>b.onclick=()=>highlight(Number(b.dataset.line),true));
  Object.keys(labels).forEach(k=>{$(`field-${k}`).onfocus=()=>{const line=d.evidence[k]?.[0]?.line;if(line)highlight(line,false);};});
  if($('compare-duplicate'))$('compare-duplicate').onclick=()=>selectDoc(d.duplicate_of);
  if(editable){$('actor').oninput=e=>{state.actor=e.target.value;sessionStorage.setItem('noticeledger.actor',state.actor);};$('save-corrections').onclick=saveCorrections;$('approve-open').onclick=()=>openReview('approve');$('reject-open').onclick=()=>openReview('reject');updateDraft();}
}
function draft(){return Object.fromEntries(new FormData($('fields')));}
function updateDraft(){if(!$('approve-open'))return;const changed=dirty();$('save-corrections').disabled=state.busy||(!changed&&!state.doc.ambiguity.length);$('approve-open').disabled=state.busy||changed||state.doc.issues.length>0||state.doc.status==='duplicate';$('draft-hint').textContent=changed?'Save corrections to rerun validation before approval.':'Approval records data in the ledger; it does not initiate a payment.';}
async function saveCorrections(){
  const reason=$('correction-reason').value.trim();if(!reason){message('Explain the correction before saving.',true);$('correction-reason').focus();return;}
  state.busy=true;updateDraft();
  try{await api(`/api/documents/${state.doc.id}/correct`,{version:state.doc.version,actor:state.actor,reason,fields:draft()});await load(state.doc.id);message('Corrections saved. Validation checks have been rerun.');}catch(e){message(e.message,true);if(e.status===409)await load(state.doc.id);}finally{state.busy=false;updateDraft();}
}
function highlight(line,scroll){document.querySelectorAll('.source-line').forEach(el=>el.classList.toggle('highlight',Number(el.dataset.sourceLine)===line));if(scroll)document.querySelector(`[data-source-line="${line}"]`)?.scrollIntoView({block:'nearest',behavior:'auto'});}
function renderEvidence(){
  const d=state.doc;if(!d){$('evidence').innerHTML='';return;}
  $('evidence').innerHTML=`<div class="evidence-header"><h2>Source notice</h2>${d.source_kind==='pdf'?`<a href="/api/documents/${esc(d.id)}/original" download>Original PDF</a>`:'<span class="badge muted">Read only</span>'}</div><p class="source-name">${esc(d.source_name)}</p><p class="hint">Select a field or source link to locate its evidence.</p><div class="source-sheet"><h3>${esc(readableType(d.original_fields.notice_type))} notice</h3><div class="source-lines">${d.source_text.split('\n').map((line,i)=>`<div class="source-line" data-source-line="${i+1}"><span class="line-number">${i+1}</span><span>${esc(line)||' '}</span></div>`).join('')}</div></div><h2>Review history</h2><ol class="audit-list">${d.audit.map(a=>`<li><strong>${esc(a.action.replaceAll('_',' '))} by ${esc(a.actor)}</strong><time datetime="${esc(a.created_at)}">${esc(when(a.created_at))}</time>${a.details.reason?`<p>${esc(a.details.reason)}</p>`:''}${Object.entries(a.details.changes||{}).map(([k,c])=>`<p>${esc(labels[k])}: ${esc(c.before||'not found')} → ${esc(c.after||'cleared')}</p>`).join('')}${a.action==='imported'?`<p>${a.details.checks} validation ${a.details.checks===1?'exception':'exceptions'} detected.</p>`:''}</li>`).join('')}</ol>`;
}
function renderLedger(){
  const s=state.snapshot;$('content').innerHTML=`<div class="view-panel"><div class="panel-header"><h2>Approved cash flows</h2><button id="export-ledger" class="primary">Export approved CSV</button></div><div class="totals">${s.totals.map(t=>`<div><strong>${esc(amount(t.net_minor,t.currency))}</strong><small>${t.notices} notices · ${esc(t.currency)} net cash flow</small></div>`).join('')}</div><p class="hint">Capital calls are outflows; distributions are inflows. Currency totals are not combined or converted. These are notice amounts, not confirmed bank transactions.</p><div class="table-scroll" tabindex="0" role="region" aria-label="Approved ledger table"><table><thead><tr><th>Fund / source</th><th>Investor</th><th>Notice</th><th>Effective date</th><th class="amount">Signed cash flow</th><th class="amount">Recallable amount</th><th>Reviewed</th></tr></thead><tbody>${s.ledger.map(r=>`<tr><td><button class="table-link" data-notice="${esc(r.document_id)}">${esc(r.fund)}</button><br><small class="subtle">${esc(r.source_name)}</small></td><td>${esc(r.investor)}</td><td>${esc(readableType(r.notice_type))}</td><td>${esc(r.effective_date)}</td><td class="amount">${esc(amount(r.signed_amount_minor,r.currency))}</td><td class="amount">${esc(amount(r.recallable_minor,r.currency))}</td><td>${esc(when(r.approved_at))}</td></tr>`).join('')}</tbody></table></div>${s.ledger.length?'':'<p class="empty">No approved cash flows yet.<br>Review a notice and approve it to create a ledger entry.</p>'}</div>`;
  $('export-ledger').onclick=()=>{const a=document.createElement('a');a.href='/api/export.csv';a.download='approved-cash-flows.csv';a.click();};$('content').querySelectorAll('[data-notice]').forEach(b=>b.onclick=()=>selectDoc(b.dataset.notice));
}
function renderJobs(){
  const s=state.snapshot;$('content').innerHTML=`<div class="view-panel"><div class="panel-header"><h2>Recent extraction jobs</h2><a href="/metrics">View monitoring metrics</a></div><p class="hint">Processing times measure local extraction validation and recording. A completed job still requires human review; it is not an approved cash flow.</p><div class="table-scroll" tabindex="0" role="region" aria-label="Processing history table"><table><thead><tr><th>Source</th><th>Result</th><th>Processing time</th><th>Imported</th><th>Issue</th></tr></thead><tbody>${s.jobs.map(j=>`<tr><td><button class="table-link" data-notice="${esc(j.document_id)}">${esc(j.source_name)}</button></td><td><span class="badge ${j.status==='failed'?'warning':'accepted'}">${esc(j.status)}</span></td><td>${Number(j.duration_ms).toFixed(2)} ms</td><td>${esc(when(j.created_at))}</td><td>${esc(j.error||'No extraction error')}</td></tr>`).join('')}</tbody></table></div>${s.jobs.length?'':'<p class="empty">No extraction jobs yet. Import a notice to begin.</p>'}</div>`;$('content').querySelectorAll('[data-notice]').forEach(b=>b.onclick=()=>selectDoc(b.dataset.notice));
}
function showDialog(id,opener){dialogOpener=opener||document.activeElement;$(id).showModal();$(id).querySelector('select,input,textarea,button')?.focus();}
function closeDialog(dialog){dialog.close();dialogOpener?.focus();}
document.querySelectorAll('.close-dialog').forEach(b=>b.onclick=()=>closeDialog(b.closest('dialog')));
document.querySelectorAll('dialog').forEach(d=>d.addEventListener('close',()=>dialogOpener?.focus()));
function openReview(action){
  if(!state.actor.trim()){message('Enter your reviewer name before continuing.',true);$('actor').focus();return;}
  const d=state.doc;pendingReview={id:d.id,action,version:d.version,actor:state.actor};const approve=action==='approve';
  $('review-title').textContent=approve?'Approve notice':'Reject notice';$('review-submit').textContent=approve?'Approve and record':'Reject notice';$('review-error').textContent='';$('verified').checked=false;$('reject-reason').value='';$('verification-label').hidden=!approve;$('rejection-input').hidden=approve;
  $('review-summary').innerHTML=`<div class="review-preview"><p>${esc(d.fields.fund)}</p><strong>${esc(d.fields.currency)} ${esc(d.fields.amount)}</strong><p>${esc(readableType(d.fields.notice_type))} · ${esc(d.fields.effective_date)}</p></div><p>${approve?'This will create one approved ledger entry. The source and your review are preserved.':'This notice will stay in history and will not enter the ledger.'}</p>`;showDialog('review-dialog');
}
$('review-form').onsubmit=async e=>{
  e.preventDefault();const {action,id,version,actor}=pendingReview;
  if(action==='approve'&&!$('verified').checked){$('review-error').textContent='Confirm that you checked the values against the source.';$('verified').focus();return;}
  if(action==='reject'&&!$('reject-reason').value.trim()){$('review-error').textContent='Explain why this notice is being rejected.';$('reject-reason').focus();return;}
  $('review-submit').disabled=true;
  try{await api(`/api/documents/${id}/${action}`,{version,actor,reason:$('reject-reason').value.trim()});closeDialog($('review-dialog'));await load(id);message(action==='approve'?'Notice approved and recorded once in the ledger.':'Notice rejected. Source and review history are preserved.');}catch(err){$('review-error').textContent=err.message;if(err.status===409){closeDialog($('review-dialog'));await load(id);message(err.message,true);}}finally{$('review-submit').disabled=false;}
};
let examples=[];
$('import-open').onclick=async()=>{if(!leaveDraft())return;$('import-error').textContent='';showDialog('import-dialog',$('import-open'));try{examples=await api('/api/examples');$('example').innerHTML='<option value="">Choose an example</option>'+examples.map((ex,i)=>`<option value="${i}">${esc(ex.name)}</option>`).join('');}catch(e){$('import-error').textContent=e.message;}};
$('example').onchange=e=>{if(e.target.value==='')return;const ex=examples[Number(e.target.value)];$('source-name').value=ex.name;$('source-text').value=ex.text;$('source-file').value='';};
$('import-kind').onchange=e=>{const kind=e.target.value;$('text-import').hidden=kind!=='text';$('source-file').value='';$('source-file').accept=kind==='pdf'?'.pdf,application/pdf':kind==='csv'?'.csv,text/csv':'.txt,text/plain';$('file-label').textContent=kind==='text'?'Or choose a text file':kind==='pdf'?'Select PDF (up to 2 MB, 20 pages)':'Select CSV batch (up to 50 notices)';$('format-help').textContent=kind==='csv'?'CSV must have source_name and source_text columns. Quote multiline source text. Every row is reviewed separately.':kind==='pdf'?'Selectable text is extracted and the original PDF is preserved. Scanned and encrypted PDFs are not supported.':'Supported fields include fund, investor, notice type, currency, total amount, notice date and due/payment date.';};
$('source-file').onchange=async()=>{const f=$('source-file').files[0];if(f&&$('import-kind').value==='text'){if(f.size>100000){$('import-error').textContent='Text file exceeds 100 KB.';return;}$('source-name').value=f.name;$('source-text').value=await f.text();}};
$('import-form').onsubmit=async e=>{
  e.preventDefault();const kind=$('import-kind').value;let name=$('source-name').value.trim(),content=$('source-text').value;const file=$('source-file').files[0];$('import-error').textContent='';
  if(kind!=='text'&&!file){$('import-error').textContent='Select a file to import.';$('source-file').focus();return;}
  if(kind==='text'&&(!name||!content.trim())){$('import-error').textContent='Provide a source name and notice text.';(!name?$('source-name'):$('source-text')).focus();return;}
  $('import-submit').disabled=true;$('import-submit').textContent='Importing…';
  try{if(file&&kind!=='text'){name=file.name;if(file.size>(kind==='pdf'?2000000:500000))throw new Error('Selected file exceeds the supported size.');if(kind==='pdf'){const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);content=btoa(binary);}else content=await file.text();}
    const result=await api('/api/import',{kind,name,content});closeDialog($('import-dialog'));state.view='review';state.filter='all';state.search='';await load(result.results[0].id);const duplicates=result.results.filter(r=>r.duplicate).length;message(`${result.results.length} ${result.results.length===1?'notice processed':'notices processed'}. ${duplicates?`${duplicates} duplicate matches found.`:'New notices await review.'}`);
  }catch(err){$('import-error').textContent=err.message;}finally{$('import-submit').disabled=false;$('import-submit').textContent='Import for review';}
};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{if(!leaveDraft())return;state.view=b.dataset.view;render();saveRoute();});
$('refresh').onclick=async()=>{if(!leaveDraft())return;$('refresh').disabled=true;try{await load(state.doc?.id);message('Workspace refreshed.');}catch(e){message(e.message,true);}finally{$('refresh').disabled=false;}};
const route=new URL(location.href);if(['review','ledger','jobs'].includes(route.searchParams.get('view')))state.view=route.searchParams.get('view');
load(route.searchParams.get('notice')).catch(e=>{$('content').innerHTML='<p class="empty">The workspace could not load. Check the local server, then choose Refresh workspace.</p>';message(e.message,true);});

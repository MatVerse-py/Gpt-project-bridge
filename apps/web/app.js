'use strict';
const API='/bridge-api';
const $=selector=>document.querySelector(selector);
const $$=selector=>[...document.querySelectorAll(selector)];
let latestStats={};

async function request(path,options={}){
  const response=await fetch(`${API}${path}`,{...options,headers:{accept:'application/json',...(options.headers||{})}});
  if(!response.ok){throw new Error(`${response.status} ${await response.text()}`)}
  return response.json();
}
function escapeHtml(value){return String(value??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function flash(message,error=false){const el=$('#flash');el.textContent=message;el.className=`flash${error?' error':''}`;el.hidden=false;clearTimeout(flash.timer);flash.timer=setTimeout(()=>el.hidden=true,7000);}
function route(){const name=(location.hash||'#dashboard').slice(1);$$('.page').forEach(p=>p.hidden=p.id!==name);$$('#nav a').forEach(a=>a.classList.toggle('active',a.dataset.route===name));if(name==='projects')loadProjects();if(name==='ingest')loadIngestions();if(name==='settings'||name==='bridge')loadHealth();}
async function loadStats(){try{const data=await request('/api/stats');latestStats=data.stats||{};for(const key of ['projects','conversations','messages','files']){$(`#stat-${key}`).textContent=String(latestStats[key]||0)}$('#empty-state').hidden=Boolean((latestStats.conversations||0)+(latestStats.files||0));$('#settings-stats').textContent=JSON.stringify(latestStats,null,2);}catch(error){flash(`Backend unavailable: ${error.message}`,true)}}
async function loadProjects(){try{const {projects}=await request('/api/projects');$('#projects-body').innerHTML=projects.map(p=>`<tr><td><strong>${escapeHtml(p.display_name)}</strong></td><td>${escapeHtml(p.project_id)}</td><td>${escapeHtml(p.attribution_basis)}</td><td>${Number(p.document_count||0)}</td></tr>`).join('')||'<tr><td colspan="4">No projects yet.</td></tr>';}catch(error){flash(error.message,true)}}
async function loadIngestions(){try{const {ingestions}=await request('/api/ingestions?limit=25');$('#ingestions-body').innerHTML=ingestions.map(r=>`<tr><td>${escapeHtml(r.source_name)}</td><td>${r.imported_documents}</td><td>${r.assigned_documents}</td><td>${r.unassigned_documents}</td><td>${escapeHtml(r.completed_at)}</td></tr>`).join('')||'<tr><td colspan="5">No ingestion runs yet.</td></tr>';}catch(error){flash(error.message,true)}}
async function loadHealth(){try{const h=await request('/health');$('#api-status').textContent=`${h.status} · version ${h.version}`;$('#bridge-health').textContent=`Status: ${h.status} · API ${h.version}`;$('#mcp-endpoint').textContent=`${location.protocol}//${location.hostname}:8787/mcp`;latestStats=h.stats||latestStats;$('#settings-stats').textContent=JSON.stringify(latestStats,null,2);}catch(error){flash(error.message,true)}}
async function runSearch(event){event.preventDefault();const q=$('#search-q').value.trim();if(!q)return;const project=$('#search-project').value.trim();const query=new URLSearchParams({q});if(project)query.set('project_id',project);try{const {results}=await request(`/api/search?${query}`);$('#search-summary').textContent=`${results.length} result(s) for “${q}”.`;$('#search-results').innerHTML=results.map(r=>`<button class="card result" data-id="${escapeHtml(r.id)}"><strong>${escapeHtml(r.title)}</strong><br><small>${escapeHtml(r.id)}</small></button>`).join('');$$('.result[data-id]').forEach(button=>button.addEventListener('click',()=>openDocument(button.dataset.id)));}catch(error){flash(error.message,true)}}
async function openDocument(id){try{const d=await request(`/api/documents/${encodeURIComponent(id)}`);$('#document-title').textContent=d.title;$('#document-body').textContent=d.text;$('#document-metadata').textContent=JSON.stringify(d.metadata,null,2);$('#document-dialog').showModal();}catch(error){flash(error.message,true)}}
async function uploadForm(event,path){event.preventDefault();const form=event.currentTarget;const button=form.querySelector('button');button.disabled=true;try{const data=await request(path,{method:'POST',body:new FormData(form),headers:{}});flash(`Ingestion completed: ${data.run.imported_documents} document(s).`);form.reset();await Promise.all([loadStats(),loadProjects(),loadIngestions()]);}catch(error){flash(error.message,true)}finally{button.disabled=false}}
window.addEventListener('hashchange',route);
$('#search-form').addEventListener('submit',runSearch);
$('#export-form').addEventListener('submit',event=>uploadForm(event,'/api/ingest/export'));
$('#files-form').addEventListener('submit',event=>uploadForm(event,'/api/ingest/project-files'));
$('#dialog-close').addEventListener('click',()=>$('#document-dialog').close());
route();loadStats();loadHealth();

import {api,post,session,download} from './api/client.js';import {batches,mappings,runs} from './api/business.js';import {store,refresh,loadVersions,selectVersion} from './stores/business.js';import {login} from './pages/login.js';import {batchesPage} from './pages/batches.js';import {mappingsPage} from './pages/mappings.js';import {runsPage} from './pages/runs.js';import {resultsPage} from './pages/results.js';import {routes} from './router/routes.js';import {ROLES} from './constants/enums.js';import {esc} from './utils/html.js';import {feedback} from './components/common/feedback.js';import {submit} from './hooks/forms.js';import {poll} from './hooks/poll.js';

let page='batches',resultId=null,busy=false,signature='';
const app=document.getElementById('app');

function contentSignature(){let base=page+':'+resultId+':'+(store.user?.role||'')+':'+store.runs.map(r=>r.id+'/'+r.status+'/'+r.progress?.processed).join(',');if(page==='results'&&resultId){let v=store.versions[resultId];base+='|'+(v?v.current_version+'/'+v.versions.map(x=>x.version+':'+x.accepted_count+':'+x.rejected_count+':'+(x.recovery?.length||0)).join(','):'');base+='|'+(store.selectedVersion[resultId]??'');}return base;}
function render(){if(!store.user){app.innerHTML=login();bind();return;}signature=contentSignature();
 let title=routes.find(r=>r[0]===page)?.[2]||'审计记录';
 app.innerHTML=`<aside class="sidebar"><a class="brand" href="#">见山<span>ARCHIVE TRANSFER / 09</span></a><p>检验结果<br>迁移核对站</p><nav>${routes.map(([key,num,name])=>`<button data-page="${key}" class="${key===page?'active':''}"><small>${num}</small>${name}</button>`).join('')}${store.user.role==='admin'?'<button data-page="audit"><small>05</small>审计记录</button>':''}</nav><div class="identity"><b>${ROLES[store.user.role]}</b><small>${esc(store.user.email)}</small><button class="quiet" id="logout">退出登录 ↗</button></div></aside><main class="workspace"><header><span class="eyebrow">QUALITY ARCHIVE · HANDOVER RECEIPT</span><span class="live">PostgreSQL / 原子提交目标行</span></header><div class="page-title"><span class="eyebrow">TRANSFER LEDGER / 2026.11</span><h1>${title}</h1><p>把旧字段交给新结构，带着每一行的去向与校验凭据。</p></div><div id="feedback" class="feedback" hidden></div><div id="content">${page==='batches'?batchesPage():page==='mappings'?mappingsPage():page==='runs'?runsPage():page==='results'?resultsPage(resultId,resultId?store.views[resultId]:null):'<pre id="audit-data">正在读取…</pre>'}</div><footer>见山质量档案组 / 受控字段迁移演练与逻辑JSON备份，不连接生产数据库。</footer></main>`;
 bind();
 if(page==='audit')api('/audit').then(x=>document.getElementById('audit-data').textContent=JSON.stringify(x,null,2)).catch(e=>feedback(e.message,true));
 if(store.user.role==='viewer')document.querySelectorAll('form button[type=submit], [data-edit-batch], [data-edit-mapping], [data-correct-row]').forEach(b=>{b.disabled=true;b.title='观察员只读';});
}

async function openResult(id,version){resultId=Number(id);page='results';render();window.scrollTo(0,0);if(version!=null)store.selectedVersion[resultId]=Number(version);try{await loadVersions(resultId);render();}catch(e){feedback(e.message,true);}}

function bind(){
 document.getElementById('login-form')?.addEventListener('submit',async e=>{e.preventDefault();try{await submit(e.target,async d=>{const r=await post('/auth/login',d);session.token=r.token;store.user=r.user;await refresh();render();});}catch(e){feedback(e.message,true);}});
 document.getElementById('logout')?.addEventListener('click',()=>{session.token=null;store.user=null;render();});
 document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{let key=b.dataset.page;if(key==='results'&&!resultId){let first=store.runs.find(r=>r.status==='completed')||store.runs[0];if(first)return openResult(first.id);}page=key;render();window.scrollTo(0,0);});
 document.querySelectorAll('[data-reset]').forEach(b=>b.onclick=()=>{document.getElementById(b.dataset.reset).reset();document.querySelector('#'+b.dataset.reset+' [name=id]').value='';});
 document.querySelectorAll('[data-edit-batch]').forEach(b=>b.onclick=()=>{let d=store.batches.find(x=>x.id===Number(b.dataset.editBatch));fill('batch-form',{...d,rows:JSON.stringify(d.rows,null,2)});});
 document.querySelectorAll('[data-edit-mapping]').forEach(b=>b.onclick=()=>{let m=store.mappings.find(x=>x.id===Number(b.dataset.editMapping));fill('mapping-form',{...m,rules:JSON.stringify(m.rules,null,2)});});
 document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>openResult(b.dataset.result));
 document.querySelectorAll('[data-version-tab]').forEach(b=>b.onclick=async()=>{try{await selectVersion(resultId,Number(b.dataset.versionTab));render();}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-export]').forEach(b=>b.onclick=()=>download(b.dataset.export).catch(e=>feedback(e.message,true)));
 document.querySelectorAll('[data-export-version]').forEach(b=>b.onclick=()=>download(b.dataset.exportVersion,'export',b.dataset.version).catch(e=>feedback(e.message,true)));
 document.querySelectorAll('[data-backup]').forEach(b=>b.onclick=()=>download(b.dataset.backup,'backup',b.dataset.version).catch(e=>feedback(e.message,true)));
 document.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=async()=>{try{await runs.cancel(b.dataset.cancel);await refresh();render();feedback('已发出取消请求；尚未提交的目标行将全部丢弃。');}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-targets]').forEach(b=>b.onclick=async()=>{try{let data=await runs.targets(b.dataset.targets,b.dataset.version);let el=document.getElementById('target-data');el.hidden=false;el.textContent=JSON.stringify(data,null,2);}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-restore]').forEach(b=>b.onclick=async()=>{try{let file=document.getElementById('backup-file').files[0];if(!file)throw Error('请先选择本运行下载的JSON备份');let result=await runs.restore(b.dataset.restore,JSON.parse(await file.text()));await loadVersions(resultId);render();feedback('已恢复第'+result.version+'版'+result.restored_count+'行，校验'+(result.verified?'一致':'失败'));}catch(e){feedback(e.message,true);}});
 document.querySelectorAll('[data-correct-row]').forEach(b=>b.onclick=()=>{let form=document.getElementById('correct-form'),view=store.views[resultId];if(!form||!view)return;let item=view.rejected_rows.find(x=>x.row_number===Number(b.dataset.correctRow));form.elements.row_number.value=b.dataset.correctRow;form.elements.source.value=JSON.stringify(view.correction?.corrections?.[b.dataset.correctRow]||item?.source||{},null,2);form.scrollIntoView({behavior:'smooth',block:'center'});form.elements.source.focus();});
 const handlers={
  'batch-form':async d=>{const id=d.id;delete d.id;d.rows=JSON.parse(d.rows);await batches.save(d,id);},
  'mapping-form':async d=>{const id=d.id;delete d.id;d.batch_id=Number(d.batch_id);d.chunk_size=Number(d.chunk_size);d.rules=JSON.parse(d.rules);await mappings.save(d,id);},
  'run-form':async d=>{d.mapping_id=Number(d.mapping_id);let r=await runs.run(d);resultId=r.id;store.selectedVersion[resultId]=null;},
  'correct-form':async d=>{let body={base_version:Number(d.base_version),corrections:{[Number(d.row_number)]:JSON.parse(d.source)},reason:d.reason||''};if(d.request_id?.trim())body.request_id=d.request_id.trim();let r=await runs.correct(resultId,body);store.selectedVersion[resultId]=null;await loadVersions(resultId);feedback(r.replayed?'该补正单号已发布过第'+r.version.version+'版，未重复发布。':'已发布第'+r.version.version+'版：入库'+r.version.accepted_count+'行，隔离'+r.version.rejected_count+'行，可由版本标签逐版复核。');}
 };
 for(const [id,fn] of Object.entries(handlers))document.getElementById(id)?.addEventListener('submit',async e=>{e.preventDefault();busy=true;try{await submit(e.target,fn);if(id!=='correct-form'){await refresh();render();feedback('已保存。到迁移执行和差异核对页复查进展。');}else render();}catch(e){feedback(e.message,true);}finally{busy=false;}});
}

function fill(id,data){const form=document.getElementById(id);for(let [k,v] of Object.entries(data)){let f=form.elements[k];if(f)f.value=v??'';}form.scrollIntoView({behavior:'smooth',block:'center'});}

try{if(session.token){store.user=await api('/auth/me');await refresh();}}catch(e){session.token=null;store.user=null;}
render();
poll(async()=>{if(!store.user||busy)return;try{await refresh();if(page==='results'&&resultId)await loadVersions(resultId);if(['results','runs'].includes(page)&&!document.querySelector('form [type=submit]:disabled')&&!document.getElementById('backup-file')?.files.length&&contentSignature()!==signature&&!document.activeElement?.closest?.('#correct-form'))render();}catch(e){feedback(e.message,true);}});

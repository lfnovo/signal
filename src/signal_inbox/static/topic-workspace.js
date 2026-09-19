(() => {
  const root = document.querySelector('#topics-workspace');
  if (!root) return;
  const directory = root.querySelector('#topics-directory-list'), workbench = root.querySelector('#topic-workbench');
  const query = root.querySelector('#topics-query'), status = root.querySelector('#topics-status'), sort = root.querySelector('#topics-sort');
  const scope = root.querySelector('#topics-include-inbox');
  scope.checked = new URL(location.href).searchParams.get('include_inbox') === 'true';
  let data, selected = root.dataset.initialTopic, relatedTo = null, graphMode = false, loadVersion = 0, previewVersion = 0, previewRequest, busy = false;
  const drafts = new Map();
  let workspaceView = new URL(location.href).searchParams.get('view') || (selected ? 'topics' : 'overview');
  const overviewRoot = root.querySelector('#topic-overview');
  const overview = initTopicOverview(overviewRoot, (id, edge) => {
    if(busy)return;
    select(id); relatedTo = edge || null; setWorkspaceView('topics'); render();
    workbench.querySelector(edge ? '#topic-finds-heading' : '.workspace-topic-heading')?.scrollIntoView({block:'start',behavior:'smooth'});
  });
  function updateLocation() {
    const params = new URLSearchParams();
    if(scope.checked) params.set('include_inbox','true');
    if(workspaceView === 'overview') params.set('view','overview');
    history.replaceState(null,'','/topics'+(workspaceView === 'topics' && selected?'/'+encodeURIComponent(selected):'')+(params.size?'?'+params:''));
  }
  function setWorkspaceView(view) {
    workspaceView = view === 'overview' ? 'overview' : 'topics';
    overviewRoot.hidden = workspaceView !== 'overview';
    root.querySelector('.topics-layout').hidden = workspaceView !== 'topics';
    root.querySelectorAll('[data-workspace-view]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.workspaceView === workspaceView)));
    updateLocation();
  }
  root.querySelectorAll('[data-workspace-view]').forEach(el=>el.addEventListener('click',()=>{if(!busy)setWorkspaceView(el.dataset.workspaceView);}));
  setWorkspaceView(workspaceView);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const folded = value => String(value).normalize('NFD').replace(/\p{M}/gu, '').toLocaleLowerCase();
  const topic = id => data.topics.find(t => t.id === id);
  const neighbors = () => data.edges.filter(e => e.source === selected || e.target === selected);
  const other = edge => edge.source === selected ? edge.target : edge.source;
  const visibleTopics = () => data.topics.filter(t => (status.value === 'all' || (status.value === 'official') === !!t.official) && folded(t.name + ' ' + t.definition + ' ' + t.personal_context).includes(folded(query.value))).sort((a,b) => sort.value === 'count' ? b.count-a.count || a.name.localeCompare(b.name) : sort.value === 'count-asc' ? a.count-b.count || a.name.localeCompare(b.name) : sort.value === 'activity' ? b.last_activity.localeCompare(a.last_activity) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
  function renderDirectory() {
    const items = visibleTopics();
    directory.innerHTML = items.map(t => `<button type="button" class="workspace-topic ${t.official ? 'official' : 'suggested'} ${t.id === selected ? 'selected' : ''}" data-topic="${escape(t.id)}" aria-pressed="${t.id === selected}"><span>${t.official ? '✓' : '✧'} ${escape(t.name)}</span><span class="topic-count">${t.count}</span></button>`).join('') || '<p class="muted">No matching threads. Try another name or filter.</p>';
  }
  function render() {
    renderDirectory();
    const t = topic(selected);
    if (!t) { workbench.innerHTML = '<div class="empty"><h2>Start a thread.</h2><p>Create a topic to give your research a home.</p></div>'; return; }
    const draft = {...t,...drafts.get(selected)};
    workbench.innerHTML = `
      <div class="workspace-topic-heading"><div><span class="topic-chip ${t.official ? 'official' : 'suggested'}">${t.official ? '✓ Official' : '✧ Suggested'}</span>${inlineField({key:'name',value:draft.name,original:t.name,label:'topic name',heading:2,max:80})}</div><div class="workspace-topic-actions">${t.official ? '' : '<button type="button" class="button primary" data-action="approve">Make official ✓</button>'}<button type="button" class="text-button" data-action="manage-merge" aria-controls="topic-management" aria-expanded="false">Merge</button><button type="button" class="text-button danger-text" data-action="delete-topic">Delete</button></div></div>
      <div class="workspace-management" id="topic-management" hidden>
        <form data-form="merge"><label>Merge into<select name="target" required><option value="">Choose the topic to keep…</option>${data.topics.filter(x=>x.id!==selected).map(x=>`<option value="${escape(x.id)}">${escape(x.name)}</option>`).join('')}</select></label><p class="muted">Combines finds and preserves both contexts in the destination.</p><button class="button">Merge</button></form>
      </div>
      <div class="topic-context-fields">
        ${inlineField({key:'definition',value:draft.definition||'',original:t.definition||'',label:'What belongs here',multiline:true,max:12000,placeholder:'Define this thread. What belongs here?'})}
        ${inlineField({key:'personal_context',value:draft.personal_context||'',original:t.personal_context||'',label:'Why this matters to me',multiline:true,max:24000,placeholder:'What are you exploring? Add your questions and interests.'})}
      </div><p class="muted topic-context-note">Official topic context guides future tagging, personal relevance and chat.</p>
      <section class="topic-connections"><div class="library-heading"><h3>Connected threads</h3><div class="connection-view" role="group" aria-label="Connection view"><button class="text-button" type="button" data-view="list" aria-pressed="${!graphMode}">List</button><button class="text-button" type="button" data-view="graph" aria-pressed="${graphMode}">Graph</button></div></div><p class="muted">Based on shared finds in ${scope.checked ? 'Inbox + Library' : 'Library'}. Focus is included when the find is in Library. Strength balances overlap and topic size.</p><div id="topic-connections-body"></div></section>
      <section class="topic-finds"><div class="library-heading"><h3 id="topic-finds-heading">Finds</h3><button type="button" class="text-button" data-action="clear-connection" hidden>Show all finds</button></div><div class="topic-finds-layout"><div id="topic-find-list"></div><aside id="topic-find-preview" class="panel" aria-label="Find preview"><div class="empty"><p>Select a find to read and review its topics.</p></div></aside></div></section>`;
    renderConnections(); renderFinds();
  }
  function renderConnections() {
    const container = workbench.querySelector('#topic-connections-body'), edges = neighbors();
    if (!edges.length) { container.innerHTML = '<p class="muted">No shared finds in this scope yet. Add finds to this topic or include Inbox.</p>'; return; }
    if (!graphMode) {
      container.innerHTML = edges.map(e=>`<div class="related-topic-row"><button class="text-button" type="button" data-topic="${escape(other(e))}">${escape(topic(other(e)).name)} ↗</button><button class="connection-evidence" type="button" data-edge="${escape(other(e))}" aria-pressed="${relatedTo === other(e)}">${e.shared_count} shared · ${Math.round(e.strength*100)}% strength</button></div>`).join('');
      return;
    }
    // A focused graph stays legible: nodes navigate, edges reveal the actual shared finds.
    const shown = edges.slice(0,10), positions = shown.map((e,i)=>({e,x:320+220*Math.cos(2*Math.PI*i/shown.length-Math.PI/2),y:240+165*Math.sin(2*Math.PI*i/shown.length-Math.PI/2)}));
    const svg = `<svg class="topic-graph" viewBox="0 0 640 480" role="group" aria-label="Topics connected to ${escape(topic(selected).name)} through shared finds"><title>Shared-find connections. Select a line for evidence or a topic to explore.</title>${positions.map(({e,x,y})=>`<g data-edge="${escape(other(e))}" tabindex="0" role="button" aria-label="${e.shared_count} finds shared with ${escape(topic(other(e)).name)}"><line class="graph-hit" x1="320" y1="240" x2="${x}" y2="${y}"/><line class="graph-edge ${relatedTo===other(e)?'selected':''}" x1="320" y1="240" x2="${x}" y2="${y}" stroke-width="${1+e.strength*6}"/><text x="${320+(x-320)*.54}" y="${240+(y-240)*.54-8}" text-anchor="middle">${e.shared_count}</text></g>`).join('')}<circle class="graph-center" cx="320" cy="240" r="36"/><text x="320" y="292" text-anchor="middle">${escape(topic(selected).name.slice(0,30))}</text>${positions.map(({e,x,y})=>`<g data-topic="${escape(other(e))}" tabindex="0" role="button" aria-label="Explore ${escape(topic(other(e)).name)}"><title>${escape(topic(other(e)).name)}</title><circle class="graph-node ${topic(other(e)).official?'official':'suggested'}" cx="${x}" cy="${y}" r="${15+Math.min(12,Math.sqrt(topic(other(e)).count)*3)}"/><text x="${x}" y="${y+42}" text-anchor="middle">${escape(topic(other(e)).name.length>25?topic(other(e)).name.slice(0,23)+'…':topic(other(e)).name)}</text></g>`).join('')}</svg>`;
    container.innerHTML = svg + `<p class="muted">${edges.length>10?'Showing the 10 strongest connections. List shows all. ':''}Select a line to see its finds. Select a node to explore that topic. These are shared-source relationships, not AI-inferred claims.</p>`;
  }
  function renderFinds(preservePreview = false) {
    const t = topic(selected), edge = neighbors().find(e=>other(e)===relatedTo);
    const ids = new Set(edge ? edge.source_ids : t.source_ids);
    const finds = data.sources.filter(s=>ids.has(s.id));
    workbench.querySelector('#topic-finds-heading').textContent = edge ? `${t.name} × ${topic(relatedTo).name} · ${finds.length}` : `Finds · ${finds.length}`;
    workbench.querySelector('[data-action="clear-connection"]').hidden = !edge;
    workbench.querySelector('#topic-find-list').innerHTML = finds.map(s=>`<button type="button" class="topic-find" data-open-find="${s.id}"><span class="muted">${escape(s.collection)}${s.focused?' · ★ Focus':''} · ${escape(s.created_at.slice(0,10))}</span><strong>${escape(s.title)}</strong></button>`).join('') || '<p class="muted">No finds in this scope. Include Inbox to see unreviewed captures.</p>';
    if (preservePreview) return;
    previewVersion++; previewRequest?.abort();
    workbench.querySelector('#topic-find-preview').innerHTML = '<div class="empty"><p>Select a find to read and review its topics.</p></div>';
  }
  async function openFind(id) {
    const ticket=++previewVersion;previewRequest?.abort();previewRequest=new AbortController();
    const panel=workbench.querySelector('#topic-find-preview');
    panel.innerHTML='<div class="empty" role="status">Following this thread…</div>';
    panel.dataset.sourceId=id;
    try {
      const r=await fetch('/sources/'+id+'/triage',{signal:previewRequest.signal});
      if(!r.ok)throw new Error('Could not load this find.');
      const html=await r.text();if(ticket!==previewVersion)return;
      panel.innerHTML=html;initTopicEditor(panel.querySelector('#topic-editor'));
      panel.querySelectorAll('.triage-full').forEach(details=>details.addEventListener('toggle',()=>{
        const video=details.querySelector('[data-youtube-id]');if(!video)return;
        if(!details.open){video.querySelector('iframe')?.remove();return;}
        if(video.querySelector('iframe'))return;
        const frame=document.createElement('iframe');frame.src='https://www.youtube-nocookie.com/embed/'+video.dataset.youtubeId;frame.title='YouTube player';frame.allow='encrypted-media; picture-in-picture; fullscreen';frame.allowFullscreen=true;frame.referrerPolicy='strict-origin-when-cross-origin';video.prepend(frame);
      }));
    } catch(e){if(ticket===previewVersion && e.name!=='AbortError')panel.textContent=e.message;}
  }
  async function load(id = selected) {
    const version=++loadVersion;
    try {
      const value=await api('/api/topics/workspace?include_inbox='+scope.checked);
      if(version!==loadVersion)return;
      data=value; selected=data.topics.some(t=>t.id===id)?id:data.topics[0]?.id;
      updateLocation(); overview.update(data);
      if(relatedTo && !neighbors().some(e=>other(e)===relatedTo))relatedTo=null;
      render();
    } catch(e){toast(e.message,true);const retry='<button type="button" class="text-button" data-action="retry">Retry loading topics ↗</button>';directory.innerHTML=retry;overviewRoot.innerHTML=retry;}
  }
  function select(id) {
    if(id===selected)return;
    selected=id;relatedTo=null;previewVersion++;previewRequest?.abort();
    updateLocation();render();
  }
  async function mutate(action) {
    if(busy)return;busy=true;root.setAttribute('aria-busy','true');
    const controls=[...root.querySelectorAll('button,input,textarea,select')].map(el=>[el,el.disabled]);
    controls.forEach(([el])=>{el.disabled=true;});
    try{await action();}catch(e){toast(e.message,true);}finally{controls.forEach(([el,disabled])=>{if(el.isConnected)el.disabled=disabled;});busy=false;root.setAttribute('aria-busy','false');}
  }
  root.addEventListener('signal:inline-change',event=>{
    if(event.target.dataset.sourceTitle || !workbench.contains(event.target))return;
    const {key,value}=event.detail, current=topic(selected), draft={...drafts.get(selected)};
    if(value===(current[key]||''))delete draft[key];else draft[key]=value;
    if(Object.keys(draft).length)drafts.set(selected,draft);else drafts.delete(selected);
  });
  root.addEventListener('signal:inline-save',async event=>{
    if(event.target.dataset.sourceTitle || !workbench.contains(event.target))return;
    const {key,value,commit,reject}=event.detail, id=selected;
    if(busy){reject(new Error('Another change is saving. Try again in a moment.'));return;}
    busy=true;
    try {
      const saved=await api('/api/topics/'+id+(key==='name'?'':'/context'),json(key==='name'?'PUT':'PATCH',{[key]:value}));
      topic(id)[key]=saved[key]||'';
      commit(topic(id)[key]);renderDirectory();overview.update(data);
      if(key==='name'){renderConnections();renderFinds(true);}
    }catch(error){reject(error);}finally{busy=false;}
  });
  document.addEventListener('signal:source-title-changed',event=>{
    const source=data?.sources.find(s=>s.id===event.detail.id);if(source)source.title=event.detail.title;
    if(topic(selected))renderFinds(true);
  });
  root.addEventListener('click',event=>{
    const el=event.target.closest('[data-topic],[data-edge],[data-view],[data-action],[data-open-find],[data-triage-action]');if(!el || busy)return;
    if(el.dataset.topic){select(el.dataset.topic);return;}
    if(el.dataset.edge){relatedTo=el.dataset.edge;renderConnections();renderFinds();return;}
    if(el.dataset.view){graphMode=el.dataset.view==='graph';renderConnections();workbench.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-pressed',String((b.dataset.view==='graph')===graphMode)));return;}
    if(el.dataset.openFind){openFind(el.dataset.openFind);return;}
    if(el.dataset.triageAction){
      const id=el.closest('#topic-find-preview').dataset.sourceId, action=el.dataset.triageAction;
      if(action==='delete' && !confirm('Permanently delete this find, its content and chat? This cannot be undone.'))return;
      mutate(async()=>{await api('/api/sources/'+id+(action==='delete'?'':'/'+(action==='focus'?'focus':'collection')),json(action==='delete'?'DELETE':'PUT',action==='delete'?{confirm:true}:action==='focus'?{focused:el.getAttribute('aria-pressed')!=='true'}:{collection:'library'}));await load();});return;
    }
    if(el.dataset.action==='retry'){load();return;}
    if(el.dataset.action==='manage-merge'){
      const panel=workbench.querySelector('.workspace-management'), kind=el.dataset.action.slice(7);
      panel.hidden=!panel.hidden && panel.dataset.openForm===kind;panel.dataset.openForm=kind;
      panel.querySelectorAll('form').forEach(form=>{form.hidden=form.dataset.form!==kind;});
      workbench.querySelectorAll('[data-action^="manage-"]').forEach(button=>button.setAttribute('aria-expanded',String(!panel.hidden && button.dataset.action===el.dataset.action)));
      if(!panel.hidden)panel.querySelector(`[data-form="${kind}"] input, [data-form="${kind}"] select`)?.focus();
      return;
    }
    if(el.dataset.action==='clear-connection'){relatedTo=null;renderConnections();renderFinds();return;}
    if(el.dataset.action==='approve')mutate(async()=>{await api('/api/topics/'+selected+'/approve',{method:'POST'});await load();});
    if(el.dataset.action==='delete-topic' && confirm('Delete this topic and its associations? Your finds stay.'))mutate(async()=>{await api('/api/topics/'+selected,{method:'DELETE'});drafts.delete(selected);selected=null;await load();});
  });
  root.addEventListener('submit',event=>{
    const form=event.target;event.preventDefault();
    if(form.id==='workspace-new-topic'){const name=form.querySelector('input').value;mutate(async()=>{const t=await api('/api/topics',json('POST',{name}));form.reset();await load(t.id);});return;}
    const kind=form.dataset.form,id=selected;
    if(kind==='merge' && confirm('Merge these topics? Contexts and finds will be combined.'))mutate(async()=>{const target=form.elements.target.value;await api('/api/topics/'+id+'/merge',json('POST',{target}));drafts.delete(id);await load(target);});
  });
  root.addEventListener('keydown',event=>{
    if(event.target.closest('input,textarea,select') || event.metaKey || event.ctrlKey || event.altKey)return;
    const entry=event.target.closest('.workspace-topic'), item=event.target.closest('[data-topic],[data-edge]');
    if(entry && ['ArrowUp','ArrowDown'].includes(event.key)){
      event.preventDefault();const entries=[...directory.querySelectorAll('.workspace-topic')],next=entries[Math.max(0,Math.min(entries.length-1,entries.indexOf(entry)+(event.key==='ArrowDown'?1:-1)))];select(next.dataset.topic);directory.querySelector(`[data-topic="${next.dataset.topic}"]`)?.focus();
    }else if(item && item.tagName.toLowerCase()==='g' && ['Enter',' '].includes(event.key)){event.preventDefault();item.dispatchEvent(new MouseEvent('click',{bubbles:true}));}
  });
  query.addEventListener('input',()=>data&&renderDirectory());status.addEventListener('change',()=>data&&renderDirectory());sort.addEventListener('change',()=>data&&renderDirectory());
  scope.addEventListener('change',()=>{updateLocation();load();});
  document.addEventListener('signal:topics-changed',async()=>{
    const version=++loadVersion;
    try{const value=await api('/api/topics/workspace?include_inbox='+scope.checked);if(version!==loadVersion)return;data=value;overview.update(data);renderDirectory();if(topic(selected)){renderConnections();renderFinds(true);const badge=workbench.querySelector('.workspace-topic-heading .topic-chip');badge.className='topic-chip '+(topic(selected).official?'official':'suggested');badge.textContent=topic(selected).official?'✓ Official':'✧ Suggested';if(topic(selected).official)workbench.querySelector('[data-action=approve]')?.remove();}}catch{/* Keep current context during temporary disconnections. */}
  });
  window.addEventListener('beforeunload',event=>{if(drafts.size){event.preventDefault();event.returnValue='';}});
  load();
})();

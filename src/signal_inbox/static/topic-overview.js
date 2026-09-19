(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fold = value => String(value).normalize('NFD').replace(/\p{M}/gu,'').toLowerCase();
  window.initTopicOverview = (root, openTopic) => {
    let data, model, active = null, search = '', zoom = 1;
    const topic = id => data.topics.find(t => t.id === id);
    const chip = id => {const t=topic(id); return `<button class="map-topic-chip ${t.official?'official':'suggested'}" data-map-topic="${escape(id)}">${t.official?'✓':'✧'} ${escape(t.name)} <span>${t.count}</span></button>`;};
    function inspect() {
      const panel = root.querySelector('.map-inspector');
      const group = model.clusters.find(c => c.id === active);
      const members = group ? group.members : data.topics.filter(t => !search || fold(t.name).includes(fold(search))).map(t=>t.id);
      const ids = new Set(members), sources = new Set(members.flatMap(id => topic(id).source_ids));
      const edges = data.edges.filter(e => ids.has(e.source) && ids.has(e.target)).slice(0,8);
      panel.innerHTML = `<div class="eyebrow">${group?'FOLLOW THIS CLUSTER':'YOUR CONSTELLATION'}</div><h2>${group?escape(topic(group.members[0]).name):search?'Matching threads':'A wider lens.'}</h2><p class="muted">${members.length} topics · ${sources.size} finds${group?' · shared finds counted once':''}</p>${group?'<button class="text-button" data-map-reset>← All clusters</button>':'<p class="muted">Select a cluster to explore it. Select a topic to open its context and finds.</p>'}<div class="map-topic-list">${members.map(chip).join('') || '<p class="muted">No matching topics.</p>'}</div>${group && edges.length?`<h3>Strongest connections</h3><div class="map-evidence">${edges.map(e=>`<button data-map-source="${escape(e.source)}" data-map-target="${escape(e.target)}"><span>${escape(topic(e.source).name)} × ${escape(topic(e.target).name)}</span><small>${e.shared_count} shared · ${Math.round(e.strength*100)}% strength ↗</small></button>`).join('')}</div>`:''}`;
      root.querySelectorAll('[data-map-cluster]').forEach(el=>el.setAttribute('aria-pressed',String(el.dataset.mapCluster === active)));
      root.querySelectorAll('.map-cluster-region').forEach(el=>el.classList.toggle('active',el.dataset.cluster === active));
      root.querySelectorAll('.map-node').forEach(el=>{
        el.classList.toggle('dimmed', !!((group && !ids.has(el.dataset.mapTopic)) || (search && !fold(topic(el.dataset.mapTopic).name).includes(fold(search)))));
      });
    }
    function render() {
      if (!data) return;
      const {clusters,isolated,degree} = model;
      const columns = Math.min(3, Math.max(1,clusters.length));
      const outerRadius = c => 145+80*Math.max(0,Math.ceil((c.members.length-1)/12)-1);
      const cellWidth = Math.max(420,...clusters.map(c => outerRadius(c)*2+130));
      const rows = Math.ceil(clusters.length/columns), rowHeights=[];
      for(let row=0;row<rows;row++) rowHeights.push(Math.max(350,...clusters.slice(row*columns,(row+1)*columns).map(c=>outerRadius(c)*2+160)));
      const width=columns*cellWidth, height=Math.max(360,rowHeights.reduce((a,b)=>a+b,0)), points=new Map();
      const regions=clusters.map((c,index)=>{
        const col=index%columns,row=Math.floor(index/columns), top=rowHeights.slice(0,row).reduce((a,b)=>a+b,0);
        const cx=col*cellWidth+cellWidth/2,cy=top+rowHeights[row]/2+15;
        // Concentric rings keep large communities inside their own region.
        c.members.forEach((id,i)=>{
          const centered=c.members.length>6;
          if(centered && i===0){points.set(id,{x:cx,y:cy});return;}
          const offset=i-(centered?1:0), ring=Math.floor(offset/12), start=ring*12;
          const count=Math.min(12,c.members.length-(centered?1:0)-start);
          const angle=(offset-start)*Math.PI*2/count-Math.PI/2+ring*.3;
          const radius=c.members.length===1?0:outerRadius(c)-ring*80;
          points.set(id,{x:cx+Math.cos(angle)*radius,y:cy+Math.sin(angle)*radius});
        });
        return `<g class="map-cluster-region" data-cluster="${escape(c.id)}"><rect x="${col*cellWidth+12}" y="${top+12}" width="${cellWidth-24}" height="${rowHeights[row]-24}" rx="32"/><g tabindex="0" role="button" data-map-cluster="${escape(c.id)}" aria-label="Explore cluster ${escape(topic(c.members[0]).name)}"><rect class="map-cluster-label-hit" x="${col*cellWidth+24}" y="${top+22}" width="${cellWidth-48}" height="42"/><text x="${col*cellWidth+32}" y="${top+48}">${String(index+1).padStart(2,'0')} / ${escape(topic(c.members[0]).name.slice(0,30))}</text></g></g>`;
      }).join('');
      const lines=data.edges.map(e=>{
        const a=points.get(e.source),b=points.get(e.target);if(!a||!b)return '';
        const same=clusters.some(c=>c.members.includes(e.source)&&c.members.includes(e.target));
        return `<line class="map-edge ${same?'':'bridge'}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke-width="${1+e.strength*3}"/>`;
      }).join('');
      const nodes=[...points].map(([id,p])=>{
        const t=topic(id),r=7+Math.min(13,Math.sqrt(t.count)*3);
        const words=t.name.split(/\s+/), labels=[''];
        for(const word of words){if((labels.at(-1)+' '+word).trim().length>17 && labels.at(-1))labels.push(word);else labels[labels.length-1]=(labels.at(-1)+' '+word).trim();}
        const label=labels.slice(0,2).map((line,i)=>`<tspan x="${p.x}" dy="${i?16:0}">${escape(line.length>19?line.slice(0,17)+'…':line+(i===1 && labels.length>2?'…':''))}</tspan>`).join('');
        return `<g class="map-node ${t.official?'official':'suggested'}" data-map-topic="${escape(id)}" tabindex="0" role="button" aria-label="${escape(t.name)}, ${t.count} finds, ${t.official?'official':'suggested'}"><title>${escape(t.name)} · ${t.count} finds · ${degree.get(id).toFixed(1)} weighted connections</title><circle class="map-node-hit" cx="${p.x}" cy="${p.y}" r="28"/><circle class="map-dot" cx="${p.x}" cy="${p.y}" r="${r}"/><text x="${p.x}" y="${p.y+r+17}" text-anchor="middle">${label}</text></g>`;
      }).join('');
      root.innerHTML=`<div class="map-toolbar"><div><h2>See where your curiosity gathers.</h2><p class="muted">${clusters.length} clusters · ${points.size} connected topics · ${isolated.length} without connections</p></div><label class="sr-only" for="map-query">Find a topic on the map</label><input id="map-query" type="search" placeholder="Find your thread…" value="${escape(search)}"></div><div class="map-layout"><div class="map-main"><div class="map-controls"><span class="muted">✓ Official <span class="map-suggested">✧ Suggested</span> · Bigger nodes, more finds</span><div role="group" aria-label="Map zoom"><button class="text-button" data-map-zoom="out" aria-label="Zoom out">−</button><button class="text-button" data-map-zoom="reset">Fit</button><button class="text-button" data-map-zoom="in" aria-label="Zoom in">+</button></div></div><div class="map-viewport" tabindex="0" role="region" aria-label="Topic cluster map. Scroll to pan, use zoom buttons to enlarge.">${points.size?`<svg class="topics-overview-graph" viewBox="0 0 ${width} ${height}" role="group" aria-label="Topic communities based on shared finds">${regions}${lines}${nodes}</svg>`:'<div class="empty"><h3>A few threads, waiting to connect.</h3><p>Clusters appear when finds share topics. Try including Inbox to widen the view.</p></div>'}</div><div class="map-cluster-list" aria-label="Explore clusters">${clusters.map((c,i)=>`<button class="button" data-map-cluster="${escape(c.id)}">${String(i+1).padStart(2,'0')} / ${escape(topic(c.members[0]).name)} <span>${c.members.length}</span></button>`).join('')}</div><p class="muted map-explanation">Groups follow stronger shared-find connections, balanced by topic size. Cluster names come from a connected topic, not an AI label. Thin lines between groups are bridges. The map changes with your collection.</p>${isolated.length?`<details class="map-isolated"><summary>${isolated.length} topics without connections in this scope</summary><p class="muted">Still part of your vocabulary. They need shared finds to join the map.</p><div class="map-isolated-topics">${isolated.map(chip).join('')}</div></details>`:''}</div><aside class="map-inspector" aria-live="polite"></aside></div>`;
      applyZoom();inspect();
    }
    function applyZoom() { const svg=root.querySelector('svg');if(svg){svg.style.width=`${zoom*100}%`;svg.style.maxWidth='none';} }
    root.addEventListener('input',event=>{if(event.target.id==='map-query'){search=event.target.value;active=null;inspect();}});
    root.addEventListener('click',event=>{
      const el=event.target.closest('[data-map-topic],[data-map-cluster],[data-map-reset],[data-map-zoom],[data-map-source]');if(!el)return;
      if(el.dataset.mapTopic)openTopic(el.dataset.mapTopic);
      else if(el.dataset.mapSource)openTopic(el.dataset.mapSource,el.dataset.mapTarget);
      else if(el.dataset.mapCluster){active=el.dataset.mapCluster;search='';root.querySelector('#map-query').value='';inspect();}
      else if(el.hasAttribute('data-map-reset')){active=null;inspect();}
      else if(el.dataset.mapZoom){zoom=el.dataset.mapZoom==='reset'?1:Math.min(4,Math.max(1,zoom+(el.dataset.mapZoom==='in'?.5:-.5)));applyZoom();}
    });
    root.addEventListener('keydown',event=>{
      if(event.target.tagName.toLowerCase()==='g' && ['Enter',' '].includes(event.key)){event.preventDefault();event.stopPropagation();event.target.dispatchEvent(new MouseEvent('click',{bubbles:true}));}
    });
    return {update(value){data=value;model=SignalTopicClusters.clusterTopics(data.topics,data.edges);if(!model.clusters.some(c=>c.id===active))active=null;render();}};
  };
})();

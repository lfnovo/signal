(() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  window.inlineField = ({key, value='', original=value, label, multiline=false, max=500, heading=0, placeholder='Click to add…'}) => {
    const editing=value!==original, tag=heading?`h${heading}`:'div';
    return `<div class="inline-field ${heading?'inline-heading':'inline-copy'}" data-inline-key="${key}" data-inline-original="${escape(original)}">
      ${!heading?`<div class="inline-label">${escape(label)}</div>`:''}
      <${tag} class="inline-display" ${editing?'hidden':''}><button type="button" class="inline-trigger ${original?'':'is-empty'}" data-inline-action="edit" aria-label="Edit ${escape(label)}"><span data-inline-text>${escape(original||placeholder)}</span><span class="inline-pencil" aria-hidden="true">✎</span></button></${tag}>
      <div data-inline-editor ${editing?'':'hidden'}>${multiline?`<textarea rows="4" maxlength="${max}" aria-label="${escape(label)}">${escape(value)}</textarea>`:`<input value="${escape(value)}" maxlength="${max}" required aria-label="${escape(label)}">`}
      <div class="inline-edit-actions"><button type="button" class="button" data-inline-action="save">Save</button><button type="button" class="text-button" data-inline-action="cancel">Cancel</button><span class="muted">${multiline?'⌘ / Ctrl + Enter':'Enter'} saves · Esc cancels</span></div><p class="inline-error" role="alert" hidden></p></div>
    </div>`;
  };
  function change(field) {
    field.dispatchEvent(new CustomEvent('signal:inline-change',{bubbles:true,detail:{key:field.dataset.inlineKey,value:field.querySelector('input,textarea').value}}));
  }
  function close(field,value) {
    field.dataset.inlineOriginal=value;
    field.querySelector('input,textarea').value=value;
    const text=field.querySelector('[data-inline-text]');text.textContent=value||'Click to add…';
    field.querySelector('.inline-trigger').classList.toggle('is-empty',!value);
    field.querySelector('[data-inline-editor]').hidden=true;
    field.querySelector('.inline-display').hidden=false;
    field.querySelector('.inline-error').hidden=true;
    change(field);field.querySelector('.inline-trigger').focus({preventScroll:true});
  }
  function busy(field,state) {
    field.dataset.saving=String(state);
    field.querySelectorAll('button,input,textarea').forEach(el=>el.disabled=state);
  }
  async function save(field) {
    if(field.dataset.saving==='true')return;
    const input=field.querySelector('input,textarea');if(!input.reportValidity())return;
    const value=input.value.trim();
    if(input.required&&!value){input.setCustomValidity('Give this a name.');input.reportValidity();return;}
    if(value===field.dataset.inlineOriginal){close(field,value);return;}
    busy(field,true);field.querySelector('.inline-error').hidden=true;
    const commit = saved => {busy(field,false);close(field,saved ?? value);};
    const reject = error => {busy(field,false);const message=field.querySelector('.inline-error');message.textContent=error.message||String(error);message.hidden=false;};
    if(field.dataset.sourceTitle){
      try {
        const id=field.dataset.sourceTitle;
        const result=await api('/api/sources/'+id+'/title',json('PUT',{title:value}));
        commit(result.title);
        document.querySelectorAll(`.source-row[data-source-id="${CSS.escape(id)}"]`).forEach(row=>{row.querySelector('.source-title-link').textContent=result.title;row.querySelector('.source-icon').setAttribute('aria-label','Open '+result.title);});
        if(document.querySelector('#source-detail')?.dataset.sourceId===id){document.title=result.title+' / Signal';const strong=document.querySelector('#delete-source-dialog strong');if(strong)strong.textContent=result.title;}
        document.dispatchEvent(new CustomEvent('signal:source-title-changed',{detail:{id,title:result.title}}));
      }catch(error){reject(error);}
    }else{
      field.dispatchEvent(new CustomEvent('signal:inline-save',{bubbles:true,detail:{key:field.dataset.inlineKey,value,commit,reject}}));
    }
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-inline-action]');if(!button)return;
    const field=button.closest('.inline-field');if(!field||field.dataset.saving==='true')return;
    event.preventDefault();
    if(button.dataset.inlineAction==='edit'){
      field.querySelector('.inline-display').hidden=true;field.querySelector('[data-inline-editor]').hidden=false;
      field.querySelector('input,textarea').focus();
    }else if(button.dataset.inlineAction==='cancel')close(field,field.dataset.inlineOriginal);
    else save(field);
  });
  document.addEventListener('input',event=>{const field=event.target.closest('.inline-field');if(field){event.target.setCustomValidity('');change(field);}});
  document.addEventListener('keydown',event=>{
    const field=event.target.closest('.inline-field');if(!field||field.querySelector('[data-inline-editor]').hidden || event.isComposing)return;
    if(event.key==='Escape'){
      event.preventDefault();event.stopPropagation();if(field.dataset.saving!=='true')close(field,field.dataset.inlineOriginal);
    }else if(event.key==='Enter' && (event.target.tagName==='INPUT'||(event.target.tagName==='TEXTAREA'&&(event.metaKey||event.ctrlKey)))){
      event.preventDefault();event.stopPropagation();save(field);
    }
  },true);
  window.addEventListener('beforeunload',event=>{
    if([...document.querySelectorAll('.inline-field')].some(f=>f.dataset.saving==='true'||f.querySelector('input,textarea').value!==f.dataset.inlineOriginal)){event.preventDefault();event.returnValue='';}
  });
})();

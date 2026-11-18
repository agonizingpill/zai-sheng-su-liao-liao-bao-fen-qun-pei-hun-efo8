import{heading}from'../components/labels.js';import{esc,kg}from'../utils/format.js';import{api}from'../utils/api.js';import{PACK_STATES}from'../constants.js';
const sourceRows=(packs,selected=new Set())=>packs.map(p=>`<label class="repack-source"><input type="checkbox" name="source" value="${p.id}" ${selected.has(p.id)?'checked':''}><span><b>${esc(p.code)}</b> ${kg(p.kg)}kg · ${PACK_STATES[p.state]||esc(p.state)}</span></label>`).join('');

const targetRow=(t={code:'',kg:''})=>`<div class="repack-target"><div><label>新箱码</label><input class="target-code" placeholder="PK-新编号" value="${esc(t.code)}" required></div><div><label>新箱重量 kg</label><input class="target-kg" type="number" min="0.001" step="0.001" value="${t.kg}" required></div><button type="button" class="remove-target" aria-label="删除新箱">×</button></div>`;

function traceChain(batches,packId){
  // Walk upstream through every merge source, then restore chronological order.
  const lines=[],seen=new Set();let frontier=[packId];
  while(frontier.length){
    const batch=batches.find(b=>b.targets.some(t=>frontier.includes(t.pack_id)));
    if(!batch||seen.has(batch.id))break;
    seen.add(batch.id);
    lines.push(`第${batch.id}批 ${batch.sources.map(t=>esc(t.pack_code)).join(' + ')} → ${batch.targets.map(t=>esc(t.pack_code)).join(' + ')}；原重${kg(batch.source_total_kg)}kg / 新箱${kg(batch.target_total_kg)}kg / 损耗${kg(batch.loss_kg)}kg`);
    frontier=[...new Set(batch.sources.filter(t=>t.origin_batch_id).map(t=>t.pack_id))];
  }
  return lines.reverse();
}

export function render(s,u){
  const certs=s.pellets.filter(p=>p.state==='certified');
  const cert=certs.find(p=>p.id===s.repackCheck)||certs[0];
  const packs=cert?s.packs.filter(p=>p.certificate_code===cert.certificate_code):[];
  const sealed=packs.filter(p=>p.state==='sealed');
  const selected=new Set([...(s.repackSources||new Set())].filter(id=>sealed.some(p=>p.id===id)));
  s.repackSources=selected;
  const selPacks=sealed.filter(p=>selected.has(p.id));
  const sourceTotal=selPacks.reduce((a,p)=>a+Number(p.kg),0);
  const packing=cert?.packing;
  const targetTotal=0;
  const loss=Math.round((sourceTotal-targetTotal)*1000)/1000;
  return heading('10','拆箱合箱，重装不重产出。','同证书未装车的已封箱码可整批拆箱合箱；原重 = 新箱总重 + 损耗。旧箱退出流转但保留来源，新箱沿用原证书与称料分群快照。',s.repacks.length,'拆箱重装批次')+`<div class="quality-flow"><aside><h2>整批拆箱合箱</h2><form id="repack-create"><label>成品证书<select name="check_id" id="repack-check">${certs.map(p=>`<option value="${p.id}" ${p.id===cert?.id?'selected':''}>${esc(p.blend_code)} / ${esc(p.certificate_code)} / 剩余可包装${kg(p.packing?.remaining_kg??0)}kg</option>`).join('')}</select></label>${cert?`<div class="quality-metrics repack-summary"><div><small>原箱总重</small><strong id="sum-source">${kg(sourceTotal)}</strong></div><div><small>新箱总重</small><strong id="sum-target">${kg(targetTotal)}</strong></div><div><small>损耗</small><strong id="sum-loss" class="${loss<0?'bad':''}">${kg(loss)}</strong></div></div><fieldset class="repack-sources"><legend>选择同证书已封未装车箱码（整批）</legend>${sourceRows(sealed,selected)||'<p class="tiny">没有可拆的同证书已封箱码。</p>'}</fieldset><div id="repack-targets">${[{}].map(t=>targetRow(t)).join('')}</div><button type="button" class="ghost" id="add-target">＋ 加一新箱</button><label>重装说明（可选）<input name="note" maxlength="600" placeholder="客户换规格、合箱原因"></label><p class="rule">原重必须等于新箱总重加损耗；损耗占用产出额度，不能再包装回来。已装车箱拒绝重装，发车清单保持原样。同一重放请求不会重复生成。</p><button class="orange" ${u.role==='viewer'||!sealed.length?'disabled':''}>提交整批拆箱合箱</button>`:'<p class="empty">暂无已签证成品可拆箱。</p>'}</form><div class="lot-list">${s.repacks.map(x=>`<button class="lot-pick" data-repack="${x.id}" data-check="${x.check_id}"><b>${esc(x.certificate_code)} · 第${x.id}批</b><span>原重${kg(x.source_total_kg)}kg / 新箱${kg(x.target_total_kg)}kg / 损耗${kg(x.loss_kg)}kg</span></button>`).join('')}</div></aside><section>${renderDetail(s,cert,packs,selPacks,sourceTotal,packing)}</section></div>`;
}

function renderDetail(s,cert,packs,selPacks,sourceTotal,packing){
  if(!cert)return '<div class="empty">先签发成品合格证并封箱，才能办理换规格重装。</div>';
  const chainPacks=packs.filter(p=>p.repack_batch_id||p.repacked_into_batch_id);
  const batches=s.repacks.filter(x=>x.check_id===cert.id||x.certificate_code===cert.certificate_code);
  return `<div class="scalehead"><h2>${esc(cert.blend_code)} · ${esc(cert.certificate_code)}</h2><span class="tag certified">主管已发合格证</span></div>
  <div class="quality-metrics"><div><small>合格产出</small><strong>${kg(packing?.actual_kg??cert.actual_kg)}kg</strong></div><div><small>有效箱码占用</small><strong>${kg(packing?.live_packed_kg??0)}kg</strong></div><div><small>历次损耗</small><strong>${kg(packing?.repack_loss_kg??0)}kg</strong></div><div><small>剩余可包装</small><strong>${kg(packing?.remaining_kg??0)}kg</strong></div></div>
  <p class="note">损耗累计占用证书产出额度，任何损耗都不能重新包装回来。来源箱码退出流转但保留箱体与证书快照，新箱沿用原证书及称料分群快照。</p>
  <div class="trace-table"><table><thead><tr><th>箱码</th><th>重量</th><th>状态</th><th>重装批次</th></tr></thead><tbody>${packs.map(p=>`<tr><td>${esc(p.code)}</td><td>${kg(p.kg)}kg</td><td>${PACK_STATES[p.state]||esc(p.state)}</td><td>${p.repack_batch_id?`由第${p.repack_batch_id}批产生`:''}${p.repacked_into_batch_id?` ／ 已拆入第${p.repacked_into_batch_id}批`:''}</td></tr>`).join('')||'<tr><td colspan="4">尚无箱码。</td></tr>'}</tbody></table></div>
  <h3>多次拆合追溯</h3><div class="trace-table"><table><thead><tr><th>批次</th><th>来源箱码</th><th>新箱</th><th>原重 / 新箱 / 损耗</th><th>办理人</th></tr></thead><tbody>${batches.map(x=>`<tr><td>第${x.id}批<br><small>${esc(x.blend_code)} / ${esc(x.certificate_code)}</small></td><td>${x.sources.map(t=>`${esc(t.pack_code)} ${kg(t.kg)}kg`).join('<br>')}</td><td>${x.targets.map(t=>`${esc(t.pack_code)} ${kg(t.kg)}kg`).join('<br>')}</td><td>${kg(x.source_total_kg)} / ${kg(x.target_total_kg)} / ${kg(x.loss_kg)}kg</td><td>${esc(x.created_by)}<br><small>${esc(x.note||'')}</small></td></tr>`).join('')||'<tr><td colspan="5">尚无拆箱合箱记录。</td></tr>'}</tbody></table></div>
  ${chainPacks.map(p=>`<details><summary>${esc(p.code)} 的来源链</summary>${traceChain(batches,p.id).map(l=>`<p>${esc(l)}</p>`).join('')||'<p class="tiny">原始包装箱，无拆合来源。</p>'}</details>`).join('')}`;
}

export function bind(refresh,render,s){
  document.querySelectorAll('[data-repack]').forEach(b=>b.onclick=()=>{s.repackCheck=+b.dataset.check;s.repackSources=new Set();render()});
  const certSel=document.querySelector('#repack-check');
  if(certSel)certSel.onchange=()=>{s.repackCheck=+certSel.value;s.repackSources=new Set();render()};
  const sum=()=>{
    const src=[...document.querySelectorAll('input[name="source"]:checked')].reduce((a,cb)=>a+Number((s.packs.find(p=>p.id===+cb.value)||{}).kg||0),0);
    const tgt=[...document.querySelectorAll('.repack-target')].reduce((a,row)=>a+Number(row.querySelector('.target-kg').value||0),0);
    const loss=Math.round((src-tgt)*1000)/1000;
    const set=(id,v,cls)=>{const el=document.querySelector(id);if(el){el.textContent=kg(v);el.classList.toggle('bad',cls)}};
    set('#sum-source',src,false);set('#sum-target',tgt,false);set('#sum-loss',loss,loss<0);
  };
  document.querySelectorAll('input[name="source"]').forEach(cb=>cb.onchange=()=>{s.repackSources=new Set([...document.querySelectorAll('input[name="source"]:checked')].map(x=>+x.value));sum()});
  const attach=()=>{document.querySelectorAll('.remove-target').forEach(b=>b.onclick=()=>{if(document.querySelectorAll('.repack-target').length>1)b.closest('.repack-target').remove();sum()});document.querySelectorAll('.target-kg').forEach(i=>i.oninput=sum)};
  attach();
  const add=document.querySelector('#add-target');
  if(add)add.onclick=()=>{document.querySelector('#repack-targets').insertAdjacentHTML('beforeend',targetRow());attach();sum()};
  const f=document.querySelector('#repack-create');
  if(f)f.onsubmit=e=>{e.preventDefault();const sources=[...f.querySelectorAll('input[name="source"]:checked')].map(x=>+x.value);const targets=[...f.querySelectorAll('.repack-target')].map(row=>({code:row.querySelector('.target-code').value.trim(),kg:+row.querySelector('.target-kg').value}));const note=f.querySelector('input[name="note"]').value;refresh(async()=>{const x=await api('/repacks','POST',{source_pack_ids:sources,targets,note,request_key:''});s.repackCheck=x.check_id;s.repackSources=new Set();s.repackSelected=x.id},'整批拆箱合箱已办理，新箱沿用原证书')};
}

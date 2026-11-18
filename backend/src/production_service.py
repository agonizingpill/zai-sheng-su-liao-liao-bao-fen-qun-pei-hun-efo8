from datetime import datetime,timezone
from hashlib import sha256
from zoneinfo import ZoneInfo
from decimal import Decimal
from fastapi import HTTPException
from sqlalchemy import select,func,update
from .models import Blend
from .quality_models import PelletCheck
from .production_models import Dryer,DryingRun,PelletPack,BlendShipment,ShipmentPack,RepackBatch,RepackSource,RepackTarget
from .blending import obj,get,audit,view

def stamp(v):return (v.replace(tzinfo=ZoneInfo('Asia/Shanghai'))if v.tzinfo is None else v).astimezone(timezone.utc)
def now():return datetime.now(timezone.utc).isoformat()
def dry_view(s,x):return {**obj(x),'blend_code':s.get(Blend,x.blend_id).code,'dryer_code':s.get(Dryer,x.dryer_id).code}
def pack_view(s,x,keep_state=None):
    p=s.get(PelletCheck,x.check_id)
    target=s.scalar(select(RepackTarget).where(RepackTarget.pack_id==x.id))
    source=s.scalar(select(RepackSource).where(RepackSource.pack_id==x.id))
    d={**obj(x),'blend_code':s.get(Blend,p.blend_id).code,'certificate_code':p.certificate_code,'repack_batch_id':target.batch_id if target else None,'repacked_into_batch_id':source.batch_id if source else None}
    if keep_state:d['state']=keep_state
    return d
def ship_view(s,x):
    packs=[{**obj(link),'pack_code':s.get(PelletPack,link.pack_id).code}for link in s.scalars(select(ShipmentPack).where(ShipmentPack.shipment_id==x.id).order_by(ShipmentPack.id))]
    return {**obj(x),'packs':packs,'total_kg':float(sum((Decimal(str(p['pack_snapshot']['kg']))for p in packs),Decimal(0)))}
def room(s,dryer_id,start,end,exclude=None):
    for x in s.scalars(select(DryingRun).where(DryingRun.dryer_id==dryer_id).with_for_update().execution_options(populate_existing=True)):
        if x.id!=exclude and datetime.fromisoformat(x.starts_at)<end and datetime.fromisoformat(x.ends_at)>start:raise HTTPException(409,'烘干设备在此时段已有作业，不能重叠占用')
def create_dry(s,p,u):
    dryer=get(s,Dryer,p.dryer_id,True)
    if dryer.state!='active':raise HTTPException(409,'烘干设备已停用')
    blend=get(s,Blend,p.blend_id,True)
    if blend.status!='released':raise HTTPException(409,'只有已投料配方可安排烘干')
    if s.scalar(select(DryingRun.id).where(DryingRun.blend_id==blend.id)):raise HTTPException(409,'配方已有烘干作业，需在原单延烘复测')
    start,end=stamp(p.starts_at),stamp(p.ends_at)
    if end<=start:raise HTTPException(409,'烘干结束须晚于开始')
    room(s,dryer.id,start,end);snap=view(s,blend);weight=sum(Decimal(str(x['kg']))for x in snap['portions'])
    moisture=sum(Decimal(str(x['kg']))*Decimal(str(x['moisture_snapshot']))for x in snap['portions'])/weight
    x=DryingRun(blend_id=blend.id,dryer_id=dryer.id,input_moisture=moisture.quantize(Decimal('.0001')),starts_at=start.isoformat(),ends_at=end.isoformat(),state='drying',measurements=[],input_snapshot=snap);s.add(x);s.flush();audit(s,u,'drying_started',blend.code);return dry_view(s,x)
def measure_dry(s,id,p,u):
    candidate=get(s,DryingRun,id);get(s,Dryer,candidate.dryer_id,True);x=get(s,DryingRun,id,True)
    if x.state not in ('drying','redry'):raise HTTPException(409,'仅烘干中或待延烘可复测，合格或已验收结果冻结')
    end=stamp(p.ends_at)
    if end<datetime.fromisoformat(x.ends_at):raise HTTPException(409,'延烘复测结束时间不得早于已占用结束时间')
    room(s,x.dryer_id,datetime.fromisoformat(x.starts_at),end,x.id)
    x.ends_at=end.isoformat();x.after_moisture=p.after_moisture;x.state='passed'if p.after_moisture<=Decimal('.05')else'redry'
    x.measurements=[*x.measurements,{'after_moisture':float(p.after_moisture),'ends_at':x.ends_at,'note':p.note,'actor':u['email'],'at':now()}];audit(s,u,'drying_measured',str(x.id)+' / '+x.state);return dry_view(s,x)
def accept_dry(s,id,u):
    x=get(s,DryingRun,id,True)
    if x.state!='passed':raise HTTPException(409,'烘后含水须≤0.05%且未验收，才能主管确认')
    x.state='accepted';x.accepted_by=u['email'];audit(s,u,'drying_accepted',str(x.id));return dry_view(s,x)
def accepted_drying(s,blend_id):
    x=s.scalar(select(DryingRun).where(DryingRun.blend_id==blend_id,DryingRun.state=='accepted'))
    if not x:raise HTTPException(409,'成品新签证须有同配方主管验收合格的烘干作业')
    return dry_view(s,x)
def available_pack_weight(s,p,kg,exclude=None):
    live=sum((x.kg for x in s.scalars(select(PelletPack).where(PelletPack.check_id==p.id,PelletPack.state.notin_(('cancelled','repacked'))).with_for_update().execution_options(populate_existing=True))if x.id!=exclude),Decimal(0))
    loss=Decimal(str(s.scalar(select(func.coalesce(func.sum(RepackBatch.loss_kg),0)).where(RepackBatch.check_id==p.id))or 0))
    remaining=p.actual_kg-live-loss
    if kg>remaining:raise HTTPException(409,f'累计有效包装与历次拆箱损耗占用产出额度，剩余可包装{remaining}kg，不分配超额箱码')
def create_pack(s,p,u):
    check=get(s,PelletCheck,p.check_id,True)
    if check.state!='certified':raise HTTPException(409,'仅主管已发合格证的成品可以包装')
    available_pack_weight(s,check,p.kg);x=PelletPack(**p.model_dump(),state='open',certificate_snapshot=check.certificate_snapshot);s.add(x);s.flush();audit(s,u,'pack_created',x.code);return pack_view(s,x)
def change_pack(s,id,p,u):
    candidate=get(s,PelletPack,id);check=get(s,PelletCheck,candidate.check_id,True);x=get(s,PelletPack,id,True)
    if x.state!='open':raise HTTPException(409,'只有未封包装可改重量，封包与出库后冻结')
    available_pack_weight(s,check,p.kg,x.id);x.kg=p.kg;audit(s,u,'pack_reweighed',x.code);return pack_view(s,x)
def pack_action(s,id,u,target):
    candidate=get(s,PelletPack,id);get(s,PelletCheck,candidate.check_id,True);x=get(s,PelletPack,id,True)
    if x.state!='open':raise HTTPException(409,'仅未封包装可封包或取消，不可重复')
    x.state=target;audit(s,u,'pack_'+target,x.code);return pack_view(s,x)
def load_pack(s,id,p,u):
    sh=get(s,BlendShipment,id,True)
    if sh.state!='loading':raise HTTPException(409,'发车后清单冻结，不能继续装车')
    pack=get(s,PelletPack,p.pack_id,True)
    if pack.state!='sealed':raise HTTPException(409,'只有已封箱且未装车箱码可装入清单')
    if s.scalar(select(ShipmentPack.id).where(ShipmentPack.pack_id==pack.id)):raise HTTPException(409,'箱码已装车，不得重复出库')
    check=get(s,PelletCheck,pack.check_id)
    for link in s.scalars(select(ShipmentPack).where(ShipmentPack.shipment_id==sh.id)):
        previous=get(s,PelletCheck,get(s,PelletPack,link.pack_id).check_id)
        if previous.blend_id!=check.blend_id:raise HTTPException(409,'同一车次只允许同配方成品，不可跨配方混装')
    # Claim the sealed box atomically: a concurrent repack of the same box can only lose here.
    claimed=s.execute(update(PelletPack).where(PelletPack.id==pack.id,PelletPack.state=='sealed').values(state='loaded')).rowcount
    if claimed!=1:raise HTTPException(409,'箱码已被并发重装或装车占用，本次装车未生效')
    pack.state='loaded'
    s.add(ShipmentPack(shipment_id=sh.id,pack_id=pack.id,pack_snapshot=pack_view(s,pack)));s.flush();audit(s,u,'shipment_loaded',sh.code+' / '+pack.code);return ship_view(s,sh)
def dispatch(s,id,u):
    sh=get(s,BlendShipment,id,True)
    if sh.state!='loading':raise HTTPException(409,'仅装车中清单可发车，不可重复出库')
    snap=ship_view(s,sh)
    if not snap['packs']:raise HTTPException(409,'清单没有已封箱成品，不能发车')
    sh.manifest={'code':sh.code,'customer':sh.customer,'packs':snap['packs'],'total_kg':snap['total_kg'],'at':now(),'dispatched_by':u['email']};sh.dispatched_by=u['email'];sh.state='dispatched';audit(s,u,'shipment_dispatched',sh.code);return ship_view(s,sh)
def receive(s,id,p,u):
    sh=get(s,BlendShipment,id,True)
    if sh.state!='dispatched':raise HTTPException(409,'仅已发车清单可签收，不能重复')
    manifest_kg=Decimal(str(sh.manifest['total_kg']));variance=(p.received_kg-manifest_kg)/manifest_kg*100
    if abs(variance)>Decimal('.5')and len(p.difference_note.strip())<8:raise HTTPException(409,'签收重量与清单偏差超过0.5%，须至少8字差异说明')
    sh.receipt_snapshot={**p.model_dump(mode='json'),'manifest_kg':float(manifest_kg),'variance_pct':float(variance.quantize(Decimal('.0001'))),'at':now(),'recorded_by':u['email']};sh.state='received';audit(s,u,'shipment_received',sh.code);return ship_view(s,sh)

def packing_summary(s,check):
    rows=s.scalars(select(PelletPack).where(PelletPack.check_id==check.id).order_by(PelletPack.id)).all()
    live=sum((x.kg for x in rows if x.state not in ('cancelled','repacked')),Decimal(0))
    losses=Decimal(str(s.scalar(select(func.coalesce(func.sum(RepackBatch.loss_kg),0)).where(RepackBatch.check_id==check.id))or 0))
    remaining=check.actual_kg-live-losses
    return {'actual_kg':float(check.actual_kg),'live_packed_kg':float(live),'repack_loss_kg':float(losses),'remaining_kg':float(remaining),
            'packs':{'open':sum(1 for x in rows if x.state=='open'),'sealed':sum(1 for x in rows if x.state=='sealed'),'loaded':sum(1 for x in rows if x.state=='loaded'),'cancelled':sum(1 for x in rows if x.state=='cancelled'),'repacked':sum(1 for x in rows if x.state=='repacked')}}

def repack_view(s,x,current=False):
    check=s.get(PelletCheck,x.check_id);blend=s.get(Blend,check.blend_id)
    pack=lambda pk_id:(get(s,PelletPack,pk_id,True)if current else s.get(PelletPack,pk_id))
    def rows(model):
        q=select(model).where(model.batch_id==x.id).order_by(model.id)
        if current:q=q.with_for_update().execution_options(populate_existing=True)
        return s.scalars(q)
    sources=[]
    for r in rows(RepackSource):
        pk=pack(r.pack_id);origin=s.scalar(select(RepackTarget).where(RepackTarget.pack_id==pk.id))
        sources.append({**obj(r),'pack_code':pk.code,'pack_state':pk.state,'origin_batch_id':origin.batch_id if origin else None})
    targets=[]
    for r in rows(RepackTarget):
        pk=pack(r.pack_id);onward=s.scalar(select(RepackSource).where(RepackSource.pack_id==pk.id))
        targets.append({**obj(r),'pack_code':pk.code,'pack_state':pk.state,'next_batch_id':onward.batch_id if onward else None})
    summary=packing_summary(s,check)
    return {**obj(x),'blend_code':blend.code,'certificate_code':check.certificate_code,'sources':sources,'targets':targets,
            'packing':{k:(float(v) if isinstance(v,(Decimal,int)) else v)for k,v in summary.items()}}

def repack_trace(s,batch_id,seen=None,depth=0):
    if batch_id is None:return None
    seen=set(seen or ())
    if batch_id in seen:return {'truncated':True,'batch_id':batch_id}
    if depth>10:return {'truncated':True,'batch_id':batch_id}
    seen.add(batch_id);x=s.get(RepackBatch,batch_id)
    if not x:return None
    d=repack_view(s,x)
    d['sources']=[{**item,'upstream':repack_trace(s,item['origin_batch_id'],set(seen),depth+1)}for item in d['sources']]
    d['targets']=[{**item,'downstream':repack_trace(s,item['next_batch_id'],set(seen),depth+1)}for item in d['targets']]
    return d

def pack_trace(s,pack_id):
    pk=get(s,PelletPack,pack_id)
    produced=s.scalar(select(RepackTarget).where(RepackTarget.pack_id==pk.id))
    consumed=s.scalar(select(RepackSource).where(RepackSource.pack_id==pk.id))
    return {'pack':pack_view(s,pk),'produced_by':repack_trace(s,produced.batch_id)if produced else None,'consumed_by':repack_trace(s,consumed.batch_id)if consumed else None}

def repack_key(check_id,source_ids,targets,explicit=''):
    explicit=(explicit or '').strip()
    if explicit:return 'CLI-'+explicit
    canon='REPACK|'+str(check_id)+'|'+','.join(str(x)for x in sorted(source_ids))+'|'+';'.join(f'{t.code}={_three(t.kg)}'for t in sorted(targets,key=lambda t:(t.code,str(t.kg))))
    return 'RP-'+sha256(canon.encode()).hexdigest()

def _existing_repack(s,key,current=False):
    q=select(RepackBatch).where(RepackBatch.request_key==key)
    # A locking read sees a concurrently committed batch even under REPEATABLE READ.
    if current:q=q.with_for_update().execution_options(populate_existing=True)
    return s.scalar(q)

def _three(v):return Decimal(str(v)).quantize(Decimal('.001'))
def _replay(s,x,ids,targets,current=False):
    def rows(model):
        q=select(model).where(model.batch_id==x.id)
        if current:q=q.with_for_update().execution_options(populate_existing=True)
        return s.scalars(q)
    same_sources=set(ids)==set(r.pack_id for r in rows(RepackSource))
    wanted=sorted((t.code,_three(t.kg))for t in targets)
    stored=sorted((get(s,PelletPack,r.pack_id,current).code,_three(r.kg))for r in rows(RepackTarget))
    if not(same_sources and wanted==stored):raise HTTPException(409,'该重装请求号已被另一批箱码使用，请换请求号或按原单重试')
    return repack_view(s,x,current)

def create_repack(s,p,u):
    ids=[int(x)for x in p.source_pack_ids]
    if len(set(ids))!=len(ids):raise HTTPException(409,'同一箱码只能拆箱一次，来源箱码不得重复')
    codes=[t.code for t in p.targets]
    if len(set(codes))!=len(codes):raise HTTPException(409,'新箱码不得重复，整批合箱需一箱一号')
    first=s.scalar(select(PelletPack).where(PelletPack.id==ids[0]))
    if not first:raise HTTPException(404,'记录不存在')
    check=get(s,PelletCheck,first.check_id,True)
    key=repack_key(check.id,ids,p.targets,getattr(p,'request_key',''))
    # Fast path: replay of an already committed request returns the same batch.
    existing=_existing_repack(s,key)
    if existing:return _replay(s,existing,ids,p.targets)
    if s.scalar(select(PelletPack.id).where(PelletPack.code.in_(codes)).limit(1)):raise HTTPException(409,'新箱码已被占用，请更换唯一箱码')
    packs=list(s.scalars(select(PelletPack).where(PelletPack.id.in_(ids)).order_by(PelletPack.id).with_for_update().execution_options(populate_existing=True)))
    if len(packs)!=len(ids):raise HTTPException(404,'记录不存在')
    if any(x.check_id!=check.id for x in packs):raise HTTPException(409,'同一次拆箱合箱只能处理同一证书的箱码')
    if check.state!='certified' or not check.certificate_snapshot:raise HTTPException(409,'只有主管已签发的成品证书箱码可以拆箱合箱')
    for x in packs:
        if s.scalar(select(ShipmentPack.id).where(ShipmentPack.pack_id==x.id).limit(1)):raise HTTPException(409,f'{x.code}已装入发车清单，拒绝重装且清单保持不变')
        if x.state=='loaded':raise HTTPException(409,f'{x.code}已装车，禁止拆箱重装，发车清单保持不变')
        if x.state=='repacked':
            # A concurrent identical request may have committed while we waited for the row locks.
            existing=_existing_repack(s,key,current=True)
            if existing:return _replay(s,existing,ids,p.targets,True)
            raise HTTPException(409,f'{x.code}已拆箱退出流转，不能重复重装')
        if x.state!='sealed':raise HTTPException(409,f'{x.code}不是已封箱状态，只有同证书未装车封箱箱码可整批拆箱')
    source_total=sum((x.kg for x in packs),Decimal(0))
    target_total=sum((t.kg for t in p.targets),Decimal(0))
    loss=source_total-target_total
    if loss<0:raise HTTPException(409,f'新箱总重{target_total}kg不能超过原箱总重{source_total}kg')
    snap=check.certificate_snapshot
    # Both repack and loading claim sealed boxes; only one side can move each box out of sealed.
    # Claim before writing the batch header so a losing request leaves no batch or loss behind.
    claimed=s.execute(update(PelletPack).where(PelletPack.id.in_(sorted(ids)),PelletPack.state=='sealed').values(state='repacked')).rowcount
    if claimed!=len(ids):
        # The loser may be a concurrent retry of this same request; return its committed batch.
        existing=_existing_repack(s,key,current=True)
        if existing:return _replay(s,existing,ids,p.targets,True)
        raise HTTPException(409,'箱码已被并发装车或另一重装占用，本次未生成任何新箱或损耗')
    x=RepackBatch(check_id=check.id,request_key=key,source_total_kg=source_total,target_total_kg=target_total,loss_kg=loss,certificate_snapshot=snap,note=(p.note or '').strip(),created_by=u['email'],created_at=now())
    s.add(x);s.flush()
    for pk in packs:
        snapshot=pack_view(s,pk,keep_state='sealed')
        pk.state='repacked'
        s.add(RepackSource(batch_id=x.id,pack_id=pk.id,kg=pk.kg,pack_snapshot=snapshot))
    for t in p.targets:
        pk=PelletPack(check_id=check.id,code=t.code,kg=t.kg,state='sealed',certificate_snapshot=snap)
        s.add(pk);s.flush();s.add(RepackTarget(batch_id=x.id,pack_id=pk.id,kg=t.kg))
    audit(s,u,'packs_repacked',f'{x.id} / 原重{source_total}kg = 新箱{target_total}kg + 损耗{loss}kg / 来源'+','.join(pk.code for pk in packs))
    s.flush();return repack_view(s,x)

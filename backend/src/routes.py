import os,redis
from fastapi import APIRouter,Depends,HTTPException
from .database import Session
from .models import *
from .schemas import *
from .common import *
from .security import current,allow
from . import consignment_service as consignment,fit_service as fit,fitting_service as fitting,sale_service as sale,return_service as returns,settlement_service as settlement
router=APIRouter()
def change(u,action,fn,supervisor=False):
 allow(u,*(['supervisor'] if supervisor else ['operator','supervisor']))
 with Session() as s:x=fn(s);audit(s,u,action,x);s.commit();return dump(x)
def batch_view(b):
 return {k:getattr(b,k) for k in ['id','version_no','kind','state','train_count','holdout_count','train_sha256','holdout_sha256','incumbent_version_no','metrics','decision','note','activated','error','actor','created_at','completed_at']}
def model_state(s):
 st=s.get(ModelState,1)
 if not st or not st.active_batch_id:return {'active_batch_id':None,'label':'模拟标注基线','updated_at':st.updated_at if st else None}
 b=s.get(TrainingBatch,st.active_batch_id)
 return {'active_batch_id':st.active_batch_id,'label':f'试穿训练 v{b.version_no}','version_no':b.version_no,'updated_at':st.updated_at}
def queue(name,record_id):
 redis.from_url(os.environ['REDIS_URL'],socket_connect_timeout=1,socket_timeout=2).rpush(name,record_id)
@router.get('/register')
def register(u=Depends(current)):
 with Session() as s:
  data={n:[dump(x) for x in rows(s,c)] for n,c in [('consignors',Consignor),('agreements',Agreement),('amendments',Amendment),('garments',Garment),('measurements',Measurement),('profiles',FitProfile),('assessments',FitAssessment),('fittings',Fitting),('sales',Sale),('returns',Return),('payouts',Payout),('withdrawals',Withdrawal),('clock',Shop)]}
  data['tryon_labels']=[dump(x) for x in rows(s,TryOnLabel)]
  data['holdout_profiles']=[dump(x) for x in rows(s,HoldoutProfile)]
  data['training_batches']=[batch_view(x) for x in rows(s,TrainingBatch)]
  data['model_state']=model_state(s)
  return data
@router.get('/audit')
def audits(u=Depends(current)):
 with Session() as s:return [dump(x) for x in rows(s,Audit)]
@router.post('/consignors')
def consignors(d:ConsignorIn,u=Depends(current)):return change(u,'寄售人登记',lambda s:consignment.consignor(s,d.model_dump()))
@router.post('/agreements')
def agreements(d:AgreementIn,u=Depends(current)):return change(u,'寄售协议',lambda s:consignment.agreement(s,d.model_dump()))
@router.post('/agreements/{id}/commission')
def amend(id:int,d:AmendmentIn,u=Depends(current)):return change(u,'佣金修订',lambda s:consignment.amend(s,id,d.model_dump()),True)
@router.post('/garments')
def garments(d:GarmentIn,u=Depends(current)):return change(u,'入店验衣',lambda s:consignment.intake(s,d.model_dump()))
@router.post('/measurements')
def measurements(d:MeasurementIn,u=Depends(current)):return change(u,'服饰实测',lambda s:fit.measure(s,d.model_dump()))
@router.post('/profiles')
def profiles(d:ProfileIn,u=Depends(current)):return change(u,'匿名体型',lambda s:fit.profile(s,d.model_dump()))
@router.post('/tryon-labels')
def tryon_labels(d:TryOnLabelIn,u=Depends(current)):
 allow(u,'operator','supervisor')
 with Session() as s:
  x,corrected=fit.label(s,d.model_dump(),u['email']);audit(s,u,'试穿松紧更正' if corrected else '试穿松紧登记',x);s.commit();return dump(x)
@router.post('/holdouts')
def holdouts(d:HoldoutIn,u=Depends(current)):return change(u,'留出匿名体型',lambda s:fit.holdout(s,d.model_dump(),u['email']),True)
@router.post('/training-batches')
def training_batches(d:TrainingBatchIn,u=Depends(current)):
 allow(u,'supervisor')
 with Session() as s:
  x=fit.start_batch(s,d.model_dump(),u['email']);audit(s,u,'发起模型训练',x);s.commit();view=dump(x)
 try:queue('resale:train',view['id'])
 except Exception:
  with Session() as s:x=s.get(TrainingBatch,view['id']);x.state='failed';x.error='训练队列不可达，可重试';x.decision='未启用：训练队列不可达';s.commit()
  raise HTTPException(503,'训练队列暂时不可达，批次已标记失败，可稍后重试')
 return batch_view(x)
@router.post('/training-batches/{id}/retry')
def retry_batch(id:int,u=Depends(current)):
 allow(u,'supervisor')
 with Session() as s:
  x=fit.retry_batch(s,id);audit(s,u,'重试模型训练',x);s.commit();view=batch_view(x)
 try:queue('resale:train',view['id'])
 except Exception:
  with Session() as s:x=s.get(TrainingBatch,view['id']);x.state='failed';x.error='训练队列不可达，可重试';s.commit()
  raise HTTPException(503,'训练队列暂时不可达，批次仍为失败，可稍后重试')
 return view
@router.post('/assessments')
def assessments(d:AssessmentIn,u=Depends(current)):
 d=change(u,'适配计算',lambda s:fit.assess(s,d.model_dump()))
 try:queue('resale:fit',d['id'])
 except Exception:
  with Session() as s:x=s.get(FitAssessment,d['id']);x.state='failed';x.error='适配队列不可达，请重试';s.commit()
  raise HTTPException(503,'适配队列暂时不可达，记录已标记失败，可重新提交')
 return d
@router.post('/fittings')
def fittings(d:FittingIn,u=Depends(current)):return change(u,'排他留衣',lambda s:fitting.reserve(s,d.model_dump()))
@router.post('/fittings/{id}/cancel')
def cancel(id:int,u=Depends(current)):return change(u,'取消试衣',lambda s:fitting.cancel(s,id))
@router.post('/sales')
def sales(d:SaleIn,u=Depends(current)):return change(u,'试衣成交',lambda s:sale.sell(s,d.model_dump()))
@router.post('/returns')
def ret(d:ReturnIn,u=Depends(current)):return change(u,'适配退货',lambda s:returns.request(s,d.model_dump()))
@router.post('/returns/{id}/inspect')
def inspect(id:int,d:InspectionIn,u=Depends(current)):return change(u,'退衣复检',lambda s:returns.inspect(s,id,d.model_dump()),True)
@router.post('/payouts')
def payouts(d:PayoutIn,u=Depends(current)):return change(u,'寄售结算',lambda s:settlement.payout(s,d.model_dump()),True)
@router.post('/garments/{id}/withdraw')
def withdraw(id:int,d:ReasonIn,u=Depends(current)):return change(u,'期满撤回',lambda s:settlement.withdraw(s,id,d.model_dump()),True)
@router.post('/clock')
def clock(d:ClockIn,u=Depends(current)):return change(u,'推进演示时钟',lambda s:settlement.clock(s,d.model_dump()),True)

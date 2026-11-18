import logging
from sqlalchemy import select,func
from .models import *
from .common import *
from .constants import FIT_LABELS,TIGHT_LABEL,MIN_TRAIN_RECORDS,MIN_HOLDOUT_RECORDS
from . import fit_model


def measure(s,d):
 c=lock(s);g=garment(s,d['garment_id']);a=get(s,Agreement,g.agreement_id)
 if g.state not in ['unmeasured','listed'] or time(c.now)>=time(a.ends):fail('仅在有效寄售期未留衣前可实测')
 if any(x.garment_id==g.id for x in rows(s,Measurement)):fail('实测已登记，保留原记录')
 x=Measurement(**d);s.add(x);g.state='listed';s.flush();return x


def profile(s,d):
 lock(s);x=FitProfile(**d);s.add(x);s.flush();return x


def label(s,d,actor):
 c=lock(s);g=garment(s,d['garment_id']);get(s,FitProfile,d['profile_id'])
 if not any(x.garment_id==g.id for x in rows(s,Measurement)):fail('未实测服饰不得登记试穿松紧')
 x=s.scalars(select(TryOnLabel).where(TryOnLabel.garment_id==g.id,TryOnLabel.profile_id==d['profile_id'])).first()
 corrected=x is not None
 if corrected:x.label=d['label'];x.note=d['note'];x.recorded_by=actor;x.updated_at=c.now
 else:x=TryOnLabel(garment_id=g.id,profile_id=d['profile_id'],label=d['label'],note=d['note'],recorded_by=actor,updated_at=c.now);s.add(x)
 s.flush();return x,corrected


def holdout(s,d,actor):
 c=lock(s);get(s,FitProfile,d['profile_id'])
 if s.scalars(select(HoldoutProfile).where(HoldoutProfile.profile_id==d['profile_id'])).first():fail('该匿名体型已留出')
 x=HoldoutProfile(profile_id=d['profile_id'],reason=d['reason'],marked_by=actor,marked_at=c.now);s.add(x);s.flush();return x


def holdout_ids(s):
 return {h.profile_id for h in rows(s,HoldoutProfile)}


def record_pool(s):
 """当前已登记的全部试穿记录，按留出体型拆分为训练与评估两池。"""
 profiles={p.id:p for p in rows(s,FitProfile)};measurements={m.garment_id:m for m in rows(s,Measurement)}
 garments={g.id:g for g in rows(s,Garment)};held=holdout_ids(s)
 train=[];hold=[]
 for l in rows(s,TryOnLabel):
  m=measurements.get(l.garment_id);p=profiles.get(l.profile_id)
  if not m or not p:continue
  r={'label_id':l.id,'garment_id':l.garment_id,'garment_code':garments[l.garment_id].code,'profile_id':l.profile_id,'profile_code':p.code,'label':l.label,'features':fit_model.features_for(dump(m),dump(p)),'updated_at':l.updated_at}
  (hold if l.profile_id in held else train).append(r)
 return train,hold


def frozen_train(s,b):
 """批次冻结的训练记录再按当前留出名单过滤，留出体型始终不入训。"""
 held=holdout_ids(s);return [r for r in b.train_records if r['profile_id'] not in held]


def incumbent(s):
 """返回完成比较时仍在用的模型；没有已启用批次时回落模拟标注基线。"""
 state=s.get(ModelState,1);b=s.get(TrainingBatch,state.active_batch_id) if state and state.active_batch_id else None
 if b and b.state=='ready':
  records=frozen_train(s,b)
  return fit_model.train(records),{'source':'tryon','version':b.version_no,'batch_id':b.id,'label':f'在用试穿训练 v{b.version_no}','train_label_ids':{r['label_id'] for r in records}}
 tr,_te,digest=fit_model.synthetic_dataset()
 return fit_model.train(tr),{'source':'synthetic','version':None,'batch_id':None,'label':'模拟标注基线','dataset_sha256':digest,'train_label_ids':set()}


def start_batch(s,d,actor):
 c=lock(s);held=holdout_ids(s)
 if not held:fail('请先将匿名体型标记为留出，训练才有独立评估记录')
 train,hold=record_pool(s)
 if len(train)<MIN_TRAIN_RECORDS:fail(f'训练需至少{MIN_TRAIN_RECORDS}条非留出试穿登记，当前{len(train)}条')
 if len(hold)<MIN_HOLDOUT_RECORDS:fail(f'留出评估需至少{MIN_HOLDOUT_RECORDS}条登记，当前{len(hold)}条')
 if not any(r['label']==TIGHT_LABEL for r in hold):fail('留出记录缺少偏紧样本，无法验证偏紧召回不降')
 state=s.get(ModelState,1);active=s.get(TrainingBatch,state.active_batch_id) if state and state.active_batch_id else None
 version=(s.scalar(select(func.max(TrainingBatch.version_no))) or 0)+1
 x=TrainingBatch(version_no=version,kind='tryon',state='queued',train_records=train,holdout_records=hold,train_count=len(train),holdout_count=len(hold),train_sha256=fit_model.snapshot(train),holdout_sha256=fit_model.snapshot(hold),incumbent_version_no=(active.version_no if active else None),metrics=None,decision='训练排队中，完成后用同一批留出记录与发起时在用模型比较',note=d.get('note',''),activated=False,actor=actor,created_at=c.now)
 s.add(x);s.flush();return x


def retry_batch(s,id):
 lock(s);b=get(s,TrainingBatch,id)
 if b.activated:fail('该批次曾启用，重试不会重复启用')
 if b.state not in ('failed','running'):fail('只有训练失败或中断的批次可重试')
 b.state='queued';b.error='';b.metrics=None;b.completed_at=None;b.decision='已按原冻结记录重新排队；同一批次至多启用一次';s.flush();return b


def complete_batch(s,id):
 c=lock(s);b=s.get(TrainingBatch,id)
 if not b or b.state not in ('queued','running') or b.activated:return b
 b.state='running';s.flush()
 try:
  train=frozen_train(s,b)
  if len(train)<MIN_TRAIN_RECORDS:raise ValueError(f'剔除留出体型后仅{len(train)}条训练记录，至少需要{MIN_TRAIN_RECORDS}条')
  # 候选与完成时仍在用的模型使用同一批留出记录比较
  base_model,base_meta=incumbent(s)
  # 同一批留出记录评估：排除在用模型曾训练过的记录，避免评估被记忆污染
  evaluated=[r for r in b.holdout_records if r['label_id'] not in base_meta['train_label_ids']]
  if len(evaluated)<MIN_HOLDOUT_RECORDS:raise ValueError(f'可用于公平比较的留出记录仅{len(evaluated)}条（其余已被在用模型训练过），至少需要{MIN_HOLDOUT_RECORDS}条')
  if not any(r['label']==TIGHT_LABEL for r in evaluated):raise ValueError('可用于公平比较的留出记录缺少偏紧样本')
  model=fit_model.train(train);cand=fit_model.evaluate(model,evaluated);base=fit_model.evaluate(base_model,evaluated)
  b.metrics={'holdout_count':len(evaluated),'holdout_frozen':len(b.holdout_records),'train_count':len(train),'evaluated_on':'同一批留出记录（剔除在用模型已训练部分）','candidate':cand,'incumbent':{**base,'model':{k:v for k,v in base_meta.items() if k!='train_label_ids'}}}
  state=s.get(ModelState,1)
  if not state:state=ModelState(id=1,active_batch_id=None,updated_at=c.now);s.add(state);s.flush()
  active=s.get(TrainingBatch,state.active_batch_id) if state.active_batch_id else None
  newer=[x for x in rows(s,TrainingBatch) if x.id!=b.id and x.version_no>b.version_no and x.state=='ready']
  b.state='ready';b.completed_at=c.now
  tight_c=cand['per_class'][TIGHT_LABEL]['recall'];tight_i=base['per_class'][TIGHT_LABEL]['recall']
  if newer:
   top=max(newer,key=lambda x:x.version_no)
   b.activated=False;b.decision=f'未启用：更新批次 v{top.version_no} 已完成评估，旧批次 v{b.version_no} 晚完成不得覆盖较新版本'
  elif active and active.version_no>=b.version_no:
   b.activated=False;b.decision=f'未启用：本批 v{b.version_no} 晚于已在用的 v{active.version_no} 完成，旧批次不得覆盖较新版本'
  elif tight_c<tight_i:
   b.activated=False;b.decision=f'未启用：留出集偏紧召回下降（候选 {tight_c:.0%}，{base_meta["label"]} {tight_i:.0%}）'
  elif cand['accuracy']<=base['accuracy']:
   b.activated=False;b.decision=f'未启用：留出集总体准确率未提高（候选 {cand["accuracy"]:.0%}，{base_meta["label"]} {base["accuracy"]:.0%}）'
  else:
   b.activated=True;state.active_batch_id=b.id;state.updated_at=c.now
   b.decision=f'已启用：留出集偏紧召回 {tight_c:.0%} 不低于 {base_meta["label"]} 的 {tight_i:.0%}，总体准确率 {cand["accuracy"]:.0%} 高于 {base["accuracy"]:.0%}'
 except Exception as e:
  b.state='failed';b.error=str(e)[:500];b.decision=f'训练失败，未启用：{str(e)[:300]}';b.activated=False;logging.exception('training batch failed')
 s.flush();return b


def assess(s,d):
 lock(s);g=garment(s,d['garment_id']);get(s,FitProfile,d['profile_id'])
 if not any(x.garment_id==g.id for x in rows(s,Measurement)):fail('未实测服饰不得推荐尺码')
 if g.state not in ['listed','held']:fail('服饰不在可试穿状态')
 x=FitAssessment(**d);s.add(x);s.flush();return x

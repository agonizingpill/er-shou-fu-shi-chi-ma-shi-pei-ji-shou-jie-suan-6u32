from sqlalchemy import select
from src.database import Base,engine,Session
from src.models import *
from src.security import password_hash
from src.common import rows

SEED_ACTOR='seed@hesheng.local'
SEED_HOLDOUT={'P034':'店长留出：小胸围体型仅用于评估，不进入训练','P037':'店长留出：高胸围体型仅用于评估，不进入训练'}


def trial_label(chest,elasticity,profile):
 """演示用试穿松紧：按胸围余量与弹性模拟店员登记，仅用于演示，不代表真实舒适度。"""
 ease=chest-profile.chest
 return '偏紧' if ease<4 else ('偏松' if ease>12 or (ease>9 and elasticity<.25) else '合身')


def seed():
 Base.metadata.create_all(engine)
 with Session() as s:
  for r in ['operator','supervisor','viewer']:
   if not any(u.email==r+'@hesheng.local' for u in rows(s,User)):s.add(User(email=r+'@hesheng.local',password=password_hash('Fit2026!'),role=r))
  if not s.get(Shop,1):s.add(Shop(id=1,now='2026-11-18T09:00:00'))
  if not any(g.code=='G031' for g in rows(s,Garment)):
   c=Consignor(name='林禾',contact='柜台留存联系单LH031');s.add(c);s.flush();prof=FitProfile(code='P031',chest=88,shoulder=37);s.add(prof);s.flush()
   for code,ends in [('A031','2026-11-26T18:00:00'),('A032','2026-11-20T18:00:00'),('A033','2026-11-17T18:00:00')]:s.add(Agreement(code=code,consignor_id=c.id,floor_cents=18000,commission_bp=2000,ends=ends))
   s.flush();ags=rows(s,Agreement)
   for code,title,ag,state in [('G031','烟苔绿短外套',ags[0],'listed'),('G032','复检中的棉衬衣',ags[1],'inspection'),('G033','期满未售针织衫',ags[2],'listed'),('G034','赭色直筒外套',ags[0],'listed')]:
    g=Garment(code=code,title=title,agreement_id=ag.id,condition='八成新，领口轻微使用痕迹，无破损',price_cents=22000,state=state);s.add(g);s.flush();s.add(Measurement(garment_id=g.id,chest=96,shoulder=39,length=58,elasticity=.2,cut=1))
   s.flush();g=next(g for g in rows(s,Garment) if g.code=='G032');f=Fitting(garment_id=g.id,profile_id=prof.id,slot=1,starts='2026-11-16T15:00:00',ends='2026-11-16T16:00:00',state='completed');s.add(f);s.flush();sale=Sale(fitting_id=f.id,garment_id=g.id,price_cents=22000,commission_bp=2000,commission_cents=4400,net_cents=17600,sold_at='2026-11-16T15:10:00',return_deadline='2026-11-23T15:10:00',state='returned');s.add(sale);s.flush();s.add(Return(sale_id=sale.id,reason='买家反馈肩部偏紧，已按原样退回待复检'))

  # 演示试穿登记：自然键补缺，已有登记与更正保留
  demo_garments=[('G035','亚麻宽肩衬衫',92,39,.75,2),('G036','灰绿薄呢马甲',90,39,.55,1),('G037','深灰落肩大衣',103,37,.55,2),('G038','浅驼薄外套',103,42,.1,1)]
  demo_profiles=[('P032',97,35),('P033',86,40),('P034',83,35),('P035',86,36),('P036',97,40),('P037',99,36),('P038',90,35)]
  a031=next(a for a in rows(s,Agreement) if a.code=='A031')
  for code,title,chest,shoulder,elasticity,cut in demo_garments:
   if not any(g.code==code for g in rows(s,Garment)):
    g=Garment(code=code,title=title,agreement_id=a031.id,condition='八成新，领口轻微使用痕迹，无破损',price_cents=22000,state='listed');s.add(g);s.flush();s.add(Measurement(garment_id=g.id,chest=chest,shoulder=shoulder,length=58,elasticity=elasticity,cut=cut))
  for code,chest,shoulder in demo_profiles:
   if not any(p.code==code for p in rows(s,FitProfile)):s.add(FitProfile(code=code,chest=chest,shoulder=shoulder))
  s.flush()
  measurements={m.garment_id:m for m in rows(s,Measurement)};profiles=rows(s,FitProfile)
  for g in rows(s,Garment):
   if g.code not in ['G031','G034','G035','G036','G037','G038']:continue
   m=measurements.get(g.id)
   if not m:continue
   for p in profiles:
    if s.scalars(select(TryOnLabel).where(TryOnLabel.garment_id==g.id,TryOnLabel.profile_id==p.id)).first():continue
    s.add(TryOnLabel(garment_id=g.id,profile_id=p.id,label=trial_label(m.chest,m.elasticity,p),note='演示种子：模拟店员试穿登记',recorded_by=SEED_ACTOR,updated_at='2026-11-18T09:00:00'))
  for code,reason in SEED_HOLDOUT.items():
   p=next((x for x in profiles if x.code==code),None)
   if p and not s.scalars(select(HoldoutProfile).where(HoldoutProfile.profile_id==p.id)).first():s.add(HoldoutProfile(profile_id=p.id,reason=reason,marked_by=SEED_ACTOR,marked_at='2026-11-18T09:00:00'))
  s.commit()
if __name__=='__main__':seed()

import os,time,pathlib,logging
import redis
from .database import Session
from .models import FitAssessment,Measurement,FitProfile,TrainingBatch
from .common import lock,rows,get,dump
from .fit_model import predict,features_for
from . import fit_service

ASSESS_QUEUE='resale:fit';TRAIN_QUEUE='resale:train'
HEARTBEAT=pathlib.Path(os.getenv('WORKER_HEARTBEAT','/artifacts/resale-heartbeat'))


def connect():
 return redis.from_url(os.environ['REDIS_URL'],decode_responses=True,socket_connect_timeout=1,socket_timeout=4)


def run_assessment(s,id):
 lock(s);x=s.get(FitAssessment,id)
 if not x or x.state!='queued':return
 try:
  m=next(m for m in rows(s,Measurement) if m.garment_id==x.garment_id);p=get(s,FitProfile,x.profile_id)
  model,meta=fit_service.incumbent(s)
  result=predict(model,features_for(dump(m),dump(p)))
  result['model']={'label':meta['label'],'version':meta.get('version'),'batch_id':meta.get('batch_id')}
  result['note']='试穿登记训练的辅助建议，最终以实际试衣感受为准'
  x.result=result;x.state='ready'
 except Exception as e:x.state='failed';x.error=str(e)[:500];logging.exception('fit failed')
 s.commit()


def run_training(s,id):
 fit_service.complete_batch(s,id);s.commit()


def run_once(conn):
 item=conn.blpop([ASSESS_QUEUE,TRAIN_QUEUE],timeout=2)
 if not item:return
 with Session() as s:
  if item[0]==TRAIN_QUEUE:run_training(s,int(item[1]))
  else:run_assessment(s,int(item[1]))


def main():
 q=connect()
 while True:
  try:HEARTBEAT.write_text(str(time.time()))
  except OSError:logging.warning('heartbeat path not writable')
  try:run_once(q)
  except redis.RedisError:
   logging.warning('Redis interrupted, retrying');time.sleep(1)


if __name__=='__main__':main()

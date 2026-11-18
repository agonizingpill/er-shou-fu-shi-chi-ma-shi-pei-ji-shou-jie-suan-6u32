import hashlib,json
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import confusion_matrix

from .constants import FIT_LABELS

FEATURE_NAMES=('胸围余量','肩宽余量','弹性','版型')


def features_for(measurement,profile):
    return [measurement['chest']-profile['chest'],measurement['shoulder']-profile['shoulder'],measurement['elasticity'],measurement['cut']]


def _pipeline(n):
    return Pipeline([('scale',StandardScaler()),('knn',KNeighborsClassifier(n_neighbors=min(5,n),weights='distance'))])


def train(records):
    if not records:raise ValueError('没有可训练的试穿记录')
    X=np.array([r['features'] for r in records],dtype=float);y=np.array([r['label'] for r in records])
    model=_pipeline(len(y));model.fit(X,y);return model


def algorithm(model):
    k=model.named_steps['knn'].n_neighbors
    return f'StandardScaler+KNeighborsClassifier(k={k},distance)'


def predict(model,features):
    label=str(model.predict([features])[0]);probs=model.predict_proba([features])[0]
    return {'algorithm':algorithm(model),'label':label,'features':features,'probabilities':{str(k):round(float(v),6) for k,v in zip(model.classes_,probs)}}


def evaluate(model,records):
    if not records:return None
    X=np.array([r['features'] for r in records],dtype=float);y=np.array([r['label'] for r in records])
    pred=model.predict(X)
    per={}
    for cls in FIT_LABELS:
        support=int(np.sum(y==cls));tp=int(np.sum((y==cls)&(pred==cls)))
        per[cls]={'support':support,'recall':(tp/support if support else None),'correct':tp}
    recalls=[v['recall'] for v in per.values() if v['support']]
    return {'count':int(len(y)),'accuracy':float(np.mean(pred==y)),'balanced_accuracy':float(np.mean(recalls)) if recalls else None,'per_class':per,'confusion_matrix':confusion_matrix(y,pred,labels=list(FIT_LABELS)).tolist(),'labels':list(FIT_LABELS)}


def synthetic_dataset():
    rng=np.random.default_rng(42);X=[];y=[]
    for cls in FIT_LABELS:
        for i in range(100):
            elasticity=float(rng.uniform(0,1));cut=int(rng.integers(0,3))
            ease=float(rng.uniform(5,12) if cls=='合身' else rng.uniform(-5,1) if cls=='偏紧' else rng.uniform(17,26))
            shoulder=float(rng.uniform(.5,2.5) if cls=='合身' else rng.uniform(-3,-.5) if cls=='偏紧' else rng.uniform(4,7))
            X.append([ease,shoulder,elasticity,cut]);y.append(cls)
    ids=np.arange(len(y));tr,te=train_test_split(ids,test_size=.25,stratify=y,random_state=42)
    X=np.array(X);y=np.array(y)
    records=lambda idx:[{'features':[float(v) for v in X[i]],'label':str(y[i]),'synthetic':True} for i in idx]
    digest=hashlib.sha256(X.tobytes()+y.tobytes()).hexdigest()
    return records(tr),records(te),digest


def snapshot(records):
    return hashlib.sha256(json.dumps(records,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

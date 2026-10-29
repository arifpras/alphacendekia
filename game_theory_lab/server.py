"""Game Theory Lab — small-class experimental economics platform.
Run: pip install -r requirements.txt && python server.py
"""
import os, json, uuid, time, random, sqlite3, secrets, csv, io, math
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('GTL_DB', str(ROOT / 'game_lab.db')))
# No well-known default: without GTL_ADMIN_PASSWORD a random password is generated and printed at startup.
ADMIN_PASSWORD = os.environ.get('GTL_ADMIN_PASSWORD') or secrets.token_urlsafe(9)
MAX_STUDENTS = 20
GAME_NAMES = {'prisoner':'Prisoner’s Dilemma','beauty':'Keynesian Beauty Contest','public':'Public Goods','ultimatum':'Ultimatum','auction':'Sealed-Bid Auction','trading':'Market Trading','oligopoly':'Cournot Competition'}
app=FastAPI(title='Game Theory Lab')
app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')

@contextmanager
def db():
 c=sqlite3.connect(DB,timeout=15,isolation_level=None)
 c.row_factory=sqlite3.Row
 c.execute('PRAGMA journal_mode=WAL')
 try:
  yield c
  if c.in_transaction: c.commit()
 except BaseException:
  if c.in_transaction: c.rollback()
  raise
 finally: c.close()

def init():
 DB.parent.mkdir(parents=True,exist_ok=True)
 with db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,pin TEXT UNIQUE,created TEXT,round_num INTEGER DEFAULT 0);
  CREATE TABLE IF NOT EXISTS participants(id TEXT PRIMARY KEY,session_id TEXT,label TEXT,secret TEXT UNIQUE,joined TEXT,nickname TEXT,active INTEGER DEFAULT 1);
  CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY,session_id TEXT,num INTEGER,game TEXT,phase TEXT,config TEXT,layout TEXT,started TEXT,finished TEXT);
  CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,round_id TEXT,participant_id TEXT,phase TEXT,action TEXT,created TEXT, UNIQUE(round_id,participant_id,phase));
  CREATE TABLE IF NOT EXISTS outcomes(round_id TEXT,participant_id TEXT,points REAL,details TEXT,PRIMARY KEY(round_id,participant_id));''')
  # Databases created by earlier versions stored the nickname in `label` and had no nickname/active columns.
  cols={x['name'] for x in rows(c,'PRAGMA table_info(participants)')}
  if 'nickname' not in cols: c.execute('ALTER TABLE participants ADD COLUMN nickname TEXT')
  if 'active' not in cols: c.execute('ALTER TABLE participants ADD COLUMN active INTEGER DEFAULT 1')

def now(): return datetime.now(timezone.utc).isoformat()
def uid(): return uuid.uuid4().hex[:16]
def rows(c,q,args=()): return [dict(x) for x in c.execute(q,args).fetchall()]
def one(c,q,args=()):
 r=c.execute(q,args).fetchone();return dict(r) if r else None
init()

def password_ok(value):
 # Compare bytes: compare_digest raises TypeError on non-ASCII str.
 return bool(value) and secrets.compare_digest(value.encode(),ADMIN_PASSWORD.encode())

def auth(request):
 if not password_ok(request.headers.get('x-admin-password','')): raise HTTPException(401,'Incorrect instructor password')

def session(c,pin):
 s=one(c,'SELECT * FROM sessions WHERE pin=?',(pin,))
 if not s: raise HTTPException(404,'Session PIN not found')
 return s

def participant(c,secret,pin):
 p=one(c,'SELECT p.* FROM participants p JOIN sessions s ON s.id=p.session_id WHERE p.secret=? AND s.pin=? AND p.active=1',(secret,pin))
 if not p: raise HTTPException(401,'Join this session first')
 return p

def current(c,s): return one(c,'SELECT * FROM rounds WHERE session_id=? ORDER BY num DESC LIMIT 1',(s['id'],))

def active_ids(c,session_id): return {p['id'] for p in rows(c,'SELECT id FROM participants WHERE session_id=? AND active=1',(session_id,))}

def players(c,r):
 """Participants assigned to round r. Rounds stored by earlier versions have no list, so use who had joined by its start."""
 lay=json.loads(r['layout'])
 if 'players' in lay: return set(lay['players'])
 return {p['id'] for p in rows(c,'SELECT id FROM participants WHERE session_id=? AND joined<=?',(r['session_id'],r['started']))}

def layout_for(game, people):
 ids=[p['id'] for p in people];random.shuffle(ids)
 result={'players':list(ids),'roles':{},'groups':[],'pairs':[],'waiting':[],'values':{}}
 if game in ('prisoner','ultimatum'):
  if len(ids)%2: result['waiting']=[ids.pop()]
  result['pairs']=[ids[i:i+2] for i in range(0,len(ids),2)]
  if game=='ultimatum':
   for x,y in result['pairs']: result['roles'][x]='proposer';result['roles'][y]='responder'
 elif game=='public':
  # Groups of about four; n/4 rounded half up (2-5 -> 1 group, 6-9 -> 2, 10-13 -> 3, ...).
  n=len(ids); group_count=max(1,(n+2)//4); sizes=[n//group_count+(i<n%group_count) for i in range(group_count)];offset=0
  for k in sizes:result['groups'].append(ids[offset:offset+k]);offset+=k
 elif game=='auction':
  result['values']={pid:random.randint(40,100) for pid in ids}
 elif game=='trading':
  result['roles']={pid:('buyer' if i<len(ids)//2 else 'seller') for i,pid in enumerate(ids)}
  result['values']={pid:(random.randint(60,110) if result['roles'][pid]=='buyer' else random.randint(20,70)) for pid in ids}
 return result

def unit_of(lay,pid):
 """Pair or group number (1-based) that pid belongs to, or None."""
 for i,unit in enumerate(lay['pairs'] or lay['groups']):
  if pid in unit: return i+1
 return None

class Start(BaseModel):
 pin:str
 game:str
 config:dict=Field(default_factory=dict)
class Join(BaseModel):
 pin:str
 nickname:str=''
class Submit(BaseModel):
 pin:str
 secret:str
 action:dict

def sanitize_config(g,c):
 defaults={'prisoner':{'cc':3,'cd':0,'dc':5,'dd':1},'beauty':{'fraction':0.6666666667},'public':{'endowment':100,'multiplier':1.6},'ultimatum':{'endowment':100},'auction':{'format':'first'},'trading':{},'oligopoly':{'intercept':100,'slope':1,'cost':10,'max_quantity':50}}
 out=defaults[g].copy()
 for k,v in c.items():
  if k in out:
   if isinstance(out[k],(int,float)):
    try: v=float(v)
    except (TypeError,ValueError):continue
    if not math.isfinite(v): continue
    if k in ('endowment','multiplier','fraction','intercept','slope','max_quantity') and v<=0:continue
    if k=='cost' and v<0:continue
    out[k]=v
   elif k=='format' and v in ('first','second'):out[k]=v
 return out

@app.get('/',response_class=HTMLResponse)
def home(): return (ROOT/'static'/'index.html').read_text()

@app.post('/api/session')
def create(request:Request):
 auth(request)
 with db() as c:
  pin=str(random.randint(100000,999999))
  while one(c,'SELECT id FROM sessions WHERE pin=?',(pin,)):pin=str(random.randint(100000,999999))
  sid=uid(); c.execute('INSERT INTO sessions(id,pin,created) VALUES(?,?,?)',(sid,pin,now()))
  return {'pin':pin}

@app.post('/api/join')
def join(x:Join):
 with db() as c:
  # Lock so the class-size check and label numbering can't race with another join.
  c.execute('BEGIN IMMEDIATE')
  s=session(c,x.pin)
  if len(active_ids(c,s['id']))>=MAX_STUDENTS:raise HTTPException(409,f'Class is full ({MAX_STUDENTS} students)')
  nickname=x.nickname.strip()[:30] or 'Student'
  # Number over everyone who ever joined, so a removed student's label is never reused.
  label='Participant '+str(one(c,'SELECT count(*) n FROM participants WHERE session_id=?',(s['id'],))['n']+1).zfill(2)
  pid=uid();secret=secrets.token_urlsafe(24)
  c.execute('INSERT INTO participants(id,session_id,label,secret,joined,nickname,active) VALUES(?,?,?,?,?,?,1)',(pid,s['id'],label,secret,now(),nickname))
  return {'secret':secret,'label':label}

@app.post('/api/remove')
def remove(request:Request,pin:str,participant_id:str):
 auth(request)
 with db() as c:
  c.execute('BEGIN IMMEDIATE');s=session(c,pin)
  n=c.execute('UPDATE participants SET active=0 WHERE id=? AND session_id=?',(participant_id,s['id'])).rowcount
  if not n:raise HTTPException(404,'Participant not found')
  return {'ok':True}

@app.post('/api/start')
def start(x:Start,request:Request):
 auth(request)
 if x.game not in GAME_NAMES:raise HTTPException(400,'Unknown game')
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  s=session(c,x.pin); prev=current(c,s)
  if prev and prev['phase']!='finished':raise HTTPException(409,'Finish the current round first')
  people=rows(c,'SELECT id FROM participants WHERE session_id=? AND active=1',(s['id'],))
  if len(people)<2:raise HTTPException(400,'At least two students are needed')
  config=sanitize_config(x.game,x.config)
  layout=layout_for(x.game,people)
  num=s['round_num']+1;rid=uid()
  c.execute('INSERT INTO rounds VALUES(?,?,?,?,?,?,?,?,?)',(rid,s['id'],num,x.game,'propose' if x.game=='ultimatum' else 'choose',json.dumps(config),json.dumps(layout),now(),None))
  c.execute('UPDATE sessions SET round_num=? WHERE id=?',(num,s['id']))
  return {'round':num,'game':x.game}

def validate_action(c,r,pid,a):
 g=r['game'];phase=r['phase']; cfg=json.loads(r['config']);lay=json.loads(r['layout'])
 if pid not in players(c,r):raise HTTPException(400,'You joined after this round started; wait for the next round')
 if pid in lay['waiting']:raise HTTPException(400,'You are resting this round')
 if g=='ultimatum' and lay['roles'].get(pid)!=('proposer' if phase=='propose' else 'responder'):raise HTTPException(400,'Not your turn')
 def number(k,low,high,integer=False):
  v=a.get(k)
  if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<low or v>high or (integer and int(v)!=v):raise HTTPException(400,f'{k} must be between {low:g} and {high:g}')
  return int(v) if integer else float(v)
 if g=='prisoner':
  if a.get('choice') not in ('C','D'):raise HTTPException(400,'Choose C or D')
  return {'choice':a['choice']}
 if g=='beauty':return {'guess':number('guess',0,100,True)}
 if g=='public':return {'contribution':number('contribution',0,cfg['endowment'],True)}
 if g=='ultimatum':
  if phase=='propose':return {'offer':number('offer',0,cfg['endowment'],True)}
  if not isinstance(a.get('accept'),bool):raise HTTPException(400,'Choose accept or reject')
  return {'accept':a['accept']}
 if g=='auction':return {'bid':number('bid',0,200,True)}
 if g=='trading':return {'price':number('price',0,150,True)}
 if g=='oligopoly':return {'quantity':number('quantity',0,cfg['max_quantity'],True)}
 raise HTTPException(400,'Unknown game')

@app.post('/api/submit')
def submit(x:Submit):
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  s=session(c,x.pin);p=participant(c,x.secret,x.pin);r=current(c,s)
  if not r or r['phase']=='finished':raise HTTPException(409,'No active round')
  act=validate_action(c,r,p['id'],x.action)
  c.execute('INSERT INTO decisions(round_id,participant_id,phase,action,created) VALUES(?,?,?,?,?) ON CONFLICT(round_id,participant_id,phase) DO UPDATE SET action=excluded.action,created=excluded.created',(r['id'],p['id'],r['phase'],json.dumps(act),now()))
  return {'ok':True}

def get_choices(c,r,phase=None):
 return {d['participant_id']:json.loads(d['action']) for d in rows(c,'SELECT * FROM decisions WHERE round_id=? AND phase=?',(r['id'],phase or r['phase']))}

def stats(c,r):
 lay=json.loads(r['layout'])
 required=players(c,r)-set(lay['waiting'])
 if r['game']=='ultimatum':required={p for p,role in lay['roles'].items() if role==('proposer' if r['phase']=='propose' else 'responder')}
 required&=active_ids(c,r['session_id'])  # removed students are no longer waited for
 choices=get_choices(c,r)
 return {'submitted':len(required & set(choices)),'expected':len(required),'all_submitted':required<=set(choices)}

@app.post('/api/advance')
def advance(request:Request,pin:str):
 auth(request)
 with db() as c:
  c.execute('BEGIN IMMEDIATE');s=session(c,pin);r=current(c,s)
  if not r or r['phase']=='finished':raise HTTPException(409,'No active round')
  if r['game']=='ultimatum' and r['phase']=='propose':
   c.execute("UPDATE rounds SET phase='responder' WHERE id=?",(r['id'],))
   return {'phase':'responder'}
  finish_round(c,r)
  return {'phase':'finished'}

def finish_round(c,r):
 game=r['game'];cfg=json.loads(r['config']);lay=json.loads(r['layout'])
 # Only decisions from assigned players count (guards rounds stored before submissions were checked).
 inround=players(c,r);d={p:v for p,v in get_choices(c,r).items() if p in inround}
 result={}
 def add(pid,points,detail):result[pid]={'points':round(float(points),3),'detail':detail}
 for pid in lay['waiting']:add(pid,0,{'resting':True})
 if game=='prisoner':
  for pair in lay['pairs']:
   a,b=pair
   if a not in d or b not in d:continue
   x,y=d[a]['choice'],d[b]['choice'];key=(x+y).lower();pay={'cc':(cfg['cc'],cfg['cc']),'cd':(cfg['cd'],cfg['dc']),'dc':(cfg['dc'],cfg['cd']),'dd':(cfg['dd'],cfg['dd'])}[key]
   add(a,pay[0],{'your_choice':x,'partner_choice':y});add(b,pay[1],{'your_choice':y,'partner_choice':x})
 elif game=='beauty':
  if d:
   avg=sum(v['guess'] for v in d.values())/len(d);target=cfg['fraction']*avg;distance={p:abs(v['guess']-target) for p,v in d.items()};best=min(distance.values())
   for pid,v in d.items():add(pid,100 if abs(distance[pid]-best)<1e-8 else 0,{'guess':v['guess'],'average':round(avg,3),'target':round(target,3),'distance':round(distance[pid],3),'winner':abs(distance[pid]-best)<1e-8})
 elif game=='public':
  for grp in lay['groups']:
   submitted=[p for p in grp if p in d]
   if not submitted:continue
   total=sum(d[p]['contribution'] for p in submitted);share=cfg['multiplier']*total/len(grp)
   for pid in submitted:add(pid,cfg['endowment']-d[pid]['contribution']+share,{'contribution':d[pid]['contribution'],'group_total':total,'group_size':len(grp),'return_from_pool':round(share,2)})
 elif game=='ultimatum':
  offers=get_choices(c,r,'propose')
  for a,b in lay['pairs']:
   if a not in offers or b not in d:continue
   offer=offers[a]['offer'];accepted=d[b]['accept']
   add(a,cfg['endowment']-offer if accepted else 0,{'role':'proposer','offer':offer,'accepted':accepted})
   add(b,offer if accepted else 0,{'role':'responder','offer':offer,'accepted':accepted})
 elif game=='auction':
  bidders=[(p,v['bid']) for p,v in d.items()];bidders.sort(key=lambda z:z[1],reverse=True)
  if bidders:
   top=bidders[0][1];winner=random.choice([p for p,b in bidders if b==top]);second=bidders[1][1] if len(bidders)>1 else 0
   price=top if cfg['format']=='first' else second
   for pid,bid in bidders:add(pid,(lay['values'][pid]-price) if pid==winner else 0,{'bid':bid,'valuation':lay['values'][pid],'winner':pid==winner,'price':price if pid==winner else None,'format':cfg['format']})
 elif game=='trading':
  buyers=sorted([(p,v['price']) for p,v in d.items() if lay['roles'][p]=='buyer'],key=lambda z:-z[1]);sellers=sorted([(p,v['price']) for p,v in d.items() if lay['roles'][p]=='seller'],key=lambda z:z[1]);matched={}
  while buyers and sellers and buyers[0][1]>=sellers[0][1]:
   buyer,bp=buyers.pop(0);seller,sp=sellers.pop(0);price=(bp+sp)/2
   matched[buyer]={'price':price,'surplus':lay['values'][buyer]-price};matched[seller]={'price':price,'surplus':price-lay['values'][seller]}
  for pid,act in d.items():
   m=matched.get(pid);add(pid,m['surplus'] if m else 0,{'role':lay['roles'][pid],'private_value_or_cost':lay['values'][pid],'limit_price':act['price'],'traded':bool(m),'transaction_price':m['price'] if m else None})
 elif game=='oligopoly':
  total=sum(v['quantity'] for v in d.values());price=max(0,cfg['intercept']-cfg['slope']*total)
  for pid,v in d.items():add(pid,(price-cfg['cost'])*v['quantity'],{'quantity':v['quantity'],'market_quantity':total,'market_price':price,'marginal_cost':cfg['cost']})
 for pid,o in result.items():c.execute('INSERT INTO outcomes VALUES(?,?,?,?)',(r['id'],pid,o['points'],json.dumps(o['detail'])))
 c.execute("UPDATE rounds SET phase='finished',finished=? WHERE id=?",(now(),r['id']))

@app.get('/api/state')
def state(request:Request,pin:str,secret:str=''):
 with db() as c:
  s=session(c,pin);is_admin=password_ok(request.headers.get('x-admin-password',''))
  p=None if is_admin else (participant(c,secret,pin) if secret else None)
  if not is_admin and not p:raise HTTPException(401,'Authentication required')
  people=rows(c,'SELECT id,label,nickname FROM participants WHERE session_id=? AND active=1 ORDER BY joined,id',(s['id'],))
  data={'pin':pin,'participants':len(people),'max_participants':MAX_STUDENTS,'games':GAME_NAMES}
  if is_admin:data['participants_private']=people
  r=current(c,s)
  if not r:return {**data,'round':None}
  layout=json.loads(r['layout']);cfg=json.loads(r['config']);st=stats(c,r)
  info={'number':r['num'],'game':r['game'],'name':GAME_NAMES[r['game']],'phase':r['phase'],'config':cfg,'submitted':st['submitted'],'expected':st['expected']}
  if is_admin:
   if r['phase']=='finished':
    # Include removed students too, so a finished round's table stays complete.
    everyone={x['id']:x for x in rows(c,'SELECT id,label,nickname FROM participants WHERE session_id=?',(s['id'],))}
    outcomes=rows(c,'SELECT * FROM outcomes WHERE round_id=?',(r['id'],))
    info['results']=[{'participant':everyone.get(z['participant_id'],{}).get('label','Unknown'),'nickname':everyone.get(z['participant_id'],{}).get('nickname'),'points':z['points'],'detail':json.loads(z['details'])} for z in outcomes]
   info['waiting_count']=len(layout['waiting'])
  else:
   pid=p['id']; info['in_round']=pid in players(c,r)
   info['resting']=pid in layout['waiting'];info['role']=layout['roles'].get(pid)
   info['private_value']=layout['values'].get(pid)
   info['submitted_by_me']=pid in get_choices(c,r)
   if r['game']=='ultimatum' and r['phase']=='responder' and info['role']=='responder':
    proposals=get_choices(c,r,'propose')
    for pair in layout['pairs']:
     if pid in pair:info['offer_received']=proposals.get(pair[0],{}).get('offer');break
   if r['phase']=='finished':
    o=one(c,'SELECT * FROM outcomes WHERE round_id=? AND participant_id=?',(r['id'],pid))
    info['outcome']={'points':o['points'],'detail':json.loads(o['details'])} if o else None
  data['round']=info
  return data

@app.get('/api/history')
def history(request:Request,pin:str):
 auth(request)
 with db() as c:
  s=session(c,pin);return {'rounds':rows(c,'SELECT num,game,phase,started,finished FROM rounds WHERE session_id=? ORDER BY num',(s['id'],))}

@app.get('/api/export')
def export(request:Request,pin:str):
 auth(request)
 with db() as c:
  s=session(c,pin)
  f=io.StringIO();writer=csv.writer(f);writer.writerow(['round','game','participant_id','phase','decision_json','payoff','outcome_json','config_json','timestamp','role','pair_or_group','private_value'])
  for r in rows(c,'SELECT * FROM rounds WHERE session_id=? ORDER BY num',(s['id'],)):
   lay=json.loads(r['layout'])
   outcomes={o['participant_id']:o for o in rows(c,'SELECT * FROM outcomes WHERE round_id=?',(r['id'],))}
   decided=set()
   def write(pid,d):
    o=outcomes.get(pid,{})
    role='resting' if pid in lay['waiting'] else lay['roles'].get(pid,'')
    writer.writerow([r['num'],r['game'],pid,d['phase'] if d else '',d['action'] if d else '',o.get('points'),o.get('details'),r['config'],d['created'] if d else '',role,unit_of(lay,pid) or '',lay['values'].get(pid,'')])
   for d in rows(c,'SELECT * FROM decisions WHERE round_id=? ORDER BY participant_id,phase',(r['id'],)):
    write(d['participant_id'],d);decided.add(d['participant_id'])
   # Resting players have an outcome but no decision; keep them so the round's assignment is complete.
   for pid in sorted(set(outcomes)-decided):write(pid,None)
  f.seek(0)
  return StreamingResponse(iter([f.getvalue()]),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="game-theory-{pin}.csv"'})

if __name__=='__main__':
 import uvicorn
 port=int(os.environ.get('PORT',8000))
 if os.environ.get('GTL_ADMIN_PASSWORD'):print(f'Instructor: http://127.0.0.1:{port} (password from GTL_ADMIN_PASSWORD)')
 else:print(f'Instructor: http://127.0.0.1:{port} | GTL_ADMIN_PASSWORD not set; generated password for this run:',ADMIN_PASSWORD)
 uvicorn.run(app,host=os.environ.get('HOST','0.0.0.0'),port=port)

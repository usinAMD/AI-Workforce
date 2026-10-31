from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import sqlite3,json,os,uuid,threading,urllib.request,urllib.error,ctypes,base64
D=Path(os.environ.get('AI_WORKFORCE_DATA',Path.home()/'.ai-workforce'));D.mkdir(parents=True,exist_ok=True);DB=D/'workforce.sqlite3';ROOT=Path(__file__).parent
P={'ollama':('Ollama','http://localhost:11434/v1','llama3.2',False),'gemini':('Gemini','https://generativelanguage.googleapis.com/v1beta','gemini-2.0-flash',False),'openrouter':('OpenRouter','https://openrouter.ai/api/v1','openrouter/free',False),'openai':('OpenAI','https://api.openai.com/v1','gpt-4o-mini',True),'anthropic':('Anthropic','https://api.anthropic.com/v1','claude-3-5-haiku-latest',True),'custom':('Compatible API','','',False)}
def secret_encode(value):
 if not value:return ''
 if os.name!='nt':raise RuntimeError('API key storage requires Windows DPAPI in this build.')
 class BLOB(ctypes.Structure):_fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
 raw=value.encode('utf-8');buf=ctypes.create_string_buffer(raw);inp=BLOB(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=BLOB()
 if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out)):raise OSError('Windows could not encrypt the API key.')
 try:return 'dpapi:'+base64.b64encode(ctypes.string_at(out.data,out.size)).decode('ascii')
 finally:ctypes.windll.kernel32.LocalFree(out.data)
def secret_decode(value):
 if not value:return ''
 if not value.startswith('dpapi:'):raise RuntimeError('Stored API key is not protected; disconnect and reconnect the provider.')
 if os.name!='nt':raise RuntimeError('This API key can only be decrypted on Windows.')
 class BLOB(ctypes.Structure):_fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
 raw=base64.b64decode(value[6:]);buf=ctypes.create_string_buffer(raw);inp=BLOB(len(raw),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=BLOB()
 if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out)):raise OSError('Windows could not decrypt the API key for this account.')
 try:return ctypes.string_at(out.data,out.size).decode('utf-8')
 finally:ctypes.windll.kernel32.LocalFree(out.data)
def db():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;return c
def init():
 with db() as c:
  c.executescript("""PRAGMA journal_mode=WAL;
  CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY,model TEXT,base TEXT,key TEXT,enabled INT DEFAULT 0,paid INT DEFAULT 0,priority INT DEFAULT 10,context_window INT DEFAULT 8192);
  CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,prompt TEXT,status TEXT,plan TEXT,output TEXT DEFAULT '',provider TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,error TEXT DEFAULT '',attempts INT DEFAULT 0,paused INT DEFAULT 0,chat_id TEXT,preferred_provider TEXT);
  CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,task TEXT,kind TEXT,message TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
  CREATE TABLE IF NOT EXISTS token_usage(id INTEGER PRIMARY KEY AUTOINCREMENT,provider TEXT,model TEXT,input_tokens INT,output_tokens INT,context_tokens INT,source TEXT,task TEXT,context_window INT,created TEXT DEFAULT CURRENT_TIMESTAMP);
  CREATE TABLE IF NOT EXISTS chats(id TEXT PRIMARY KEY,title TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,updated TEXT DEFAULT CURRENT_TIMESTAMP,preferred_provider TEXT);
  CREATE TABLE IF NOT EXISTS chat_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,chat_id TEXT NOT NULL,task_id TEXT,role TEXT NOT NULL,content TEXT NOT NULL,created TEXT DEFAULT CURRENT_TIMESTAMP);
  INSERT OR IGNORE INTO settings VALUES('stop','false');
  UPDATE settings SET value='false' WHERE key='stop';
  UPDATE tasks SET status='failed',error='Interrupted by server restart; retry this task to resume.' WHERE status='running';""")
  task_cols={r[1] for r in c.execute("PRAGMA table_info(tasks)")}
  if 'chat_id' not in task_cols:c.execute("ALTER TABLE tasks ADD COLUMN chat_id TEXT")
  if 'preferred_provider' not in task_cols:c.execute("ALTER TABLE tasks ADD COLUMN preferred_provider TEXT")
  chat_cols={r[1] for r in c.execute("PRAGMA table_info(chats)")}
  if 'preferred_provider' not in chat_cols:c.execute("ALTER TABLE chats ADD COLUMN preferred_provider TEXT")
  provider_cols={r[1] for r in c.execute("PRAGMA table_info(providers)")}
  if 'context_window' not in provider_cols:c.execute("ALTER TABLE providers ADD COLUMN context_window INT DEFAULT 8192")
  for k,(n,b,m,p) in P.items():c.execute('INSERT OR IGNORE INTO providers(id,model,base,paid) VALUES(?,?,?,?)',(k,m,b,int(p)))

def log(t,k,m):
 with db() as c:c.execute('INSERT INTO events(task,kind,message) VALUES(?,?,?)',(t,k,str(m)[:1000]))
def category(s):
 x=s.lower()
 for words,name in [(('code','website','app','build'),'Development'),(('research','sources'),'Research'),(('analyze','data','csv'),'Analysis'),(('write','draft'),'Writing')]:
  if any(w in x for w in words):return name
 return 'Reasoning'
def estimate_tokens(text):
 # Text-only rough estimate; provider tokenization can differ.
 return max(1,(len(text)+3)//4) if text else 0
def call(p,s):
 system="You are an AI Workforce agent. Complete the task carefully; state uncertainty."
 if p['id']=='gemini':
  u=p['base'].rstrip('/')+'/models/'+p['model']+':generateContent?key='+secret_decode(p['key'])
  body={'contents':[{'parts':[{'text':s}]}]};h={'Content-Type':'application/json'}
 else:
  u=p['base'].rstrip('/')+'/chat/completions'
  body={'model':p['model'],'messages':[{'role':'system','content':system},{'role':'user','content':s}]}
  h={'Content-Type':'application/json'};h.update({'Authorization':'Bearer '+secret_decode(p['key'])} if p['key'] else {})
 try:
  r=urllib.request.urlopen(urllib.request.Request(u,data=json.dumps(body).encode(),headers=h),timeout=90);d=json.loads(r.read())
  if p['id']=='gemini':
   answer=d['candidates'][0]['content']['parts'][0]['text'];usage=d.get('usageMetadata',{})
   ip=usage.get('promptTokenCount');op=usage.get('candidatesTokenCount')
  else:
   answer=d['choices'][0]['message']['content'];usage=d.get('usage') or {}
   ip=usage.get('prompt_tokens',usage.get('input_tokens'));op=usage.get('completion_tokens',usage.get('output_tokens'))
   if ip is None:ip=usage.get('prompt_eval_count')
   if op is None:op=usage.get('eval_count')
  source='provider' if ip is not None and op is not None else 'estimated'
  ip=int(ip) if ip is not None else estimate_tokens(system+s)
  op=int(op) if op is not None else estimate_tokens(answer)
  return answer,ip,op,source
 except urllib.error.HTTPError as e:raise RuntimeError(('429 ' if e.code==429 else 'server error ' if e.code>=500 else '')+'HTTP '+str(e.code))

def chat_prompt(messages):
 return "\n\n".join(("User" if m['role']=='user' else "Assistant")+": "+m['content'] for m in messages)
def run(t):
 with db() as c:
  q=c.execute('SELECT * FROM tasks WHERE id=?',(t,)).fetchone()
  if not q:return
  ps=(c.execute('SELECT * FROM providers WHERE id=? AND enabled=1',(q['preferred_provider'],)).fetchall() if q['preferred_provider'] else c.execute("SELECT * FROM providers WHERE enabled=1 AND paid=0 AND (id!='openrouter' OR model='openrouter/free' OR model LIKE '%:free') ORDER BY priority").fetchall())
 if not ps:
  with db() as c:c.execute("UPDATE tasks SET status='waiting',error='Connect a free provider.' WHERE id=?",(t,))
  log(t,'notice','Task saved. Connect a free model to run it.');return
 prompt=q['prompt']
 if q['chat_id']:
  with db() as c:history=c.execute("SELECT role,content FROM chat_messages WHERE chat_id=? ORDER BY id",(q['chat_id'],)).fetchall()
  prompt=chat_prompt(history)
 for i,p in enumerate(ps[:3],1):
  with db() as c:
   if c.execute("SELECT value FROM settings WHERE key='stop'").fetchone()[0]=='true' or c.execute('SELECT paused FROM tasks WHERE id=?',(t,)).fetchone()[0]:c.execute("UPDATE tasks SET status='paused' WHERE id=?",(t,));return
   c.execute('UPDATE tasks SET status=?,provider=?,attempts=? WHERE id=?',('running',p['id'],i,t))
  log(t,'activity','Agent using '+P[p['id']][0]+' / '+p['model'])
  try:
   answer,inp,out,source=call(p,prompt);context=inp+out
   with db() as c:
    c.execute("INSERT INTO token_usage(provider,model,input_tokens,output_tokens,context_tokens,source,task,context_window) VALUES(?,?,?,?,?,?,?,?)",(p['id'],p['model'],inp,out,context,source,t,p['context_window']))
    c.execute("UPDATE tasks SET status='completed',output=?,error='' WHERE id=?",(answer,t))
    if q['chat_id']:
     c.execute("INSERT INTO chat_messages(chat_id,task_id,role,content) VALUES(?,?,?,?)",(q['chat_id'],t,'assistant',answer))
     c.execute("UPDATE chats SET updated=CURRENT_TIMESTAMP WHERE id=?",(q['chat_id'],))
   log(t,'complete','Response saved · '+str(context)+' tokens '+('reported by provider' if source=='provider' else 'estimated'));return
  except Exception as e:log(t,'handoff',str(e)+'; trying next free provider.')
 with db() as c:c.execute("UPDATE tasks SET status='failed',error='Eligible providers failed.' WHERE id=?",(t,))

def plan(s):return [{'role':'Planner','task':'Scope '+category(s)+' work'},{'role':'Developer' if category(s)=='Development' else 'Researcher','task':'Complete assignment'},{'role':'Reviewer','task':'Check output'}]
class H(BaseHTTPRequestHandler):
 def log_message(self,*x):pass
 def send(self,n,o):
  b=o if isinstance(o,bytes) else json.dumps(o).encode();self.send_response(n);self.send_header('Content-Type','application/json' if not isinstance(o,bytes) else 'text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def data(self):
  n=int(self.headers.get('Content-Length',0));return json.loads(self.rfile.read(min(n,1000000)) or b'{}')
 def do_GET(self):
  x=self.path.split('?')[0]
  if x=='/':return self.send(200,(ROOT/'index.html').read_bytes())
  if x=='/api/providers':
   with db() as c:r=c.execute('SELECT * FROM providers').fetchall()
   return self.send(200,[{'id':z['id'],'name':P[z['id']][0],'model':z['model'],'base':z['base'],'connected':bool(z['enabled']),'has_key':bool(z['key']),'paid':bool(z['paid']),'context_window':z['context_window']} for z in r])
  if x=='/api/usage':
   with db() as c:
    providers=c.execute('SELECT id,model,context_window FROM providers ORDER BY priority').fetchall();items=[]
    for p in providers:
     totals=c.execute('SELECT COALESCE(SUM(input_tokens),0) AS inp,COALESCE(SUM(output_tokens),0) AS out,COUNT(*) AS calls FROM token_usage WHERE provider=? AND model=?',(p['id'],p['model'])).fetchone()
     last=c.execute('SELECT input_tokens,output_tokens,context_tokens,source,created FROM token_usage WHERE provider=? AND model=? ORDER BY id DESC LIMIT 1',(p['id'],p['model'])).fetchone()
     limit=max(1024,int(p['context_window'] or 8192));used=int(last['context_tokens']) if last else 0
     items.append({'provider':p['id'],'model':p['model'],'context_window':limit,'input_total':totals['inp'],'output_total':totals['out'],'calls':totals['calls'],'last_input':last['input_tokens'] if last else 0,'last_output':last['output_tokens'] if last else 0,'last_context':used,'last_remaining':max(0,limit-used),'source':last['source'] if last else None,'last_call':last['created'] if last else None})
   return self.send(200,items)
  if x=='/api/chats':
   with db() as c:r=c.execute("SELECT h.*,(SELECT content FROM chat_messages m WHERE m.chat_id=h.id ORDER BY m.id DESC LIMIT 1) AS last_message,(SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id=h.id) AS message_count FROM chats h ORDER BY h.updated DESC").fetchall()
   return self.send(200,[dict(z) for z in r])
  if x.startswith('/api/chats/'):
   parts=x.strip('/').split('/')
   if len(parts)==4 and parts[3]=='messages':
    cid=parts[2]
    with db() as c:
     if not c.execute('SELECT id FROM chats WHERE id=?',(cid,)).fetchone():return self.send(404,{'error':'Chat not found'})
     r=c.execute('SELECT m.*,t.status AS task_status,t.error AS task_error FROM chat_messages m LEFT JOIN tasks t ON t.id=m.task_id WHERE m.chat_id=? ORDER BY m.id',(cid,)).fetchall()
    return self.send(200,[dict(z) for z in r])
  if x=='/api/tasks':
   with db() as c:r=c.execute('SELECT * FROM tasks ORDER BY created DESC').fetchall()
   return self.send(200,[dict(z) for z in r])
  if x=='/api/events':
   t=self.path.split('task=')[-1] if 'task=' in self.path else ''
   with db() as c:r=c.execute('SELECT * FROM events '+('WHERE task=?' if t else '')+' ORDER BY id DESC LIMIT 80',((t,) if t else ())).fetchall()
   return self.send(200,[dict(z) for z in r[::-1]])
  if x=='/api/approvals':return self.send(200,[])
  return self.send(404,{'error':'Not found'})
 def do_POST(self):
  try:d=self.data()
  except:return self.send(400,{'error':'Invalid request'})
  x=self.path.split('?')[0]
  if x=='/api/providers':
   k=d.get('id')
   if k not in P:return self.send(400,{'error':'Unknown provider'})
   n,b,m,paid=P[k];b=str(d.get('base',b));m=str(d.get('model',m));secret=secret_encode(str(d.get('key','')));context_window=int(d.get('context_window',8192))
   if context_window<1024 or context_window>2000000:return self.send(400,{'error':'Context window must be 1,024–2,000,000 tokens.'})
   if paid and not d.get('authorize_paid'):return self.send(402,{'error':'Explicitly authorize paid use first.'})
   if k=='custom' and not (b.startswith('https://') or 'localhost' in b or '127.0.0.1' in b):return self.send(400,{'error':'Use HTTPS or a local URL.'})
   with db() as c:c.execute("UPDATE providers SET model=?,base=?,key=CASE WHEN ?='' THEN key ELSE ? END,context_window=?,enabled=1 WHERE id=?",(m,b,secret,secret,context_window,k))
   return self.send(200,{'ok':True})
  if x=='/api/chats':
   cid=uuid.uuid4().hex
   title=str(d.get('title','New conversation')).strip()[:80] or 'New conversation'
   with db() as c:c.execute('INSERT INTO chats(id,title) VALUES(?,?)',(cid,title))
   return self.send(201,{'id':cid,'title':title})
  if x.startswith('/api/chats/') and x.endswith('/model'):
   cid=x.strip('/').split('/')[2];preferred=d.get('provider') or None
   if preferred is not None and preferred not in P:return self.send(400,{'error':'Unknown model provider.'})
   with db() as c:
    if not c.execute('SELECT id FROM chats WHERE id=?',(cid,)).fetchone():return self.send(404,{'error':'Chat not found'})
    if preferred and not c.execute('SELECT id FROM providers WHERE id=? AND enabled=1',(preferred,)).fetchone():return self.send(409,{'error':'Connect this provider before selecting it.'})
    c.execute('UPDATE chats SET preferred_provider=?,updated=CURRENT_TIMESTAMP WHERE id=?',(preferred,cid))
   return self.send(200,{'ok':True,'provider':preferred})
  if x.startswith('/api/chats/') and x.endswith('/messages'):
   cid=x.strip('/').split('/')[2];prompt=str(d.get('prompt','')).strip();preferred=d.get('provider') or None
   if preferred is not None and preferred not in P:return self.send(400,{'error':'Unknown model provider.'})
   if not prompt or len(prompt)>200000:return self.send(400,{'error':'Enter a message under 200,000 characters.'})
   with db() as c:
    if not c.execute('SELECT id FROM chats WHERE id=?',(cid,)).fetchone():return self.send(404,{'error':'Chat not found'})
    if preferred and not c.execute('SELECT id FROM providers WHERE id=? AND enabled=1',(preferred,)).fetchone():return self.send(409,{'error':'Connect this provider before selecting it.'})
    tid=uuid.uuid4().hex
    c.execute("INSERT INTO tasks(id,prompt,status,plan,chat_id,preferred_provider) VALUES(?,?,'queued',?,?,?)",(tid,prompt,json.dumps(plan(prompt)),cid,preferred))
    c.execute("INSERT INTO chat_messages(chat_id,task_id,role,content) VALUES(?,?,?,?)",(cid,tid,'user',prompt))
    c.execute("UPDATE chats SET title=CASE WHEN title='New conversation' THEN ? ELSE title END,updated=CURRENT_TIMESTAMP WHERE id=?",(prompt[:64],cid))
   log(tid,'created','Chat message saved · '+category(prompt));threading.Thread(target=run,args=(tid,),daemon=True).start()
   return self.send(201,{'id':tid,'status':'queued'})
  if x=='/api/tasks':
   s=str(d.get('prompt','')).strip()
   if not s or len(s)>20000:return self.send(400,{'error':'Enter a task under 20,000 characters.'})
   t=uuid.uuid4().hex
   with db() as c:c.execute("INSERT INTO tasks(id,prompt,status,plan) VALUES(?,?,'queued',?)",(t,s,json.dumps(plan(s))))
   log(t,'created','Task saved · '+category(s));threading.Thread(target=run,args=(t,),daemon=True).start();return self.send(201,{'id':t})
  if x.endswith('/action'):
   t=x.split('/')[-2];a=d.get('action')
   with db() as c:c.execute("UPDATE tasks SET paused=?,status=? WHERE id=?",(1 if a=='pause' else 0,'paused' if a=='pause' else 'queued',t))
   log(t,'checkpoint','Task '+str(a))
   if a!='pause':threading.Thread(target=run,args=(t,),daemon=True).start()
   return self.send(200,{'ok':True})
  if x=='/api/emergency-stop':
   with db() as c:c.execute("UPDATE settings SET value='true' WHERE key='stop'")
   return self.send(200,{'ok':True})
  return self.send(404,{'error':'Not found'})
 def do_DELETE(self):
  k=self.path.rsplit('/',1)[-1]
  with db() as c:c.execute('UPDATE providers SET enabled=0,key=NULL WHERE id=?',(k,))
  return self.send(200,{'ok':True})
if __name__=='__main__':
 init();print('AI Workforce at http://127.0.0.1:'+str(os.environ.get('PORT','8765')));ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),int(os.environ.get('PORT','8765'))),H).serve_forever()

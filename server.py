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
  c.executescript('''PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY,model TEXT,base TEXT,key TEXT,enabled INT DEFAULT 0,paid INT DEFAULT 0,priority INT DEFAULT 10); CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,prompt TEXT,status TEXT,plan TEXT,output TEXT DEFAULT '',provider TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,error TEXT DEFAULT '',attempts INT DEFAULT 0,paused INT DEFAULT 0); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,task TEXT,kind TEXT,message TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT); INSERT OR IGNORE INTO settings VALUES('stop','false'); UPDATE settings SET value='false' WHERE key='stop'; UPDATE tasks SET status='failed',error='Interrupted by server restart; retry this task to resume.' WHERE status='running';''')
  for k,(n,b,m,p) in P.items():c.execute('INSERT OR IGNORE INTO providers(id,model,base,paid) VALUES(?,?,?,?)',(k,m,b,int(p)))
def log(t,k,m):
 with db() as c:c.execute('INSERT INTO events(task,kind,message) VALUES(?,?,?)',(t,k,str(m)[:1000]))
def category(s):
 x=s.lower()
 for words,name in [(('code','website','app','build'),'Development'),(('research','sources'),'Research'),(('analyze','data','csv'),'Analysis'),(('write','draft'),'Writing')]:
  if any(w in x for w in words):return name
 return 'Reasoning'
def call(p,s):
 if p['id']=='gemini':u=p['base']+'/models/'+p['model']+':generateContent?key='+secret_decode(p['key']);body={'contents':[{'parts':[{'text':s}]}]};h={'Content-Type':'application/json'}
 else:u=p['base'].rstrip('/')+'/chat/completions';body={'model':p['model'],'messages':[{'role':'user','content':s}]};h={'Content-Type':'application/json'};h.update({'Authorization':'Bearer '+secret_decode(p['key'])} if p['key'] else {})
 try:
  r=urllib.request.urlopen(urllib.request.Request(u,data=json.dumps(body).encode(),headers=h),timeout=90);d=json.loads(r.read());return d['candidates'][0]['content']['parts'][0]['text'] if p['id']=='gemini' else d['choices'][0]['message']['content']
 except urllib.error.HTTPError as e:raise RuntimeError(('429 ' if e.code==429 else 'server error ' if e.code>=500 else '')+'HTTP '+str(e.code))
def run(t):
 with db() as c:q=c.execute('SELECT * FROM tasks WHERE id=?',(t,)).fetchone();ps=c.execute('SELECT * FROM providers WHERE enabled=1 AND paid=0 ORDER BY priority').fetchall()
 if not q:return
 if not ps:
  with db() as c:c.execute("UPDATE tasks SET status='waiting',error='Connect a free provider.' WHERE id=?",(t,))
  log(t,'notice','Task saved. Connect a free model to run it.');return
 for i,p in enumerate(ps[:3],1):
  with db() as c:
   if c.execute("SELECT value FROM settings WHERE key='stop'").fetchone()[0]=='true' or c.execute('SELECT paused FROM tasks WHERE id=?',(t,)).fetchone()[0]:c.execute("UPDATE tasks SET status='paused' WHERE id=?",(t,));return
   c.execute('UPDATE tasks SET status=?,provider=?,attempts=? WHERE id=?',('running',p['id'],i,t))
  log(t,'activity','Agent using '+P[p['id']][0]+' / '+p['model'])
  try:
   a=call(p,q['prompt'])
   with db() as c:c.execute("UPDATE tasks SET status='completed',output=?,error='' WHERE id=?",(a,t))
   log(t,'complete','Response saved.');return
  except Exception as e:log(t,'handoff',str(e)+'; trying next free provider.')
 with db() as c:c.execute("UPDATE tasks SET status='failed',error='Eligible providers failed.' WHERE id=?",(t,))
def plan(s):return [{'role':'Planner','task':'Scope '+category(s)+' work'},{'role':'Developer/Researcher','task':'Complete assignment'},{'role':'Reviewer','task':'Check output'}]
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
   return self.send(200,[{'id':z['id'],'name':P[z['id']][0],'model':z['model'],'base':z['base'],'connected':bool(z['enabled']),'has_key':bool(z['key']),'paid':bool(z['paid'])} for z in r])
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
   n,b,m,paid=P[k];b=str(d.get('base',b));m=str(d.get('model',m));secret=secret_encode(str(d.get('key','')))
   if paid and not d.get('authorize_paid'):return self.send(402,{'error':'Explicitly authorize paid use first.'})
   if k=='custom' and not (b.startswith('https://') or 'localhost' in b or '127.0.0.1' in b):return self.send(400,{'error':'Use HTTPS or a local URL.'})
   with db() as c:c.execute("UPDATE providers SET model=?,base=?,key=CASE WHEN ?='' THEN key ELSE ? END,enabled=1 WHERE id=?",(m,b,secret,secret,k))
   return self.send(200,{'ok':True})
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

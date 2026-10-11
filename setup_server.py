"""Local setup UI and authenticated API. Run on the artwork host."""
import argparse
import hashlib
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time

from setup_devices import validate_config

CODE = Path(__file__).resolve().parent
UNIT = 'listening-room-setup.service'


def atomic(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2)+'\n')
    temp.chmod(0o600)
    temp.replace(path)


class App:
    def __init__(self, root, port):
        self.root, self.port = Path(root).expanduser().resolve(), port
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.guard = (self.root/'manager.lock').open('a')
        try:
            fcntl.flock(self.guard, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise
        self.mutex = threading.RLock()
        token = self.root/'api-token'
        if not token.exists():
            token.write_text(secrets.token_urlsafe(32))
        token.chmod(0o600)
        self.token = token.read_text().strip()
        self.process = None
        self.operation = None
        self.started = 0
        self.config = self.read('config.json', {})
        self.settings = self.read('settings.json', {'enabled':False})
        self.last_failure = ''

    def read(self, name, default):
        try:
            return json.loads((self.root/name).read_text())
        except (OSError, ValueError):
            return default

    def busy(self):
        return self.process is not None and self.process.poll() is None

    def status(self):
        with self.mutex:
            busy = self.busy()
            result = self.read('result.json', {}) if not busy and self.operation != 'run' else {}
            if self.process and not busy and self.process.returncode != 0 and not result:
                result = {'ok':False,'message':'Operation exited unexpectedly. Check connections and retry.'}
            state = self.read('display/state.json', {})
            recent = []
            seen = set()
            for folder in ('display','preview-display'):
                for entry in self.read(folder+'/state.json',{}).get('history',[]):
                    name = str(entry.get('cover','')).removeprefix('covers/')
                    if re.fullmatch(r'[a-f0-9]{64}\.jpg',name) and name not in seen:
                        recent.append({'image':name,'title':entry.get('title',''),'artist':entry.get('artist','')})
                        seen.add(name)
            return {'config':self.config,'running':busy and self.operation=='run',
                    'busy':busy,'operation':self.operation,'result':result,
                    'enabled':self.settings.get('enabled',False),
                    'title':state.get('title','') if busy and self.operation=='run' else '',
                    'view':state.get('view',''), 'failure':self.last_failure,
                    'operation_id':self.started, 'playback':self.read('playback.json',{}),
                    'presentation':self.read('presentation.json',{}),'recent':recent[:8],
                    'startup_supported':sys.platform.startswith('linux'),
                    'startup_installed':self.settings.get('startup_installed',False)}

    def conflict(self):
        if sys.platform.startswith('linux'):
            try:
                r = subprocess.run(['systemctl','--user','is-active','--quiet','frame-art.service'], timeout=3)
                if r.returncode == 0:
                    raise ValueError('The existing frame-art service is running. Stop it before using this manager.')
            except FileNotFoundError:
                pass

    def save(self, data):
        with self.mutex:
            if self.busy():
                raise ValueError('Stop the display or wait for the current step before saving.')
            config = validate_config(data)
            if self.config and (config['tv'],config['tv_model']) != (self.config['tv'],self.config['tv_model']):
                states = [self.read(folder+'/state.json',{}) for folder in ('display','test-display')]
                if any(state.get('owned') or state.get('pending') for state in states):
                    raise ValueError('Restore the old TV before changing TVs.')
                token = self.root/'display/token'
                if token.exists():
                    token.replace(self.root/'display/token.previous')
            if self.config:
                atomic(self.root/'config.previous.json',self.config)
            atomic(self.root/'config.json', config)
            self.config = config
            return {'message':'Configuration saved.'}

    def launch(self, action, payload=None):
        with self.mutex:
            if self.busy():
                raise ValueError('Stop the display or wait for the current step first.')
            if action not in ('discover','probe') and not self.config:
                raise ValueError('Save a TV and playback source first.')
            if action in ('run','test','restore','check'):
                self.conflict()
            if action == 'run':
                trial = self.read('test-display/state.json',{})
                if trial.get('owned') or trial.get('pending'):
                    raise ValueError('Restore the test artwork before starting the display.')
                command = [sys.executable,str(CODE/'setup_worker.py'),str(self.root),'run']
                (self.root/'playback.json').unlink(missing_ok=True)
            else:
                atomic(self.root/'request.json', payload or {})
                (self.root/'result.json').unlink(missing_ok=True)
                command = [sys.executable,str(CODE/'setup_worker.py'),str(self.root),action]
            log = self.root/'worker.log'
            if log.exists() and log.stat().st_size > 1_000_000:
                log.replace(self.root/'worker.previous.log')
            with log.open('a') as output:
                self.process = subprocess.Popen(command, stdout=output, stderr=output, cwd=CODE,
                                                start_new_session=True)
            self.operation, self.started = action, time.monotonic()
            self.last_failure = ''
            if action == 'run':
                self.settings['enabled'] = True
                atomic(self.root/'settings.json', self.settings)
            return {'message':'Following playback.' if action=='run' else 'Step started.'}

    def stop(self, disable=True):
        with self.mutex:
            if disable:
                self.settings['enabled'] = False
                atomic(self.root/'settings.json', self.settings)
            if self.busy():
                self.process.terminate()
                try:
                    self.process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=5)
                    self.last_failure = 'Worker stopped after timeout. Use Restore artwork when the TV is reachable.'
            return {'message':self.last_failure or 'Stopped. Original-art restoration was attempted.'}

    def startup(self):
        if not sys.platform.startswith('linux'):
            raise ValueError('Automatic startup currently requires Linux with a systemd user session.')
        # Never request administrator privileges or change lingering automatically.
        def quote(value):
            return '"'+str(value).replace('%','%%').replace('\\','\\\\').replace('"','\\"')+'"'
        args = [sys.executable,CODE/'setup_server.py','--state-dir',self.root,'--port',str(self.port)]
        unit = ('[Unit]\nDescription=Listening Room setup and artwork manager\nAfter=network-online.target\n'
                '[Service]\nExecStart='+' '.join(quote(x) for x in args)+'\nRestart=on-failure\n'
                'RestartSec=5\nTimeoutStopSec=50\nUMask=0077\nNoNewPrivileges=true\n'
                '[Install]\nWantedBy=default.target\n')
        target = Path.home()/'.config/systemd/user'/UNIT
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.read_text() != unit:
            raise ValueError('A different setup service already exists. Review it before replacing it.')
        target.write_text(unit)
        for command in (['systemctl','--user','daemon-reload'], ['systemctl','--user','enable',UNIT]):
            subprocess.run(command,check=True,timeout=10,capture_output=True)
        self.settings['startup_installed'] = True
        atomic(self.root/'settings.json',self.settings)
        return {'message':'Enabled for future user sessions. Keep this window running today. For boot without login, configure user lingering separately and verify after reboot.'}

    def tick(self):
        with self.mutex:
            if self.busy() and self.operation != 'run' and time.monotonic()-self.started > 100:
                self.stop(disable=False)
                self.last_failure='Setup step timed out. Check connections and retry.'
            if self.operation == 'run' and not self.busy() and self.settings.get('enabled'):
                if time.monotonic()-self.started > 30:
                    try:
                        self.launch('run')
                    except Exception:
                        self.last_failure='Could not restart display. Stop and check the configuration.'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # No URLs or credentials in HTTP logs.

    def reply(self, code, data, kind='application/json', headers=None):
        body = json.dumps(data).encode() if kind=='application/json' else data
        self.send_response(code)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        for name,value in (headers or {}).items():
            self.send_header(name,value)
        self.end_headers();self.wfile.write(body)

    def authorized(self, session=False):
        hosts = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host','')
        origin = self.headers.get('Origin')
        if host not in hosts or (origin and origin != 'http://'+host):
            self.reply(403,{'error':'Local same-origin access required.'});return False
        if self.headers.get('Sec-Fetch-Site') not in (None,'none','same-origin'):
            self.reply(403,{'error':'Cross-site access denied.'});return False
        if not session:
            provided = self.headers.get('Authorization','').removeprefix('Bearer ')
            if not secrets.compare_digest(provided,self.server.app.token):
                self.reply(401,{'error':'API token required.'});return False
        return True

    def do_GET(self):
        if not self.authorized(session=self.path in ('/','/app.js','/style.css','/api/session')):
            return
        if self.path=='/api/session':
            self.reply(200,{'token':self.server.app.token})
        elif self.path=='/api/image/current' or self.path.startswith('/api/cover/'):
            if self.path=='/api/image/current':
                paths=[self.server.app.root/'preview.jpg']
            else:
                name=self.path.removeprefix('/api/cover/')
                if not re.fullmatch(r'[a-f0-9]{64}\.jpg',name):
                    self.reply(404,{'error':'Not found'});return
                paths=[self.server.app.root/folder/'covers'/name for folder in ('display','preview-display')]
            path=next((p for p in paths if p.is_file() and not p.is_symlink()),None)
            if path is None or path.stat().st_size>20_000_000:
                self.reply(404,{'error':'Image not available'});return
            content=path.read_bytes()
            self.reply(200,content,'image/jpeg',{'X-Image-Revision':hashlib.sha256(content).hexdigest()})
        elif self.path=='/api/status':
            self.reply(200,self.server.app.status())
        elif self.path in ('/','/app.js','/style.css'):
            name,kind={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),'/style.css':('style.css','text/css')}[self.path]
            self.reply(200,(CODE/'setup_ui'/name).read_bytes(),kind)
        else:
            self.reply(404,{'error':'Not found'})

    def do_POST(self):
        if not self.authorized():return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<0 or size>16384: raise ValueError('Request too large')
            data=json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(data,dict):raise ValueError('Expected a JSON object')
            app=self.server.app
            if self.path=='/api/config':result=app.save(data)
            elif self.path=='/api/stop':result=app.stop()
            elif self.path=='/api/startup':result=app.startup()
            elif self.path in ('/api/discover','/api/probe','/api/check','/api/preview','/api/test','/api/restore','/api/start'):
                action=self.path.rsplit('/',1)[-1]
                result=app.launch('run' if action=='start' else action,data)
            else:
                self.reply(404,{'error':'Not found'});return
            self.reply(200,result)
        except (ValueError,TypeError) as exc:
            self.reply(400,{'error':str(exc)})
        except Exception as exc:
            self.reply(500,{'error':'Operation failed ('+type(exc).__name__+'). Check the host and retry.'})


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default='~/.local/state/listening-room-setup')
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    app=App(args.state_dir,args.port)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.app=app
    server.daemon_threads=True
    closing=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:closing.set())
    server.timeout=.5
    print(f'Open http://127.0.0.1:{args.port} on this computer.',flush=True)
    if app.settings.get('enabled') and app.config:
        try:app.launch('run')
        except Exception:app.last_failure='Could not resume display. Check for another running artwork service.'
    try:
        while not closing.is_set():
            server.handle_request();app.tick()
    finally:
        app.stop(disable=False);server.server_close();app.guard.close()

if __name__=='__main__':main()

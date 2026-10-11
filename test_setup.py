import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from setup_devices import local_host, probe_tv, validate_config
from setup_server import App, Handler, ThreadingHTTPServer


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=App(self.tmp.name,8765)
        self.config={'source':'sonos','tv':'frame.local','tv_model':'TEST_FRAME','sonos_uid':'TEST_ROOM'}
        self.host=patch('setup_devices.socket.getaddrinfo',return_value=[(2,1,6,'',('192.168.1.2',0))]);self.host.start()

    def tearDown(self):
        self.app.guard.close();self.host.stop();self.tmp.cleanup()

    def test_config_saved_private_without_unknown_fields(self):
        self.app.save({**self.config,'token':'do not persist','command':'dangerous'})
        saved=json.loads((self.app.root/'config.json').read_text())
        self.assertNotIn('token',saved);self.assertNotIn('command',saved)
        self.assertEqual(saved['sonos_uid'],'TEST_ROOM')
        self.assertEqual((self.app.root/'config.json').stat().st_mode & 0o777,0o600)
        self.app.save({**self.config,'sonos_uid':'OTHER_ROOM','sonos_room_name':'Kitchen'})
        self.assertEqual(json.loads((self.app.root/'config.previous.json').read_text())['sonos_uid'],'TEST_ROOM')
        self.assertEqual(self.app.config['sonos_room_name'],'Kitchen')

    def test_config_validation_and_tv_change_guard(self):
        for data in ({**self.config,'sonos_uid':''},{**self.config,'source':'bad'},
                     {**self.config,'tv':'http://frame.local'},{**self.config,'tv_model':''}):
            with self.assertRaises(ValueError):self.app.save(data)
        self.app.save(self.config)
        (self.app.root/'display').mkdir()
        (self.app.root/'display/state.json').write_text('{"owned":"CARD"}')
        with self.assertRaises(ValueError):self.app.save({**self.config,'tv':'another.local'})

    def test_public_or_loopback_devices_rejected(self):
        for ip in ('8.8.8.8','127.0.0.1'):
            with patch('setup_devices.socket.getaddrinfo',return_value=[(2,1,6,'',(ip,0))]):
                with self.assertRaises(ValueError):local_host('device.local')

    def test_source_configs(self):
        roon=validate_config({**self.config,'source':'roon','roon_state_dir':'~/roon'})
        self.assertNotIn('sonos_uid',roon)
        upnp=validate_config({**self.config,'source':'upnp','renderer_control_url':'http://renderer.local/control','local_art_hosts':['music.local']})
        self.assertEqual(upnp['local_art_hosts'],['music.local'])
        with self.assertRaises(ValueError):validate_config({**self.config,'source':'upnp','renderer_control_url':'file:///etc/passwd'})

    def test_launch_serialized_and_stop_disables_resume(self):
        self.app.save(self.config)
        process=Mock();process.poll.return_value=None
        with patch.object(self.app,'conflict'),patch('setup_server.subprocess.Popen',return_value=process) as popen:
            self.app.launch('run')
            self.assertTrue(self.app.status()['running'])
            self.assertIn('setup_worker.py',popen.call_args.args[0][1])
            with self.assertRaises(ValueError):self.app.launch('test')
            with self.assertRaises(ValueError):self.app.save(self.config)
            self.app.stop()
            process.terminate.assert_called_once()
            self.assertFalse(self.app.settings['enabled'])

    def test_timeout_kills_worker_but_retains_restoration_warning(self):
        process=Mock();process.poll.return_value=None
        import subprocess
        process.wait.side_effect=[subprocess.TimeoutExpired('test',40),0]
        self.app.process=process
        self.app.stop()
        process.kill.assert_called_once()
        self.assertIn('Restore',self.app.last_failure)

    def test_existing_service_conflict_blocks_launch(self):
        self.app.save(self.config)
        with patch('setup_server.sys.platform','linux'),patch('setup_server.subprocess.run',return_value=Mock(returncode=0)):
            with self.assertRaises(ValueError):self.app.launch('run')

    def test_manager_lock_prevents_duplicate_owner(self):
        with self.assertRaises(BlockingIOError):App(self.tmp.name,8766)

    def test_startup_uses_fixed_user_service_without_starting_second_manager(self):
        with patch('setup_server.sys.platform','linux'),patch('setup_server.Path.home',return_value=Path(self.tmp.name)),patch('setup_server.subprocess.run') as run:
            self.app.startup()
            calls=[call.args[0] for call in run.call_args_list]
            self.assertEqual(calls[-1],['systemctl','--user','enable','listening-room-setup.service'])
            unit=(Path(self.tmp.name)/'.config/systemd/user/listening-room-setup.service').read_text()
            self.assertIn('setup_server.py',unit)
            self.assertNotIn('sudo',unit)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=App(self.tmp.name,0)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.app.guard.close();self.tmp.cleanup()

    def request(self,path,headers=None,body=None):
        request=Request(self.base+path,headers=headers or {},data=body)
        try:
            with urlopen(request,timeout=3) as r:return r.status,r.read()
        except HTTPError as e:return e.code,e.read()

    def test_browser_bootstrap_and_authenticated_status(self):
        code,body=self.request('/api/session');self.assertEqual(code,200)
        token=json.loads(body)['token']
        self.assertEqual(self.request('/api/status')[0],401)
        self.assertEqual(self.request('/api/status',{'Authorization':'Bearer '+token})[0],200)
        self.assertEqual(self.request('/')[0],200)
        self.assertEqual(self.request('/../api-token')[0],401)

    def test_cross_origin_and_rebinding_rejected_even_with_token(self):
        for extra in ({'Origin':'https://evil.example'},{'Host':'evil.example'},{'Sec-Fetch-Site':'cross-site'}):
            headers={'Authorization':'Bearer '+self.app.token,**extra}
            self.assertEqual(self.request('/api/stop',headers,b'{}')[0],403)
            self.assertEqual(self.request('/api/session',extra)[0],403)

    def test_authorized_save_and_job_dispatch(self):
        headers={'Authorization':'Bearer '+self.app.token}
        config={'source':'sonos','tv':'frame.local','tv_model':'TEST','sonos_uid':'ROOM'}
        with patch('setup_devices.local_host',side_effect=lambda host:host):
            code,_=self.request('/api/config',headers,json.dumps(config).encode())
        self.assertEqual(code,200)
        self.assertEqual(self.app.config['sonos_uid'],'ROOM')
        with patch.object(self.app,'launch',return_value={'message':'started'}) as launch:
            self.assertEqual(self.request('/api/test',headers,b'{}')[0],200)
            launch.assert_called_once_with('test',{})

    def test_bad_json_and_unknown_route(self):
        headers={'Authorization':'Bearer '+self.app.token}
        self.assertEqual(self.request('/api/config',headers,b'[')[0],400)
        self.assertEqual(self.request('/api/nope',headers,b'{}')[0],404)


class WorkerTests(unittest.TestCase):
    def test_generated_card_restores_and_uses_separate_state(self):
        import setup_worker
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'request.json').write_text('{}')
            (root/'config.json').write_text('{"source":"sonos"}')
            (root/'display').mkdir()
            normal=Mock()
            trial=Mock(state={'album':'sonos:setup-test'})
            trial.owned_ids.return_value=set()
            # Advance immediately through the 15-second display window.
            with patch('setup_worker.sys.argv',['worker',directory,'test']), patch('setup_worker.Bridge',side_effect=[normal,trial]) as bridge, patch('setup_worker.run_bounded',side_effect=lambda f,*args:f()), patch('setup_worker.time.monotonic',side_effect=[0,20]), patch('setup_worker.signal.signal'):
                setup_worker.main()
            self.assertTrue(json.loads((root/'result.json').read_text())['ok'])
            self.assertEqual(bridge.call_args.args[1],root/'test-display')
            trial.step.assert_called_once()
            self.assertEqual(trial.restore.call_count,2)
            self.assertTrue((root/'test-display/token').is_symlink())
            normal.step.assert_not_called()

    def test_connection_failure_returns_redacted_result(self):
        import setup_worker
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'request.json').write_text('{"host":"device.local"}')
            with patch('setup_worker.sys.argv',['worker',directory,'probe']), patch('setup_worker.probe_tv',side_effect=RuntimeError('SECRET_SIGNED_URL')), patch('setup_worker.run_bounded',side_effect=lambda f,*args:f()), patch('setup_worker.signal.signal'):
                with self.assertRaises(SystemExit):setup_worker.main()
            text=(root/'result.json').read_text()
            self.assertNotIn('SECRET_SIGNED_URL',text)
            self.assertFalse(json.loads(text)['ok'])

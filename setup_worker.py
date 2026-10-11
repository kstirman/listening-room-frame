"""Isolated, time-bounded setup operations. Private files stay on the host."""
import io
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import time

from PIL import Image, ImageDraw
from layouts import font
from bridge import run_bounded
from setup_preview import PreviewBridge as Bridge
from setup_devices import discover_rooms, discover_tvs, probe_tv


def write(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(path)


def main():
    os.umask(0o077)
    root, action = Path(sys.argv[1]), sys.argv[2]
    payload = json.loads((root/'request.json').read_text()) if action != 'run' else {}
    result = root/'result.json'
    guard = (root/'worker.lock').open('a')
    try:
        fcntl.flock(guard, fcntl.LOCK_EX|fcntl.LOCK_NB)
        stopping = [False]
        parent = os.getppid()
        def stop(*_):
            stopping[0] = True
            remaining, _ = signal.getitimer(signal.ITIMER_REAL)
            if remaining > 30:
                signal.setitimer(signal.ITIMER_REAL,30)
        signal.signal(signal.SIGTERM, stop)
        if action == 'run':
            config = json.loads((root/'config.json').read_text())
            bridge = Bridge(config, root/'display')
            observe = bridge.observe_playback
            def observed(status, metadata):
                observe(status, metadata)
                write(root/'playback.json', {'transport':status,'title':metadata.get('title',''),
                                             'artist':metadata.get('artist',''),'album':metadata.get('album',''),'updated_at':time.time()})
            bridge.observe_playback = observed
            while not stopping[0] and os.getppid()==parent:
                delay = config.get('poll_seconds',2)
                try:
                    run_bounded(bridge.step)
                except Exception as exc:
                    write(root/'playback.json', {'transport':'UNAVAILABLE','title':'',
                                                 'error':type(exc).__name__,'updated_at':time.time()})
                    delay = 5
                until = time.monotonic()+delay
                while time.monotonic()<until and not stopping[0]:
                    time.sleep(.2)
            run_bounded(bridge.restore,30)
            return
        def work():
            if action == 'discover':
                rooms, warning = [], ''
                try:
                    rooms = discover_rooms()
                except ImportError:
                    warning = 'Install requirements-sonos.txt to discover Sonos rooms.'
                return {'rooms': rooms, 'tvs': discover_tvs(), 'warning': warning}
            if action == 'probe':
                return probe_tv(payload['host'])
            config = json.loads((root/'config.json').read_text())
            bridge = Bridge(config, root/'display')
            if action == 'preview':
                bridge = Bridge(config,root/'preview-display')
                status, metadata = bridge.playback()
                if status != 'PLAYING' or not bridge.album_key(metadata):
                    return {'message':'Play a track with artwork in the selected room, then try again.', 'prepared':False}
                bridge.card(metadata,bridge.details(metadata))
                write(root/'playback.json', {'transport':status,'title':metadata.get('title',''),
                                           'artist':metadata.get('artist',''),'album':metadata.get('album',''),
                                           'updated_at':time.time()})
                return {'message':'Preview prepared. Nothing was sent to the TV.', 'prepared':True}
            if action == 'check':
                status, metadata = bridge.playback()
                tv = bridge.television()
                connected = tv is not None
                if tv:
                    tv.close()
                return {'playback': status, 'title': metadata.get('title', ''),
                        'album': metadata.get('album', ''), 'tv_art_mode': connected,
                        'message': 'TV connected in Art Mode.' if connected else 'Put the Frame in Art Mode and check again.'}
            if action == 'restore':
                bridge.restore()
                trial = Bridge(config, root/'test-display')
                trial.restore()
                if bridge.owned_ids() or trial.owned_ids():
                    raise RuntimeError('Restoration pending; TV may be unavailable')
                return {'message': 'Restoration checked. Manual TV selections are preserved.'}
            if action == 'test':
                trial = root/'test-display'
                trial.mkdir(exist_ok=True)
                token = trial/'token'
                if not token.exists() and not token.is_symlink():
                    token.symlink_to(root/'display/token')
                bridge = Bridge(config, trial)
                bridge.restore()
                picture = Image.new('RGB', (1000,1000), '#a3442e')
                draw = ImageDraw.Draw(picture)
                for radius in range(480,70,-10):
                    draw.ellipse((720-radius,540-radius,720+radius,540+radius), outline='#342820', width=5)
                draw.ellipse((645,465,795,615),fill='#eee9df')
                draw.text((75,100),'HELLO,',font=font(115),fill='#f6eedf')
                draw.text((75,225),'LISTENING',font=font(100),fill='#f6eedf')
                draw.text((75,340),'ROOM.',font=font(115),fill='#f6eedf')
                draw.text((75,900),'SOUND, WITH A VIEW.',font=font(25),fill='#f6eedf')
                content = io.BytesIO(); picture.save(content, format='JPEG')
                metadata = {'source':'sonos', 'source_album_key':'sonos:setup-test',
                            'object_id':'sonos:setup-test', 'album':'Listening Room', 'artist':'Setup',
                            'sonos_lines':['Your Frame is connected', 'Listening Room', 'Original artwork returns in 15 seconds']}
                # Synthetic art, independent of music service or current playback.
                bridge.sonos = type('TestArt', (), {'cover':lambda self, _:content.getvalue()})()
                bridge.playback = lambda: ('PLAYING', metadata)
                try:
                    bridge.step()
                    if bridge.state.get('album') != 'sonos:setup-test':
                        raise RuntimeError('TV is not ready for the test image')
                    until = time.monotonic()+15
                    while time.monotonic()<until and not stopping[0] and os.getppid()==parent:
                        time.sleep(.2)
                finally:
                    run_bounded(bridge.restore, 30)
                if bridge.owned_ids():
                    raise RuntimeError('Restoration pending; TV may be unavailable')
                return {'message':'Test image displayed and restoration checked.'}
            raise ValueError('Unknown operation')
        try:
            data = run_bounded(work, 90)
            write(result, {'ok':True, 'data':data})
        except Exception as exc:
            write(result, {'ok':False, 'error':type(exc).__name__,
                           'message':'Could not complete this step. Check device reachability, TV approval and Art Mode; then retry.'})
            sys.exit(1)
    finally:
        guard.close()

if __name__ == '__main__':
    main()

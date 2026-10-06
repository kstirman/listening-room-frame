import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import bridge


class TransitionTests(unittest.TestCase):
    def test_changed_track_same_album_is_observed_without_secrets(self):
        b = bridge.Bridge.__new__(bridge.Bridge)
        first = {'object_id':'private-track-one', 'album_id':'private-album'}
        with patch('bridge.LOG') as log:
            b.observe_playback('PLAYING', first)
            b.observe_playback('PLAYING', first)
            b.observe_playback('PLAYING', dict(first, object_id='private-track-two'))
            b.observe_playback('STOPPED', {})
        self.assertEqual(log.info.call_count, 3)
        self.assertNotIn('private-', str(log.info.call_args_list))

    def test_resource_changes_without_controller_id_are_observed(self):
        b = bridge.Bridge.__new__(bridge.Bridge)
        with patch('bridge.LOG') as log:
            b.observe_playback('PLAYING', {'resource':'https://example.invalid/one?secret=one'})
            b.observe_playback('PLAYING', {'resource':'https://example.invalid/two?secret=two'})
        self.assertEqual(log.info.call_count, 2)
        self.assertNotIn('secret', str(log.info.call_args_list))

    def test_changed_stream_with_stale_metadata_is_observed(self):
        b = bridge.Bridge.__new__(bridge.Bridge)
        metadata = {'object_id':'same-track', 'album_id':'same-album'}
        with patch('bridge.LOG') as log:
            b._media_fingerprint = 'first-hash'
            b.observe_playback('PLAYING', metadata)
            b._media_fingerprint = 'second-hash'
            b.observe_playback('PLAYING', metadata)
        self.assertEqual(log.info.call_count, 2)

    def test_failed_lookup_polls_again_after_five_seconds(self):
        # Exercise the real main loop: failed update, bounded wait, next update.
        clock = [0.0]
        calls = []
        fake = Mock(config={'poll_seconds':2}, running=True, collage_until=None,
                    phase='metadata-lookup')
        def step():
            calls.append(clock[0])
            if len(calls) == 1:
                raise ConnectionError('private-url')
            fake.running = False
        fake.step.side_effect = step
        def sleep(seconds):
            clock[0] += seconds
        with tempfile.TemporaryDirectory() as d:
            config = Path(d)/'config.json';config.write_text('{}')
            with patch('sys.argv', ['bridge', '--config', str(config), '--state-dir', d]), \
                 patch('bridge.Bridge', return_value=fake), \
                 patch('bridge.signal.signal'), patch('bridge.os.umask'), \
                 patch('bridge.run_bounded', side_effect=lambda fn, *args: fn()), \
                 patch('bridge.time.monotonic', side_effect=lambda: clock[0]), \
                 patch('bridge.time.sleep', side_effect=sleep), patch('bridge.LOG') as log:
                bridge.main()
        self.assertEqual(calls, [0.0, 5.0])
        fake.restore.assert_called_once()
        self.assertNotIn('private-url', str(log.warning.call_args_list))
        self.assertIn('metadata-lookup', str(log.warning.call_args_list))

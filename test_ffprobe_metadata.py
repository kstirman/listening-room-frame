import json
import os
import unittest
from unittest.mock import Mock, patch

import ffprobe_metadata


class FfprobeMetadataTests(unittest.TestCase):
    def test_output_overflow_kills_ffprobe_before_json_parsing(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b'x' * 9)
        finally:
            os.close(write_fd)
        process = Mock()
        process.stdout = os.fdopen(read_fd, 'rb', buffering=0)
        process.poll.return_value = None
        process.wait.return_value = 0
        with patch('ffprobe_metadata.MAX_OUTPUT_BYTES', 8), \
             patch('ffprobe_metadata.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(ValueError, 'output exceeds byte limit'):
                ffprobe_metadata.read('/library/crafted.flac')
        process.kill.assert_called_once_with()

    def test_tag_count_and_value_limits_reject_unsafe_metadata(self):
        excessive = [
            {'format': {'tags': {'tag'+str(i): 'value' for i in range(257)}}},
            {'format': {'tags': {'title': 'x' * (64 * 1024 + 1)}}},
        ]
        for document in excessive:
            with self.subTest(tags=len(document['format']['tags'])):
                output = json.dumps(document).encode()
                with patch('ffprobe_metadata._run', return_value=output):
                    with self.assertRaises(ValueError):
                        ffprobe_metadata.read('/library/crafted.flac')

    def test_ordinary_tags_and_probe_options_are_preserved(self):
        output = json.dumps({'format': {'duration': '61.4',
            'tags': {'COMPOSER': 'Composer', 'movement': '2/4'}}}).encode()
        with patch('ffprobe_metadata._run', return_value=output) as run:
            result = ffprobe_metadata.read('/library/track.flac', timeout=2,
                probe_args=('-analyzeduration','0','-probesize','32768'))
        self.assertEqual(result, {'duration':'61.4',
            'tags':{'composer':'Composer','movement':'2/4'}})
        command = run.call_args.args[0]
        self.assertIn('-nostdin', command)
        self.assertEqual(command[-1], '/library/track.flac')


if __name__ == '__main__':
    unittest.main()

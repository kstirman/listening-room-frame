"""Exercise actual Linux deadline expiry in isolated child processes."""
import signal
import subprocess
import sys
import unittest
from pathlib import Path
from bridge import run_bounded


class WatchdogTests(unittest.TestCase):
    def test_success_cancels_alarm_and_restores_handler(self):
        before = signal.getsignal(signal.SIGALRM)
        self.assertEqual(run_bounded(lambda: 42, .1), 42)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))
        self.assertEqual(signal.getsignal(signal.SIGALRM), before)

    def test_failure_cancels_alarm(self):
        def fail():
            raise ValueError('synthetic')
        with self.assertRaises(ValueError):
            run_bounded(fail, .1)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0, 0))

    def child(self, action):
        code = ('from bridge import run_bounded\nimport time\n'
                + action + '\nrun_bounded(blocked, .1)\n')
        return subprocess.run([sys.executable, '-c', code],
                              cwd=Path(__file__).parent, capture_output=True,
                              text=True, timeout=10)

    def test_blocked_wait_exits_without_running_stuck_cleanup(self):
        result = self.child('''def blocked():
    secret = "synthetic-private-url"
    try:
        time.sleep(30)
    finally:
        time.sleep(30)
''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('timed out (blocked)', result.stderr)
        self.assertIn('blocked', result.stderr)
        self.assertNotIn('synthetic-private-url', result.stderr)

    def test_unrelated_events_cannot_extend_deadline(self):
        result = self.child('''def blocked():
    while True:
        time.sleep(.001)  # Each receive succeeds, expected event never arrives.
''')
        self.assertEqual(result.returncode, 1)
        self.assertIn('restarting', result.stderr)

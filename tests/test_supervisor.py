import sys
import unittest

from mimicgate.browser.supervisor import launch_owned


class SupervisorTests(unittest.TestCase):
    def test_owned_process_group_is_reaped(self):
        owner = launch_owned([sys.executable, "-c", "import time; time.sleep(30)"], "/tmp/mimicgate-supervisor")
        self.assertNotEqual(owner.pid, 0)
        owner.terminate(timeout=2)
        self.assertIsNotNone(owner.process.poll())

    def test_empty_command_is_rejected(self):
        with self.assertRaises(ValueError):
            launch_owned([], "/tmp/mimicgate-supervisor")

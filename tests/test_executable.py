# Copyright (c) 2018-2019 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General Public
# License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""Test cases for runner module."""
import logging
import os
import signal
import threading
import time
import unittest

import mock
from testdef.rc_data import SKTData

from skt import executable
from skt.runner import BeakerRunner
from tests import misc

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
RC_EXAMPLE = open(os.path.join(ASSETS_DIR, 'actual_rc.cfg')).read()


def trigger_signal():
    """ Send SIGTERM to self after 2 seconds."""
    time.sleep(2)
    pid = os.getpid()
    os.kill(pid, signal.SIGTERM)


class TestExecutable(unittest.TestCase):
    """Test cases for executable module."""

    def setUp(self) -> None:
        self.myrunner = BeakerRunner(**misc.DEFAULT_ARGS)

    def test_full_path_relative(self):
        """Verify that full_path() expands a relative path."""
        filename = "somefile"
        result = executable.full_path(filename)
        expected_path = "{}/{}".format(os.getcwd(), filename)
        self.assertEqual(expected_path, result)

    def test_full_path_user_directory(self):
        """Verify that full_path() expands a user directory path."""
        filename = "somefile"
        result = executable.full_path("~/{}".format(filename))
        expected_path = "{}/{}".format(os.path.expanduser('~'), filename)
        self.assertEqual(expected_path, result)

    def test_setup_logging(self):
        """Ensure that setup_logging works and sets-up to what we expect."""
        verbose = False
        executable.setup_logging(verbose)

        requests_lgr = logging.getLogger('requests')
        urllib3_lgr = logging.getLogger('urllib3')

        requests_level = logging.getLevelName(requests_lgr.getEffectiveLevel())
        urllib3_level = logging.getLevelName(urllib3_lgr.getEffectiveLevel())

        self.assertEqual(requests_level, 'WARNING')
        self.assertEqual(urllib3_level, 'WARNING')

        current_logger = logging.getLogger('executable')
        self.assertEqual(current_logger.getEffectiveLevel(), logging.WARNING -
                         (verbose * 10))

    @mock.patch('skt.executable.BeakerRunner')
    @mock.patch('builtins.open', create=True)
    @mock.patch('subprocess.Popen')
    @mock.patch('logging.error')
    @mock.patch('subprocess.call')
    @mock.patch('skt.runner.BeakerRunner._BeakerRunner__jobsubmit')
    def test_cleanup_called(self, mock_jobsubmit, mock_call, mock_log_err,
                            mock_popen, mock_open, mock_runner):
        """Ensure BeakerRunner.signal_handler works."""
        # pylint: disable=W0613,R0913
        mock_runner.return_value = self.myrunner

        mock_jobsubmit.return_value = "J:0001"

        mock_call.return_value = 0
        mock_popen.return_value = 0

        thread = threading.Thread(target=trigger_signal)

        try:
            thread.start()
            # it's fine to call this directly, no need to mock
            skt_data = SKTData.deserialize(RC_EXAMPLE)

            executable.cmd_run(skt_data)
            thread.join()
        except (KeyboardInterrupt, SystemExit):
            logging.info('Thread cancelling...')

        self.assertTrue(executable.cmd_run.cleanup_done)

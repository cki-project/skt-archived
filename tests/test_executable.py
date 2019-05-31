# Copyright (c) 2018 Red Hat, Inc. All rights reserved. This copyrighted
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
import sys
import unittest

from io import BytesIO
from StringIO import StringIO

import mock

from skt import executable


class TestExecutable(unittest.TestCase):
    """Test cases for executable module."""

    # pylint: disable=too-many-public-methods
    def check_args_tester(self, args, expected_fail=True,
                          expected_stdout=None, expected_stderr=None):
        """Reusable method to test the check_args() method."""
        parser = executable.setup_parser()

        # Capture stdout/stderr temporarily
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        temp_stdout = StringIO()
        temp_stderr = StringIO()
        sys.stdout = temp_stdout
        sys.stderr = temp_stderr

        if expected_fail:
            with self.assertRaises(SystemExit):
                args = parser.parse_args(args)
        else:
            args = parser.parse_args(args)

        if expected_stdout:
            self.assertIn(expected_stdout, temp_stdout.getvalue().strip())

        if expected_stderr:
            self.assertIn(expected_stderr, temp_stderr.getvalue().strip())

        # Reset stdout/stderr
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    def load_config_tester(self, config_file, testing_args):
        """Reusable method to test the load_config() method."""
        mock_open = mock.patch(
            'ConfigParser.open',
            return_value=BytesIO('\n'.join(config_file))
        )
        parser = executable.setup_parser()
        args = parser.parse_args(testing_args)

        with mock_open:
            cfg = executable.load_config(args)

        self.assertTrue(isinstance(cfg, dict))
        return cfg

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


    def test_load_config_runner_args(self):
        """Test load_config() with runner arguments."""
        config_file = []
        args = ['--rc', '/tmp/testing.ini', '--workdir', '/tmp/workdir',
                '--state', 'run', '--runner', 'myrunner', '[\'value\']']
        cfg = self.load_config_tester(config_file, args)
        self.assertListEqual(['myrunner', ['value']], cfg['runner'])

    def test_save_state(self):
        """Ensure save_state works."""
        def merge_two_dicts(dict1, dict2):
            """Given two dicts, merge them into json_data new dict as
            json_data shallow copy."""
            result = dict1.copy()
            result.update(dict2)
            return result

        # cfg has to have 'state', otherwise it returns None
        self.assertIsNone(executable.save_state({}, {}))

        config_file = []
        args = ['--rc', '/tmp/testing.ini', '--workdir', '/tmp/workdir',
                '--state', 'run', '--runner', 'myrunner', '{"key": "value"}']
        cfg = self.load_config_tester(config_file, args)

        state = {'a': 0, 'b': 1, 'c': 1}

        result = merge_two_dicts(cfg, state)

        executable.save_state(cfg, state)

        self.assertEqual(cfg, result)

    def test_addtstamp(self):
        """Ensure addtstamp works."""
        testdata = {
            ('/etc/shadow', '45-10'): '/etc/45-10-shadow',
            ('/etc/rc.d/e', '45-10'): '/etc/rc.d/45-10-e'
        }

        for key in testdata:
            path, stamp = key
            result = executable.addtstamp(path, stamp)

            self.assertEqual(result, testdata[key])

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

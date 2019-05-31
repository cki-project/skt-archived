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
from io import StringIO

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

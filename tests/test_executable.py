"""
Test cases for runner module.
"""
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
from StringIO import StringIO
import os
import sys
import unittest

from skt import executable


class TestExecutable(unittest.TestCase):
    """Test cases for executable module"""

    def check_args_tester(self, args, expected_fail=True,
                          expected_stdout=None, expected_stderr=None):
        """Reusable method to test the check_args() method."""
        parser = executable.setup_parser()

        # Capture stdout/stderr temporarily
        temp_stdout = StringIO()
        temp_stderr = StringIO()
        sys.stdout = temp_stdout
        sys.stderr = temp_stderr

        if expected_fail:
            with self.assertRaises(SystemExit):
                args = parser.parse_args(args)
                executable.check_args(parser, args)
        else:
            args = parser.parse_args(args)
            executable.check_args(parser, args)

        if expected_stdout:
            self.assertIn(expected_stdout, temp_stdout.getvalue().strip())

        if expected_stderr:
            self.assertIn(expected_stderr, temp_stderr.getvalue().strip())

    def test_full_path_relative(self):
        """Verify that full_path() expands a relative path"""
        filename = "somefile"
        result = executable.full_path(filename)
        expected_path = "{}/{}".format(os.getcwd(), filename)
        self.assertEqual(expected_path, result)

    def test_full_path_user_directory(self):
        """Verify that full_path() expands a user directory path"""
        filename = "somefile"
        result = executable.full_path("~/{}".format(filename))
        expected_path = "{}/{}".format(os.path.expanduser('~'), filename)
        self.assertEqual(expected_path, result)

    def test_check_args_basic(self):
        """Test check_args() with merge."""
        args = ['merge']
        self.check_args_tester(args, expected_fail=False)

    def test_check_args_mail_incomplete(self):
        """Test check_args() with incomplete mail args."""
        args = ['report', '--reporter', 'mail']
        expected_stderr = (
            '--reporter mail requires --mail-to and --mail-from to be set'
        )
        self.check_args_tester(args, expected_stderr=expected_stderr)

    def test_check_args_missing_glob(self):
        """Test check_args() without rh-configs-glob."""
        args = ['build', '--cfgtype', 'rh-configs']
        expected_stderr = (
            '--cfgtype rh-configs requires --rh-configs-glob to set'
        )
        self.check_args_tester(args, expected_stderr=expected_stderr)

    def test_check_args_stdio_mail(self):
        """Test check_args() with stdio and mail arguments."""
        args = ['report', '--reporter', 'stdio', '--mail-to',
                'someone@example.com']
        expected_stderr = (
            'the stdio reporter was selected but arguments for the mail '
            'reporter were provided'
        )
        self.check_args_tester(args, expected_stderr=expected_stderr)

    def test_check_args_stdio_valid(self):
        """Test check_args() with stdio and valid arguments."""
        args = ['report', '--reporter', 'stdio']
        self.check_args_tester(args, expected_fail=False)

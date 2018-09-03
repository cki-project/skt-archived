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
from io import BytesIO
import os
import sys
import unittest

import mock
import six
from skt import executable


class TestExecutable(unittest.TestCase):
    """Test cases for executable module."""

    def check_args_tester(self, args, expected_fail=True,
                          expected_stdout=None, expected_stderr=None):
        """Reusable method to test the check_args() method."""
        parser = executable.setup_parser()

        # Capture stdout/stderr temporarily
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        temp_stdout = six.StringIO()
        temp_stderr = six.StringIO()
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

    def test_merge_option(self):
        """Test parsing of multiple different merge options."""
        args = ['--workdir', '/tmp/workdir', '--state', 'merge',
                '--patch', 'patch.txt',
                '--pw', 'http://patchwork.example.com/patch/1',
                '--merge-ref', 'git://example.com/repo1']
        parser = executable.setup_parser()
        args = parser.parse_args(args)
        cfg = executable.load_config(args)
        # Check that ordered mixture of merge arguments
        self.assertEqual('patch', cfg['merge_queue'][0][0])
        self.assertEqual('pw', cfg['merge_queue'][1][0])
        self.assertEqual('merge_ref', cfg['merge_queue'][2][0])
        # Check the content patch of ordered mixture merge arguments
        self.assertEqual('patch.txt', cfg['merge_queue'][0][1])
        self.assertEqual('http://patchwork.example.com/patch/1',
                         cfg['merge_queue'][1][1])
        self.assertEqual('git://example.com/repo1', cfg['merge_queue'][2][1])

    def test_load_config(self):
        """Test load_config() with some arguments."""
        config_file = [
            '[config]',
            'foo=bar',
            'workdir=/tmp/workdir',
            'basecfg=.config',
            'buildconf=value',
            'tarpkg=value',
            '[merge-1]',
            'url = repourl',
            'ref = master'
        ]
        args = ['--rc', '/tmp/testing.ini', '--workdir', '/tmp/workdir',
                '--state', '--junit', '/tmp/junit', 'report', '--reporter',
                'stdio', '--result', '/tmp/state.txt']
        cfg = self.load_config_tester(config_file, args)
        self.assertEqual('bar', cfg['foo'])
        self.assertEqual('report', cfg['_name'])

    def test_load_config_reporter_args(self):
        """Test load_config() with reporter arguments."""
        config_file = []
        args = ['--rc', '/tmp/testing.ini', 'report', '--reporter', 'mail',
                '--mail-to', 'someone@example.com', '--mail-from',
                'sender@example.com']
        cfg = self.load_config_tester(config_file, args)
        self.assertEqual('mail', cfg['type'])

    def test_load_config_reporter_config(self):
        """Test load_config() with reporter in the config file."""
        # pylint: disable=invalid-name
        config_file = [
            '[reporter]',
            'type=stdio',
        ]
        args = ['--rc', '/tmp/testing.ini', 'report']
        cfg = self.load_config_tester(config_file, args)
        self.assertTupleEqual(('type', 'stdio'), cfg['reporter'][0])

    def test_load_config_runner_args(self):
        """Test load_config() with runner arguments."""
        config_file = []
        args = ['--rc', '/tmp/testing.ini', '--workdir', '/tmp/workdir',
                '--state', 'run', '--runner', 'myrunner', '[\'value\']']
        cfg = self.load_config_tester(config_file, args)
        self.assertListEqual(['myrunner', ['value']], cfg['runner'])

    def test_load_config_with_state_arg(self):
        """Test load_config() with state."""
        config_file = [
            '[state]',
            'jobid_01=J:123456',
            'jobid_02=J:234567',
            'mergerepo_01=git://example.com/repo1',
            'mergerepo_02=git://example.com/repo2',
            'mergehead_01=master',
            'mergehead_02=master',
            'localpatch_01=/tmp/patch1.txt',
            'localpatch_02=/tmp/patch2.txt',
            'patchwork_01=http://patchwork.example.com/patch/1',
            'patchwork_02=http://patchwork.example.com/patch/2',
            'workdir=/tmp/workdir2',
            'some_other_state=some_value',
            '[publisher]',
            'type=mypublisher',
            'destination=/tmp/publish',
            'baseurl=http://example.com/publish',
            '[runner]',
            'type=myrunner',
            'jobtemplate=mytemplate.xml',
        ]
        args = ['--rc', '/tmp/lolwut.ini', '--workdir', '/tmp/workdir',
                '--state', 'merge']
        cfg = self.load_config_tester(config_file, args)

        # Check that state was retrieved from the config file
        self.assertSetEqual(set([u'J:123456', u'J:234567']), cfg['jobs'])
        self.assertListEqual(
            ['master', 'master'],
            cfg['mergeheads']
        )
        self.assertListEqual(
            ['git://example.com/repo1', 'git://example.com/repo2'],
            cfg['mergerepos']
        )
        self.assertListEqual(
            ['/tmp/patch1.txt', '/tmp/patch2.txt'],
            cfg['localpatches']
        )
        self.assertListEqual(
            [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            cfg['patchworks']
        )
        self.assertEqual('some_value', cfg['some_other_state'])

"""
Test cases for reporter module.
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
import StringIO
import os
import re
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as etree

from contextlib import contextmanager

import responses

import mock

from skt import reporter

from tests import misc

SCRIPT_PATH = os.path.dirname(__file__)


class TestConsoleLog(unittest.TestCase):
    """Test cases for reporter.ConsoleLog class"""
    @staticmethod
    @contextmanager
    def request_get_mocked(filename):
        """Mock request.get to allow feeding ConsoleLog with known inputs. When
        request.get is called, it "fetches" the content of the asset passed as
        parameter.

        Args:
            filename: Asset's filename.
        """
        get_mocked = mock.Mock()

        def remove_nt_marker(line):
            """Filter function for removing the 'nt ' markers on assets"""
            return not re.match(r'^nt ', line)
        get_mocked.text = filter(remove_nt_marker,
                                 misc.get_asset_content(filename))
        with mock.patch('requests.get', mock.Mock(return_value=get_mocked)):
            yield

    @staticmethod
    def get_expected_traces(filename):
        """Return expected traces from an asset. Each line started with 'nt '
        is discarded.

        Args:
            filename: Asset's filename.
        Returns:
            A list where every member is a trace.
        """
        expected_traces = []
        tmp_trace = []
        for line in misc.get_asset_content(filename).splitlines()[1:]:
            if line.startswith('nt '):
                if tmp_trace:
                    expected_traces.append('\n'.join(tmp_trace))
                    tmp_trace = []
            else:
                tmp_trace.append(line)
        expected_traces.append('\n'.join(tmp_trace))
        return expected_traces

    def test_kernel_version_unmatch(self):
        """Check it doesn't catch any trace when kernel version doesn't
        match.
        """
        with self.request_get_mocked('x86_one_trace.txt'):
            consolelog = reporter.ConsoleLog('4-4', 'someurl')
            traces = consolelog.gettraces()
        self.assertListEqual(traces, [])

    def test_match_one_trace(self):
        """Check one trace can be extracted from a console log"""
        with self.request_get_mocked('x86_one_trace.txt'):
            consolelog = reporter.ConsoleLog('4-5-fake', 'someurl')
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 1)
        expected_trace = self.get_expected_traces('x86_one_trace.txt')[0]
        self.assertEqual(expected_trace, traces[0])

    def test_match_three_traces(self):
        """Check three traces can be extracted from a console log"""
        with self.request_get_mocked('x86_three_traces.txt'):
            consolelog = reporter.ConsoleLog('4.16-fake', 'someurl')
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 3)
        expected_traces = self.get_expected_traces('x86_three_traces.txt')
        for idx, trace in enumerate(traces):
            msg = ("Trace_{} doesn't match.\n"
                   "{!r} != {!r}").format(idx, trace, expected_traces[idx])
            self.assertEqual(trace, expected_traces[idx], msg=msg)


class TestStdioReporter(unittest.TestCase):
    """Test the StdioReporter class."""

    # pylint: disable=too-many-instance-attributes

    def setUp(self):
        """Set up test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.tempconfig = "{}/.config".format(self.tmpdir)
        with open(self.tempconfig, 'w') as fileh:
            fileh.write('Config file text from a file')

        self.mergelog = "{}/mergelog".format(self.tmpdir)
        with open(self.mergelog, 'w') as fileh:
            for counter in range(0, 10):
                fileh.write(
                    'Merge log failure sample text line {}\n'.format(counter)
                )

        self.buildlog = "{}/buildlog".format(self.tmpdir)
        with open(self.buildlog, 'w') as fileh:
            for counter in range(0, 10):
                fileh.write(
                    'Build log failure sample text line {}\n'.format(counter)
                )

        self.mbox_side_effect = [
            'http://patchwork.example.com/patch/1/mbox',
            'http://patchwork.example.com/patch/2/mbox',
            'http://patchwork.example.com/patch/1/mbox',
            'http://patchwork.example.com/patch/2/mbox',
            'http://patchwork.example.com/patch/1/mbox',
            'http://patchwork.example.com/patch/2/mbox',
            'http://patchwork.example.com/patch/1/mbox',
            'http://patchwork.example.com/patch/2/mbox',
        ]
        self.name_side_effect = [
            '[1/2] I fixed a great thing',
            '[2/2] I fixed this other thing too',
            '[1/2] I fixed a great thing',
            '[2/2] I fixed this other thing too',
            '[1/2] I fixed a great thing',
            '[2/2] I fixed this other thing too',
            '[1/2] I fixed a great thing',
            '[2/2] I fixed this other thing too',
        ]

        beaker_pass_xml = (
            "{}/assets/beaker_job_pass_results_full.xml".format(SCRIPT_PATH)
        )
        with open(beaker_pass_xml, 'r') as fileh:
            self.beaker_pass_results = fileh.read()

        beaker_fail_xml = (
            "{}/assets/beaker_job_fail_results_full.xml".format(SCRIPT_PATH)
        )
        with open(beaker_fail_xml, 'r') as fileh:
            self.beaker_fail_results = fileh.read()

    def tearDown(self):
        """Tear down text fixtures."""
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    @responses.activate
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @mock.patch('skt.get_patch_name')
    @mock.patch('skt.get_patch_mbox')
    def test_jinja2_report(self, mock_mbox, mock_name, mock_grt):
        """Ensure the jinja2 report works."""
        mock_mbox.side_effect = self.mbox_side_effect
        mock_name.side_effect = self.name_side_effect
        mock_grt.return_value = etree.fromstring(self.beaker_pass_results)

        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Example console log"
        )

        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Example machine info"
        )

        cfg = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547021', 'J:2547022'],
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(cfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        self.assertIn(cfg['basehead'], report)
        self.assertIn(cfg['baserepo'], report)
        self.assertIn('Result: Pass', report)
        self.assertIn('Example machine info', report)
        # Triple newlines look bad since some lines get double spaced and
        # others don't.
        self.assertNotIn("\n\n\n", report)

    @responses.activate
    @mock.patch('skt.reporter.Reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @mock.patch('skt.get_patch_name')
    @mock.patch('skt.get_patch_mbox')
    def test_jinja2_multireport_success(self, mock_mbox, mock_name, mock_grt,
                                        mock_load_state_cfg):
        """Ensure the jinja2 report works."""
        mock_mbox.side_effect = list(self.mbox_side_effect)
        mock_name.side_effect = list(self.name_side_effect)
        mock_grt.return_value = etree.fromstring(self.beaker_pass_results)

        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )

        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Example machine info"
        )

        mock_state_cfg1 = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547021'],
            'kernel_arch': 'x86_64',
            'krelease': '3.10.0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        mock_state_cfg2 = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547022'],
            'kernel_arch': 's390x',
            'krelease': '3.10.0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        mock_load_state_cfg.side_effect = [
            mock_state_cfg1,
            mock_state_cfg2,
        ]

        cfg = {
            'result': ['state1', 'state2'],
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': ['http://patchwork.example.com/patch/1'],
            'jobs': ['J:2547021', 'J:2547022'],
            'krelease': '3.10.0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(cfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        self.assertIn(cfg['basehead'], report)
        self.assertIn(cfg['baserepo'], report)
        self.assertIn('Result: Pass', report)
        self.assertIn('Example machine info', report)
        # Triple newlines look bad since some lines get double spaced and
        # others don't.
        self.assertNotIn("\n\n\n", report)

    @responses.activate
    @mock.patch('skt.reporter.Reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @mock.patch('skt.get_patch_name')
    @mock.patch('skt.get_patch_mbox')
    def test_jinja2_multireport_failure(self, mock_mbox, mock_name, mock_grt,
                                        mock_load_state_cfg):
        """Ensure the jinja2 report works."""
        mock_mbox.side_effect = list(self.mbox_side_effect)
        mock_name.side_effect = list(self.name_side_effect)
        mock_grt.side_effect = [
            etree.fromstring(self.beaker_pass_results),
            etree.fromstring(self.beaker_fail_results),
        ]

        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )

        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Example machine info"
        )

        mock_state_cfg1 = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547021'],
            'kernel_arch': 'x86_64',
            'krelease': '3.10.0',
            'buildlog': self.buildlog,
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        mock_state_cfg2 = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547022'],
            'kernel_arch': 's390x',
            'krelease': '3.10.0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        mock_load_state_cfg.side_effect = [
            mock_state_cfg1,
            mock_state_cfg2,
        ]

        cfg = {
            'result': ['state1', 'state2'],
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'mergerepos': ['other_repo_name'],
            'mergeheads': ['fedcba4321'],
            'localpatches': ['/tmp/patch.txt', '/tmp/patch2.txt'],
            'patchworks': ['http://patchwork.example.com/patch/1'],
            'jobs': ['J:2547021', 'J:2547022'],
            'krelease': '3.10.0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(cfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        self.assertIn(cfg['basehead'], report)
        self.assertIn(cfg['baserepo'], report)
        self.assertIn('Result: Pass', report)
        self.assertIn('Result: Fail', report)
        self.assertIn('Example machine info', report)
        # Triple newlines look bad since some lines get double spaced and
        # others don't.
        self.assertNotIn("\n\n\n", report)

    @responses.activate
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @mock.patch('skt.get_patch_name')
    @mock.patch('skt.get_patch_mbox')
    def test_jinja2_baseline(self, mock_mbox, mock_name, mock_grt):
        """Ensure the jinja2 report works."""
        mock_mbox.side_effect = self.mbox_side_effect
        mock_name.side_effect = self.name_side_effect
        mock_grt.return_value = etree.fromstring(self.beaker_pass_results)

        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Example console log"
        )

        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Example machine info"
        )

        cfg = {
            'workdir': self.tmpdir,
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'jobs': ['J:2547021'],
            'retcode': '0',
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }
        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(cfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        self.assertIn(cfg['basehead'], report)
        self.assertIn(cfg['baserepo'], report)
        self.assertIn('Result: Pass', report)
        self.assertIn('Example machine info', report)
        # Triple newlines look bad since some lines get double spaced and
        # others don't.
        self.assertNotIn("\n\n\n", report)

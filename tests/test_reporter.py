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
"""Test cases for reporter module."""
import StringIO
import os
import shutil
import tempfile
import unittest

import xml.etree.ElementTree as etree

import mock
import responses

from skt import reporter


SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))


def read_asset(filename):
    """Read a test asset."""
    filename = "{}/assets/{}".format(SCRIPT_PATH, filename)
    with open(filename, 'r') as fileh:
        return fileh.read()


class TestReporterFunctions(unittest.TestCase):
    """Test cases for the functions in skt.reporter."""

    def setUp(self):
        """Test fixtures."""
        self.statefile = "{}/assets/testing_state.cfg".format(SCRIPT_PATH)
        self.statefile_no_runner = (
            "{}/assets/testing_state_no_runner.cfg".format(SCRIPT_PATH)
        )

    def test_load_state_cfg(self):
        """Test with a state file that has a runner section."""
        cfg = reporter.load_state_cfg(self.statefile)

        expected_cfg = {
            'jobid_01': 'J:123456',
            'jobs': set(['J:123456']),
            'localpatch_01': '/tmp/patch.txt',
            'localpatches': ['/tmp/patch.txt'],
            'mergehead_01': 'master',
            'mergeheads': ['master'],
            'mergerepo_01': 'git://git.kernel.org/pub/scm/linux/kernel/git/',
            'mergerepos': ['git://git.kernel.org/pub/scm/linux/kernel/git/'],
            'patchwork_01': 'http://example.com/patch/1',
            'patchworks': ['http://example.com/patch/1'],
            'runner': ['beaker', {'jobtemplate': 'test_template.xml'}]
        }
        self.assertDictEqual(expected_cfg, cfg)

    @mock.patch('logging.debug')
    def test_load_state_no_runner(self, mock_log):
        """Test with a state file that has no runner section."""
        cfg = reporter.load_state_cfg(self.statefile_no_runner)

        expected_cfg = {
            'foo': 'bar',
            'jobid_01': 'J:123456',
            'jobid_02': 'J:123456',
            'jobs': set(['J:123456']),
            'localpatch_01': '/tmp/patch.txt',
            'localpatch_02': '/tmp/patch.txt',
            'localpatches': ['/tmp/patch.txt', '/tmp/patch.txt'],
            'mergehead_01': 'master',
            'mergehead_02': 'master',
            'mergeheads': ['master', 'master'],
            'mergerepo_01': 'git://git.kernel.org/pub/scm/linux/kernel/git/',
            'mergerepo_02': 'git://git.kernel.org/pub/scm/linux/kernel/git/',
            'mergerepos': [
                'git://git.kernel.org/pub/scm/linux/kernel/git/',
                'git://git.kernel.org/pub/scm/linux/kernel/git/'
            ],
            'patchwork_01': 'http://example.com/patch/1',
            'patchwork_02': 'http://example.com/patch/1',
            'patchworks': [
                'http://example.com/patch/1',
                'http://example.com/patch/1'
            ]
        }
        self.assertDictEqual(expected_cfg, cfg)
        mock_log.assert_called_once()


class TestStdioReporter(unittest.TestCase):
    """Test cases for StdioReporter class."""

    def setUp(self):
        """Set up test fixtures."""
        # Write a kernel .config file
        self.tmpdir = tempfile.mkdtemp()
        self.tempconfig = "{}/.config".format(self.tmpdir)
        with open(self.tempconfig, 'w') as fileh:
            fileh.write('Config file text from a file')

        # Load our sample Beaker XML files
        self.beaker_pass_results = etree.fromstring(
            read_asset("beaker_job_pass_results_full.xml")
        )
        self.beaker_pass_ltp_results = etree.fromstring(
            read_asset("beaker_job_pass_ltp_results_full.xml")
        )
        self.beaker_fail_results = etree.fromstring(
            read_asset("beaker_job_fail_results_full.xml")
        )
        self.beaker_fail_ltp_results = etree.fromstring(
            read_asset("beaker_job_fail_ltp_results_full.xml")
        )

        # Set up a base config dictionary that we can adjust and use for tests
        self.basecfg = {
            'workdir': self.tmpdir,
            'krelease': '3.10.0',
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547021'],
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            )
        }

    def tearDown(self):
        """Tear down text fixtures."""
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def make_file(self, filename, content="Test file"):
        """Create test files, such as a logs, configs, etc."""
        tempfilename = "{}/{}".format(self.tmpdir, filename)
        with open(tempfilename, 'w') as fileh:
            fileh.write(content)

        return tempfilename

    @responses.activate
    def test_merge_failure(self):
        """Verify stdio report works with a merge failure.

        Test that log/txt attachments are properly printed as well.
        """
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )

        self.basecfg['mergelog'] = self.make_file(
            'merge.log', 'merge failed\nThe copy of the patch'
        )

        # The kernel config should not be present after a merge failure.
        if os.path.isfile(self.tempconfig):
            os.unlink(self.tempconfig)

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.attach.append(('example.log', 'just an example'))
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: FAIL: Patch application failed',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'application of the last patch above failed',
            'merge failed',
            'because of the error message stated above',
            'example.log\njust an example'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @responses.activate
    def test_merge_failure_empty_log(self):
        """Verify stdio report works with a merge failure w/empty log."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )

        self.basecfg['mergelog'] = self.make_file(
            'merge.log', ''
        )

        # The kernel config should not be present after a merge failure.
        if os.path.isfile(self.tempconfig):
            os.unlink(self.tempconfig)

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: FAIL: Patch application failed',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'application of the last patch above failed',
            'because of the error message stated above',
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @responses.activate
    def test_build_failure(self):
        """Verify stdio report works with a build failure."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )

        self.basecfg['buildlog'] = self.make_file('build.log', 'build failed')

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: FAIL: Build failed',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'However, the build failed.',
            'build.log.gz',
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_run_failure(self, mock_grt):
        """Verify stdio report works with a failed run."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_fail_results

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: FAIL: Report for kernel 3.10.0',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'Test run #1\nResult: Fail',
            'see attached console log',
            '01_console.log.gz',
            'Machine information from beaker goes here'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_run_success_multiple_patches(self, mock_grt):
        """Verify stdio report works with success + multiple patches."""
        # pylint: disable=invalid-name
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )

        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_pass_results
        self.basecfg['retcode'] = '0'

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Report for kernel 3.10.0',
            'We applied the following patches:',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'Test run #1\nResult: Pass',
            'Machine information from beaker goes here'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_run_success_single_patch(self, mock_grt):
        """Verify stdio report works with a single patch."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )

        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_pass_results
        self.basecfg['retcode'] = '0'
        self.basecfg['localpatches'] = []
        self.basecfg['patchworks'] = ["http://patchwork.example.com/patch/1"]

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Report for kernel 3.10.0',
            'We applied the following patch:',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'Test run #1\nResult: Pass',
            'Machine information from beaker goes here'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @responses.activate
    def test_run_success_no_runner(self):
        """Verify stdio report works without a runner.

        Test the case of no 'mergerepos' set and with 'cfgurl' for better
        coverage.

        """
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        responses.add(
            responses.GET,
            "http://example.com/config",
            body="Config from configurl"
        )

        self.basecfg['retcode'] = '0'
        self.basecfg['cfgurl'] = "http://example.com/config"
        del self.basecfg['krelease']
        del self.basecfg['runner']

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Report',
            'We applied the following patches:',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_baseline_success(self, mock_grt):
        """Verify stdio report works with a successful baseline run."""
        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_pass_results
        self.basecfg['retcode'] = '0'
        self.basecfg['localpatches'] = []
        self.basecfg['patchworks'] = []

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Report for kernel 3.10.0',
            'checked out {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'Test run #1\nResult: Pass',
            'Machine information from beaker goes here'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_multireport_success(self, mock_grt, mock_load):
        """Verify multireport success output."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_pass_results

        self.basecfg['retcode'] = '0'
        self.basecfg['result'] = ['state1', 'state2']

        # Create our two mocked state files for two different arches
        state1 = self.basecfg.copy()
        state1['kernel_arch'] = 's390x'
        state2 = self.basecfg.copy()
        state2['kernel_arch'] = 'x86_64'

        # Mock the loading of these state files
        mock_load.side_effect = [state1, state2]

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'PASS: Report for kernel 3.10.0',
            'All builds and tests PASSED.',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'results for s390x architecture',
            'results for x86_64 architecture',
            'Test run #1\nResult: Pass',
            'Machine information from beaker goes here'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_multireport_failure(self, mock_grt, mock_load):
        """Verify multireport failure output."""
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        url_base = "https://beaker.example.com/recipes/5290731"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/74019100/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_fail_ltp_results

        self.basecfg['retcode'] = '1'
        self.basecfg['result'] = ['state1', 'state2']

        # Create our two mocked state files for two different arches
        state1 = self.basecfg.copy()
        state1['kernel_arch'] = 's390x'
        state2 = self.basecfg.copy()
        state2['kernel_arch'] = 'x86_64'

        # Mock the loading of these state files
        mock_load.side_effect = [state1, state2]

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'FAIL: Report for kernel 3.10.0',
            'Testing FAILED!',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'results for s390x architecture',
            'results for x86_64 architecture',
            'Test run #1\nResult: Fail',
            'Machine information from beaker goes here',
            'R:5290731',
            'https://beaker.example.com/recipes/5290731/tasks/74019102/'
            'logs/RHELKT1LITE.FILTERED.run.log',
            'https://beaker.example.com/recipes/5290731/tasks/74019102/'
            'results/358727247/logs/resultoutputfile.log',
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_multireport_partial_success(self, mock_grt, mock_load):
        """Verify multireport partial success output."""
        # pylint: disable=invalid-name
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/1/mbox",
            body="Subject: Patch #1"
        )
        responses.add(
            responses.GET,
            "http://patchwork.example.com/patch/2/mbox",
            body="Subject: Patch #2"
        )
        url_base = "https://beaker.example.com/recipes/5273166"
        responses.add(
            responses.GET,
            "{}/logs/console.log".format(url_base),
            body="Linux version 3.10.0"
        )
        responses.add(
            responses.GET,
            "{}/tasks/73795444/logs/machinedesc.log".format(url_base),
            body="Machine information from beaker goes here"
        )
        mock_grt.side_effect = [
            self.beaker_fail_results,
            self.beaker_fail_results,
            self.beaker_pass_results,
            self.beaker_pass_results,
        ]

        self.basecfg['retcode'] = '1'
        self.basecfg['result'] = ['state1', 'state2']

        # Create our two mocked state files for two different arches
        state1 = self.basecfg.copy()
        state1['kernel_arch'] = 's390x'
        state2 = self.basecfg.copy()
        state2['kernel_arch'] = 'x86_64'

        # Mock the loading of these state files
        mock_load.side_effect = [state1, state2]

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'FAIL: Report for kernel 3.10.0',
            'Testing FAILED!',
            'commit {}'.format(self.basecfg['basehead']),
            self.basecfg['baserepo'],
            'results for s390x architecture',
            'results for x86_64 architecture',
            'Test run #1\nResult: Pass',
            'Test run #1\nResult: Fail',
            'Machine information from beaker goes here',
            'config_s390x.gz',
            'config_x86_64.gz',
            'see attached console log',
            '01_console.log.gz'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

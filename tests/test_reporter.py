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

from defusedxml.ElementTree import fromstring

import mock
import responses

from skt import reporter
from tests.misc import fake_has_soaking


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
    # pylint: disable=too-many-instance-attributes

    def setUp(self):
        """Set up test fixtures."""
        self.mock_redis = mock.patch('redis.Redis',
                                     lambda *args, **kwargs: None)
        self.mock_redis.start()
        # return no soaking info for anything by default
        self.mock_soak_wrap = mock.patch('skt.misc.SoakWrap.has_soaking',
                                         lambda x, y: None)
        self.mock_soak_wrap.start()
        # Write a kernel .config file
        self.tmpdir = tempfile.mkdtemp()
        self.tempconfig = "{}/.config".format(self.tmpdir)
        with open(self.tempconfig, 'w') as fileh:
            fileh.write('Config file text from a file')

        # Load our sample Beaker XML files
        self.beaker_pass_results = fromstring(
            read_asset("beaker_recipe_set_results.xml")
        )
        self.beaker_fail_results = fromstring(
            read_asset("beaker_recipe_set_fail_results.xml")
        )
        self.beaker_panic_results = fromstring(
            read_asset("beaker_recipe_set_panic_results.xml")
        )

        # Set up a base config dictionary that we can adjust and use for tests
        self.basecfg = {
            'workdir': self.tmpdir,
            'template': 'full',
            'krelease': '3.10.0',
            'baserepo': 'git://git.example.com/kernel.git',
            'basehead': '1234abcdef',
            'kernel_arch': 'x86_64',
            'patchworks': [
                'http://patchwork.example.com/patch/1',
                'http://patchwork.example.com/patch/2'
            ],
            'jobs': ['J:2547021'],
            'recipe_sets': ['RS:123456'],
            'runner': (
                'beaker', {
                    'jobtemplate': 'foo',
                    'jobowner': 'mhayden'
                }
            ),
            'soak': 'True'
        }
        mock_env_vars = {
            'REDIS_SERVICE': 'MY_REDIS',
            'MY_REDIS_SERVICE_HOST': '127.0.0.1',
            'MY_REDIS_SERVICE_PORT': '6379',
            'GITLAB_PRIVATE_TOKEN': 'secrete',
        }
        self.mock_env = mock.patch.dict('os.environ', mock_env_vars)
        self.mock_env.start()

    def tearDown(self):
        """Tear down text fixtures."""
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir)

        self.mock_soak_wrap.stop()
        self.mock_env.stop()

        self.mock_redis.stop()

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
            'Overall result: FAILED',
            'Patch merge: FAILED',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            'When we attempted to merge the patches, we received an error:',
            'merge failed',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
            'Overall result: FAILED',
            'Patch merge: FAILED',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            'When we attempted to merge the patches, we received an error:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
            'Overall result: FAILED',
            'Patch merge: OK',
            'Compile: FAILED',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            'We compiled the kernel for 1 architecture:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )

        mock_grt.return_value = self.beaker_fail_results
        self.basecfg['retcode'] = '1'

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: FAIL: Test report for kernel 3.10.0 (kernel)',
            'Overall result: FAILED',
            'Patch merge: OK',
            'Compile: OK',
            'Kernel tests: FAILED',
            'Beaker results:',
            'https://beaker.engineering.redhat.com/jobs/2547021',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            'We compiled the kernel for 1 architecture:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_run_soak_hidden(self, mock_grt):
        """ Verify stdio report works and that soaking tests are not
            present in results and that failure on test soaking doesn't
            mean a failed run."""
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )

        # Expected values:
        # the return xml has fails, but retcode is 0, because soaking tests
        # are hidden
        mock_grt.return_value = self.beaker_fail_results
        self.basecfg['retcode'] = '0'

        testprint = StringIO.StringIO()

        with mock.patch('skt.misc.SoakWrap.has_soaking', fake_has_soaking):
            rptclass = reporter.StdioReporter(self.basecfg)
            rptclass.report(printer=testprint)

            report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Test report for kernel 3.10.0 (kernel)',
            'Overall result: PASSED',
            'Patch merge: OK',
            'Compile: OK',
            'Kernel tests: OK',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            'We compiled the kernel for 1 architecture:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
        ]

        # this mustn't be in report, it's soaking and hidden
        missing_strings = [
            '/test/we/ran',
            'Beaker results:',
            'https://beaker.engineering.redhat.com/jobs/2547021'
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

        for should_miss in missing_strings:
            self.assertNotIn(should_miss, report)

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
        responses.add(responses.GET,
                      'http://example.com/',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )

        mock_grt.return_value = self.beaker_pass_results
        self.basecfg['retcode'] = '0'

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Test report for kernel 3.10.0 (kernel)',
            'Overall result: PASSED',
            'Patch merge: OK',
            'Compile: OK',
            'Kernel tests: OK',
            'Repo: git://git.example.com/kernel.git',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            'We compiled the kernel for 1 architecture:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
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
            'Subject: PASS: Test report for kernel 3.10.0 (kernel)',
            'Overall result: PASSED',
            'Patch merge: OK',
            'Compile: OK',
            'Kernel tests: OK',
            'Kernel repo: git://git.example.com/kernel.git',
            'We compiled the kernel for 1 architecture:',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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

        self.basecfg['cfgurl'] = "http://example.com/config"
        del self.basecfg['runner']

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'Subject: PASS: Test report',
            'Commit: 1234abcdef None',
            'Kernel repo: git://git.example.com/kernel.git',
            'Overall result: PASS',
            'Patch merge: OK',
            'Compile: OK',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            'We then merged the following patches with `git am`:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_baseline_success(self, mock_grt):
        """Verify stdio report works with a successful baseline run."""
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
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
            'Subject: PASS: Test report for kernel 3.10.0 (kernel)',
            'Overall result: PASS',
            'Patch merge: OK',
            'Compile: OK',
            'Kernel tests: OK',
            'Kernel repo: git://git.example.com/kernel.git',
            'We compiled the kernel for 1 architecture:',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
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
            'PASS: Test report for kernel 3.10.0 (kernel)',
            'Overall result: PASSED',
            'Patch merge: OK',
            'Compile: OK',
            'Repo: git://git.example.com/kernel.git',
            'Kernel tests: OK',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            'We compiled the kernel for 2 architectures:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_fail_results

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
            'FAIL: Test report for kernel 3.10.0 (kernel)',
            'Overall result: FAILED',
            'Kernel tests: FAILED',
            's390x: FAILED',
            'x86_64: FAILED',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            'We compiled the kernel for 2 architectures:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

    @mock.patch('skt.reporter.load_state_cfg')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @responses.activate
    def test_multireport_panic(self, mock_grt, mock_load):
        """Verify multireport works with a kernel panic result."""
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )
        mock_grt.return_value = self.beaker_panic_results

        self.basecfg['retcode'] = '1'
        self.basecfg['result'] = ['state']

        state = self.basecfg.copy()
        state['kernel_arch'] = 'x86_64'
        mock_load.side_effect = [state]

        testprint = StringIO.StringIO()
        rptclass = reporter.StdioReporter(self.basecfg)
        rptclass.report(printer=testprint)
        report = testprint.getvalue().strip()

        required_strings = [
            'FAIL: Test report for kernel 3.10.0 (kernel)',
            'Overall result: FAILED',
            'Kernel tests: FAILED',
            'x86_64: FAILED',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            'http://example.com/console',
            'We compiled the kernel for 1 architecture:',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
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
        responses.add(responses.GET,
                      'http://example.com',
                      body="Linux version 3.10.0")
        responses.add(
            responses.GET,
            "http://example.com/machinedesc.log",
            body="Machine information from beaker goes here"
        )
        mock_grt.side_effect = [
            self.beaker_fail_results,
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
            'FAIL: Test report for kernel 3.10.0 (kernel)',
            'Overall result: FAILED',
            'Kernel tests: FAILED',
            's390x: FAILED',
            'x86_64: PASSED',
            'http://patchwork.example.com/patch/1',
            'http://patchwork.example.com/patch/2',
            '- URL: https://github.com/CKI-project/tests-beaker/',
            'distribution/kpkginstall',
            '/test/we/ran',
            'We compiled the kernel for 2 architectures:',
            'Beaker results:',
            'https://beaker.engineering.redhat.com/jobs/2547021',
            self.basecfg['basehead'],
            self.basecfg['baserepo'],
        ]
        for required_string in required_strings:
            self.assertIn(required_string, report)

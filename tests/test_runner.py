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
import os
import unittest

import xml.etree.ElementTree as etree
import mock

from skt import runner

from tests import misc

SCRIPT_PATH = os.path.dirname(__file__)

DEFAULT_ARGS = {
    'jobtemplate': '{}/assets/test.xml'.format(SCRIPT_PATH)
}


class TestRunner(unittest.TestCase):
    """Test cases for runner module."""
    # (Too many public methods) pylint: disable=too-many-public-methods

    def setUp(self):
        """Set up test fixtures"""
        self.myrunner = runner.BeakerRunner(**DEFAULT_ARGS)
        with open('{}/assets/test.xml'.format(SCRIPT_PATH), 'r') as fileh:
            self.test_xml = fileh.read()

        self.max_aborted = 3

    def test_getrunner(self):
        """Ensure getrunner() can create a runner subclass."""
        result = runner.getrunner('beaker', {'jobtemplate': 'test'})
        self.assertIsInstance(result, runner.BeakerRunner)

    def test_invalid_getrunner(self):
        """Ensure getrunner() throws an exception for an invalid runner."""
        bad_runner = 'pizza'
        with self.assertRaises(Exception) as context:
            runner.getrunner(bad_runner, {'jobtemplate': 'test'})

        self.assertIn(
            "Unknown runner type: {}".format(bad_runner),
            context.exception
        )

    def test_getxml(self):
        """Ensure BeakerRunner.__getxml() returns xml."""
        # pylint: disable=W0212,E1101
        result = self.myrunner._BeakerRunner__getxml({})
        self.assertEqual(result, self.test_xml)

    def test_getxml_replace(self):
        """Ensure BeakerRunner.__getxml() returns xml with replacements."""
        # pylint: disable=W0212,E1101
        result = self.myrunner._BeakerRunner__getxml({'KVER': 'kernel-4.16'})
        expected_xml = self.test_xml.replace("##KVER##", "kernel-4.16")
        self.assertEqual(result, expected_xml)

    def test_getxml_multi_replace(self):
        """
        Ensure BeakerRunner.__getxml() returns xml with multi-instance
        replacements.
        """
        # pylint: disable=W0212,E1101
        result = self.myrunner._BeakerRunner__getxml({'ARCH': 's390x'})
        expected_xml = self.test_xml.replace("##ARCH##", "s390x")
        self.assertEqual(result, expected_xml)

    @mock.patch('subprocess.Popen')
    def test_getresultstree(self, mock_popen):
        """Ensure getresultstree() works."""
        test_xml = "<xml><test>TEST</test></xml>"
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (test_xml, '')
        result = self.myrunner.getresultstree(jobid=0)
        self.assertEqual(next(x.text for x in result.iter('test')), 'TEST')

    def test_forget_cid_withj(self):
        """Ensure __forget_cid() works with jobs."""
        # pylint: disable=protected-access,E1101
        self.myrunner.jobs = ["J:00001"]
        self.myrunner.job_to_recipe_map = {"J:00001": ["R:00001"]}
        result = self.myrunner._BeakerRunner__forget_cid("J:00001")
        self.assertIsNone(result)
        self.assertEqual(self.myrunner.jobs, [])

    def test_forget_cid_withr(self):
        """Ensure __forget_cid() works with recipes."""
        # pylint: disable=protected-access,E1101
        self.myrunner.jobs = ["J:00001"]
        self.myrunner.job_to_recipe_map = {"J:00001": ["R:00001"]}
        result = self.myrunner._BeakerRunner__forget_cid("R:00001")
        self.assertIsNone(result)

    def test_forget_cid_bad_job(self):
        """Ensure __forget_cid() fails with an invalid taskspec."""
        # pylint: disable=protected-access,E1101
        with self.assertRaises(Exception) as context:
            self.myrunner._BeakerRunner__forget_cid("OHCOMEON:00001")

        self.assertIn(
            "Unknown cid type: OHCOMEON:00001",
            context.exception
        )

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_getverboseresults(self, mock_getresultstree):
        """Ensure getverboseresults() works"""
        # Mock up a beaker XML reply
        mocked_xml = misc.get_asset_content('beaker_results.xml')
        mock_getresultstree.return_value = etree.fromstring(mocked_xml)

        result = self.myrunner.getverboseresults(["R:00001"])
        expected_result = {
            'R:00001': {
                'R:None': (
                    None,
                    None,
                    'http://example.com/',
                    'http://example.com/machinedesc.log',
                    'http://example.com/lshw.log',
                    []
                ),
                'result': 'Pass',
                'status': 'Completed'
            }
        }
        self.assertDictEqual(result, expected_result)

    def test_getresults(self):
        """Ensure __getresults() works."""
        # pylint: disable=W0212,E1101
        result = self.myrunner._BeakerRunner__getresults()
        self.assertEqual(result, 0)

    @mock.patch('logging.warning')
    def test_getresults_failure(self, mock_logging):
        """Ensure __getresults() handles a job failure."""
        # pylint: disable=W0212,E1101

        # Ensure that the failure loop hits 'continue' which doesn't change
        # the result
        self.myrunner.failures = {
            'test': ['A', set(), 4]
        }
        result = self.myrunner._BeakerRunner__getresults()
        self.assertEqual(result, 0)

        # Go through the failure loop with one failed host
        self.myrunner.failures = {
            'test': [['A'], set([('result', 'status')]), 1]
        }
        result = self.myrunner._BeakerRunner__getresults()
        self.assertEqual(result, 1)

        # Go through the failure loop with multiple failed hosts
        self.myrunner.failures = {
            'test': [['A', 'B'], set([('result', 'status')]), 1]
        }
        result = self.myrunner._BeakerRunner__getresults()
        self.assertEqual(result, 1)
        mock_logging.assert_called()

    def test_recipe_to_job(self):
        """Ensure __recipe_to_job() works."""
        # pylint: disable=W0212,E1101
        beaker_xml = misc.get_asset_content('beaker_results.xml')
        xml_parsed = etree.fromstring(beaker_xml)

        result = self.myrunner._BeakerRunner__recipe_to_job(xml_parsed)
        self.assertEqual(result.tag, 'job')

        result = self.myrunner._BeakerRunner__recipe_to_job(xml_parsed,
                                                            samehost=True)
        self.assertEqual(result.tag, 'job')

    @mock.patch('skt.runner.BeakerRunner._BeakerRunner__jobsubmit')
    def test_run(self, mock_jobsubmit):
        """Ensure BeakerRunner.run works."""
        url = "http://machine1.example.com/builds/1234567890.tar.gz"
        release = "4.17.0-rc1"
        wait = False

        mock_jobsubmit.return_value = "J:0001"

        result = self.myrunner.run(url, self.max_aborted, release, wait)
        self.assertEqual(result, (0, ''))

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    @mock.patch('skt.runner.BeakerRunner._BeakerRunner__jobsubmit')
    def test_run_wait(self, mock_jobsubmit, mock_getresultstree):
        """Ensure BeakerRunner.run works."""
        url = "http://machine1.example.com/builds/1234567890.tar.gz"
        release = "4.17.0-rc1"
        wait = True

        beaker_xml = misc.get_asset_content('beaker_pass_results.xml')
        mock_getresultstree.return_value = etree.fromstring(beaker_xml)
        mock_jobsubmit.return_value = "J:0001"

        # no need to wait 60 seconds
        # though beaker_pass_results.xml only needs one iteration
        self.myrunner.watchdelay = 0.1
        result = self.myrunner.run(url, self.max_aborted, release, wait)
        self.assertEqual(result, (0, ''))

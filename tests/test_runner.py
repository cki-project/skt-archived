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
import os
import tempfile
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
    """Test cases for runner module"""

    def setUp(self):
        """Set up test fixtures"""
        self.myrunner = runner.BeakerRunner(**DEFAULT_ARGS)
        with open('{}/assets/test.xml'.format(SCRIPT_PATH), 'r') as fileh:
            self.test_xml = fileh.read()

    def test_getrunner(self):
        """Ensure getrunner() can create a runner subclass"""
        result = runner.getrunner('beaker', {'jobtemplate': 'test'})
        self.assertIsInstance(result, runner.BeakerRunner)

    def test_invalid_getrunner(self):
        """Ensure getrunner() throws an exception for an invalid runner"""
        bad_runner = 'pizza'
        with self.assertRaises(Exception) as context:
            runner.getrunner(bad_runner, {'jobtemplate': 'test'})

        self.assertIn(
            "Unknown runner type: {}".format(bad_runner),
            context.exception
        )

    def test_getxml(self):
        """Ensure BeakerRunner.getxml() returns xml"""
        result = self.myrunner.getxml({})
        self.assertEqual(result, self.test_xml)

    def test_getxml_replace(self):
        """Ensure BeakerRunner.getxml() returns xml with replacements"""
        result = self.myrunner.getxml({'KVER': 'kernel-4.16'})
        expected_xml = self.test_xml.replace("##KVER##", "kernel-4.16")
        self.assertEqual(result, expected_xml)

    def test_getxml_multi_replace(self):
        """
        Ensure BeakerRunner.getxml() returns xml with multi-instance
        replacements.
        """
        result = self.myrunner.getxml({'ARCH': 's390x'})
        expected_xml = self.test_xml.replace("##ARCH##", "s390x")
        self.assertEqual(result, expected_xml)

    @mock.patch('subprocess.Popen')
    def test_getresultstree(self, mock_popen):
        """Ensure getresultstree() works"""
        test_xml = "<xml><test>TEST</test></xml>"
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (test_xml, '')
        result = self.myrunner.getresultstree(jobid=0)
        self.assertEqual(next(x.text for x in result.iter('test')), 'TEST')

    @mock.patch('subprocess.Popen')
    def test_dumpjunitresults(self, mock_popen):
        """Ensure dumpjunitresults() works"""
        mock_popen.return_value.returncode = 0
        mock_popen.return_value.communicate.return_value = (
            'stdout',
            'stderr'
        )

        junit_dir = tempfile.mkdtemp()
        self.myrunner.dumpjunitresults("J:00001", junit_dir)
        expected_file = "{}/j_00001.xml".format(junit_dir)
        self.assertTrue(os.path.exists(expected_file))

        # Clean up
        os.unlink(expected_file)
        os.rmdir(junit_dir)

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_getconsolelog(self, mock_getresultstree):
        """Ensure getconsolelog() works"""
        # Mock up a beaker XML reply
        mocked_xml = misc.get_asset_content('beaker_results.xml')
        mock_getresultstree.return_value = etree.fromstring(mocked_xml)

        result = self.myrunner.getconsolelog()
        self.assertEqual(result, "http://example.com/")

    def test_forget_cid_withj(self):
        """Ensure _forget_cid() works with jobs"""
        # pylint: disable=protected-access
        self.myrunner.jobs = ["J:00001"]
        self.myrunner.j2r = {"J:00001": ["R:00001"]}
        self.myrunner.recipes = ["R:00001"]
        result = self.myrunner._forget_cid("J:00001")
        self.assertIsNone(result)
        self.assertEqual(self.myrunner.jobs, [])
        self.assertEqual(self.myrunner.recipes, [])

    def test_forget_cid_withr(self):
        """Ensure _forget_cid() works with recipes"""
        # pylint: disable=protected-access
        self.myrunner.jobs = ["J:00001"]
        self.myrunner.j2r = {"J:00001": ["R:00001"]}
        self.myrunner.recipes = ["R:00001"]
        result = self.myrunner._forget_cid("R:00001")
        self.assertIsNone(result)
        self.assertEqual(self.myrunner.recipes, [])

    def test_forget_cid_bad_job(self):
        """Ensure _forget_cid() fails with an invalid taskspec"""
        # pylint: disable=protected-access
        with self.assertRaises(Exception) as context:
            self.myrunner._forget_cid("OHCOMEON:00001")

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
                    'http://example.com/lshw.log'
                ),
                'result': 'Pass'
            }
        }
        self.assertDictEqual(result, expected_result)

    def test_get_mfhost(self):
        """Ensure get_mfhost() works"""
        self.myrunner.failures = {'test': ['a', 'b']}
        result = self.myrunner.get_mfhost()
        self.assertEqual(result, 'a')

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_jobresult(self, mock_getresultstree):
        """Ensure jobresult() works"""
        # beaker_xml up a beaker XML reply
        beaker_xml = misc.get_asset_content('beaker_results.xml')
        mock_getresultstree.return_value = etree.fromstring(beaker_xml)

        result = self.myrunner.jobresult("J:00001")
        self.assertTupleEqual(result, (0, 'Pass'))

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_jobresult_failure(self, mock_getresultstree):
        """Ensure jobresult() handles a job failure"""
        # Mock up a beaker XML reply
        beaker_xml = misc.get_asset_content('beaker_fail_results.xml')
        mock_getresultstree.return_value = etree.fromstring(beaker_xml)

        result = self.myrunner.jobresult("J:00001")
        self.assertTupleEqual(result, (1, 'Fail'))

    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_getresults(self, mock_getresultstree):
        """Ensure getresults() works"""
        # Mock up a beaker XML reply
        beaker_xml = misc.get_asset_content('beaker_results.xml')
        mock_getresultstree.return_value = etree.fromstring(beaker_xml)

        result = self.myrunner.getresults("J:00001")
        self.assertEqual(result, 0)

    @mock.patch('logging.warning')
    @mock.patch('skt.runner.BeakerRunner.getresultstree')
    def test_getresults_failure(self, mock_getresultstree, mock_logging):
        """Ensure getresults() handles a job failure"""
        # Mock up a beaker XML reply
        beaker_xml = misc.get_asset_content('beaker_fail_results.xml')
        mock_getresultstree.return_value = etree.fromstring(beaker_xml)

        # Ensure that the failure loop hits 'continue'
        self.myrunner.failures = {
            'test': ['A', None, 4]
        }
        result = self.myrunner.getresults("J:00001")
        self.assertEqual(result, 1)

        # Go through the failure loop with one failed host
        self.myrunner.failures = {
            'test': [['A'], ['a'], 1]
        }
        result = self.myrunner.getresults("J:00001")
        self.assertEqual(result, 1)

        # Go through the failure loop with multiple failed hosts
        self.myrunner.failures = {
            'test': [['A', 'B'], ['a'], 1]
        }
        result = self.myrunner.getresults("J:00001")
        self.assertEqual(result, 1)
        mock_logging.assert_called()

    def test_recipe_to_job(self):
        """Ensure recipe_to_job() works"""
        beaker_xml = misc.get_asset_content('beaker_results.xml')
        xml_parsed = etree.fromstring(beaker_xml)

        result = self.myrunner.recipe_to_job(xml_parsed)
        self.assertEqual(result.tag, 'job')

        result = self.myrunner.recipe_to_job(xml_parsed, samehost=True)
        self.assertEqual(result.tag, 'job')

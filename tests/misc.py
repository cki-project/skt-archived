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
"""Miscellaneous for tests."""
import os

import mock
from defusedxml.ElementTree import fromstring

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')


def get_asset_path(filename):
    """Return the absolute path of an asset passed as parameter.

    Args:
        filename: Asset's filename.
    Returns:
        The absolute path of the corresponding asset.
    """
    return os.path.join(ASSETS_DIR, filename)


def get_asset_content(filename):
    """Return the content of an asset passed as parameter.

    Args:
        filename: Asset's filename.
    Returns:
        The content of the corresponding asset.
    """
    with open(get_asset_path(filename)) as asset:
        return asset.read()


def fake_cancel_pending_jobs(sself):
    """Cancel pending job without calling 'bkr cancel'.

    Args:
        sself: BeakerRunner
    Returns:
        None
    """
    # pylint: disable=protected-access,
    for job_id in set(sself.job_to_recipe_set_map):
        sself._BeakerRunner__forget_taskspec(job_id)


def exec_on(myrunner, mock_jobsubmit, xml_asset_file, max_aborted,
            alt_state=None):
    """Simulate getting live results from Beaker.
    Feed skt/runner with an XML and change it after a couple of runs.

    Args:
        myrunner:       BeakerRunner
        mock_jobsubmit: mock object for __jobsubmit
        xml_asset_file: xml filename to use
        max_aborted:    Maximum number of allowed aborted jobs. Abort the
                        whole stage if the number is reached.
        alt_state:      if set, represents a state to transition the job to
    Returns:
        BeakerRunner run() result

    """
    # pylint: disable=W0613
    def fake_getresultstree(sself, taskspec):
        """Fakt getresultstree. Change state of last recipe on 3rd loop.

        Args:
             sself:    BeakerRunner
             taskspec: ID of the job, recipe or recipe set.
        Returns:
            xml root
        """
        if alt_state:
            if fake_getresultstree.run_count > 2:
                result = fromstring(get_asset_content(xml_asset_file))

                recipe = result.findall('.//recipe')[-1]
                recipe.attrib['status'] = alt_state

                return result

            fake_getresultstree.run_count += 1

        return fromstring(get_asset_content(xml_asset_file))

    fake_getresultstree.run_count = 1
    # fake cancel_pending_jobs so 'bkr cancel' isn't run
    mock1 = mock.patch('skt.runner.BeakerRunner.cancel_pending_jobs',
                       fake_cancel_pending_jobs)
    mock1.start()

    # fake getresultstree, so we can change XML input during testrun
    mock2 = mock.patch('skt.runner.BeakerRunner.getresultstree',
                       fake_getresultstree)
    mock2.start()

    url = "http://machine1.example.com/builds/1234567890.tar.gz"
    release = "4.17.0-rc1"
    wait = True

    mock_jobsubmit.return_value = "J:0001"

    # no need to wait 60 seconds
    # though beaker_pass_results.xml only needs one iteration
    myrunner.watchdelay = 0.01

    result = myrunner.run(url, max_aborted, release, wait)

    mock1.stop()
    mock2.stop()
    return result


def fake_has_soaking(self, testname):
    """ Fake function to mock has_soaking of SoakWrap.
        Returns 1 for few selected tests.
    """
    # pylint: disable=unused-argument
    if testname in ['/test/misc/machineinfo', '/test/we/ran']:
        return 1

    return None


def fake_increase_test_runcount(self, testname, amount=1):
    """ Fake function to mock increase_test_runcount of SoakWrap.

    """
    # pylint: disable=unused-argument
    try:
        fake_increase_test_runcount.fake_stats[testname] += amount
    except AttributeError:
        fake_increase_test_runcount.fake_stats = {}
        fake_increase_test_runcount.fake_stats[testname] = amount

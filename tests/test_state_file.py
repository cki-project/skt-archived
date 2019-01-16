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
"""Test cases for state_file functions."""
import ConfigParser
import os
import shutil
import tempfile
import unittest

import mock

from skt import state_file


def exception_maker(*args, **kwargs):  # pylint: disable=W0613
    """Test function for throwing an exception."""
    raise IOError("Simulated test failure")


class TestStateFile(unittest.TestCase):
    """Test cases for state_file module."""

    def setUp(self):
        """Text fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.tempstate = "{}/state.yml".format(self.tmpdir)
        with open(self.tempstate, 'w') as fileh:
            fileh.write("---\noption: value")
        self.cfg = {'state': self.tempstate}

    def tearDown(self):
        """Teardown steps when testing is complete."""
        # Some tests remove the work directory, so we should check for it
        # before deleting it.
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir)

    def test_destroy_state_file(self):
        """Ensure destroy() deletes the state file."""
        state_file.destroy(self.cfg)
        self.assertFalse(os.path.isfile(self.cfg['state']))

    def test_destroy_state_file_missing(self):
        """Ensure destroy() checks to see if the state file exists."""
        os.unlink(self.tempstate)
        state_file.destroy(self.cfg)
        self.assertFalse(os.path.isfile(self.cfg['state']))

    @mock.patch('logging.error')
    @mock.patch('os.unlink', side_effect=exception_maker)
    def test_destroy_state_file_failure(self, mock_unlink, mock_logging):
        """Ensure destroy() fails when state file cannot be deleted."""
        # pylint: disable=W0613
        with self.assertRaises(IOError):
            state_file.destroy(self.cfg)

        mock_logging.assert_called_once()

    def test_read_state_file(self):
        """Ensure read() reads the state file."""
        test_yaml = state_file.read(self.cfg)
        self.assertDictEqual(test_yaml, {'option': 'value'})

    def test_read_state_file_missing(self):
        """Ensure read() checks to see if the state file exists.."""
        os.unlink(self.tempstate)
        test_yaml = state_file.read(self.cfg)
        self.assertDictEqual(test_yaml, {})

    @mock.patch('logging.error')
    @mock.patch('yaml.load', side_effect=exception_maker)
    def test_read_state_file_failure(self, mock_yaml, mock_logging):
        """Ensure read() fails when state file is unreadable."""
        # pylint: disable=W0613
        with self.assertRaises(IOError):
            state_file.read(self.cfg)

        mock_logging.assert_called_once()

    def test_update(self):
        """Ensure update() updates the state file."""
        # Write some new state
        new_state = {'foo2': 'bar2'}
        state_file.update(self.cfg, new_state)

        # Read in the state file to verify the new state was written
        state_data = state_file.read(self.cfg)

        expected_dict = {
            'option': 'value',
            'foo2': 'bar2',
        }
        self.assertDictEqual(state_data, expected_dict)

    @mock.patch('logging.error')
    @mock.patch('yaml.dump', side_effect=exception_maker)
    def test_update__failure(self, mock_yaml, mock_logging):
        """Ensure update() fails when the state file cannot be updated."""
        # pylint: disable=W0613
        # Write some new state
        new_state = {'foo2': 'bar2'}
        with self.assertRaises(IOError):
            state_file.update(self.cfg, new_state)

        mock_logging.assert_called_once()

    def test_update_state(self):
        """Ensure update_state() writes a state file."""
        temp_state = "{}/temp_sktrc".format(self.tmpdir)
        new_state = {
            'foo': 'bar',
            'foo2': 'bar2',
        }
        state_file.update_state(temp_state, new_state)

        config = ConfigParser.RawConfigParser()
        config.read(temp_state)
        self.assertEqual(config.get('state', 'foo'), 'bar')
        self.assertEqual(config.get('state', 'foo2'), 'bar2')

        # Test a write with an existing state file.
        state_file.update_state(temp_state, new_state)

        config = ConfigParser.RawConfigParser()
        config.read(temp_state)
        self.assertEqual(config.get('state', 'foo'), 'bar')
        self.assertEqual(config.get('state', 'foo2'), 'bar2')

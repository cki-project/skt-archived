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
import os
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
        self.tempyaml = "---\nfoo: bar"

    def prep_temporary_state_file(self):
        """Prepare a temporary state file."""
        tempstate = tempfile.NamedTemporaryFile(delete=False)
        tempstate.write(self.tempyaml)
        tempstate.close()
        cfg = {'state': tempstate.name}
        return cfg

    def test_destroy_state_file(self):
        """Ensure destroy() deletes the state file."""
        cfg = self.prep_temporary_state_file()

        state_file.destroy(cfg)
        self.assertFalse(os.path.isfile(cfg['state']))

    @mock.patch('os.unlink', side_effect=exception_maker)
    def test_destroy_state_file_failure(self, mockobj):
        """Ensure destroy() fails when state file cannot be deleted."""
        # pylint: disable=W0613
        cfg = self.prep_temporary_state_file()

        with self.assertRaises(IOError):
            state_file.destroy(cfg)

    def test_read_state_file(self):
        """Ensure read() reads the state file."""
        cfg = self.prep_temporary_state_file()

        test_yaml = state_file.read(cfg)
        self.assertDictEqual(test_yaml, {'foo': 'bar'})

        os.unlink(cfg['state'])

    @mock.patch('yaml.load', side_effect=exception_maker)
    def test_read_state_file_failure(self, mockobj):
        """Ensure read() fails when state file is unreadable."""
        # pylint: disable=W0613
        cfg = self.prep_temporary_state_file()

        with self.assertRaises(IOError):
            state_file.read(cfg)

        os.unlink(cfg['state'])

    def test_update_state_file(self):
        """Ensure update() updates the state file."""
        cfg = self.prep_temporary_state_file()

        # Write some new state
        new_state = {'foo2': 'bar2'}
        state_file.update(cfg, new_state)

        # Read in the state file to verify the new state was written
        state_data = state_file.read(cfg)

        expected_dict = {
            'foo': 'bar',
            'foo2': 'bar2',
        }
        self.assertDictEqual(state_data, expected_dict)

        os.unlink(cfg['state'])

    @mock.patch('yaml.dump', side_effect=exception_maker)
    def test_update_state_file_failure(self, mockobj):
        """Ensure update() fails when the state file cannot be updated."""
        # pylint: disable=W0613
        cfg = self.prep_temporary_state_file()

        # Write some new state
        new_state = {'foo2': 'bar2'}
        with self.assertRaises(IOError):
            state_file.update(cfg, new_state)

        os.unlink(cfg['state'])

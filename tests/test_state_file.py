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
        return tempstate.name

    def test_destroy_state_file(self):
        """Ensure destroy() deletes the state file."""
        state_name = self.prep_temporary_state_file()
        state = state_file.read(state_name)

        state_file.destroy(state)
        self.assertFalse(os.path.isfile(state_name))

    @mock.patch('os.unlink', side_effect=exception_maker)
    def test_destroy_state_file_failure(self, mockobj):
        """Ensure destroy() fails when state file cannot be deleted."""
        # pylint: disable=W0613
        state_name = self.prep_temporary_state_file()
        state = state_file.read(state_name)

        with self.assertRaises(IOError):
            state_file.destroy(state)

    def test_read_state_file(self):
        """Ensure read() reads the state file."""
        state_name = self.prep_temporary_state_file()
        expected_yaml = {
            'state_file': state_name,
            'foo': 'bar',
        }

        test_yaml = state_file.read(state_name)
        self.assertDictEqual(test_yaml, expected_yaml)

        os.unlink(state_name)

    @mock.patch('yaml.load', side_effect=exception_maker)
    def test_read_state_file_failure(self, mockobj):
        """Ensure read() fails when state file is unreadable."""
        # pylint: disable=W0613
        state_name = self.prep_temporary_state_file()

        with self.assertRaises(IOError):
            state_file.read(state_name)

        os.unlink(state_name)

    def test_update_state_file(self):
        """Ensure update() updates the state file."""
        state_name = self.prep_temporary_state_file()
        state_data = state_file.read(state_name)

        # Write some new state
        new_state = {'foo2': 'bar2'}
        state_data = state_file.update(state_data, new_state)

        # Read in the state file to verify the new state was written
        state_data = state_file.read(state_name)

        expected_dict = {
            'state_file': state_name,
            'foo': 'bar',
            'foo2': 'bar2',
        }
        self.assertDictEqual(state_data, expected_dict)

        os.unlink(state_name)

    @mock.patch('yaml.dump', side_effect=exception_maker)
    def test_update_state_file_failure(self, mockobj):
        """Ensure update() fails when the state file cannot be updated."""
        # pylint: disable=W0613
        state_name = self.prep_temporary_state_file()
        state = state_file.read(state_name)

        # Write some new state
        new_state = {'foo2': 'bar2'}
        with self.assertRaises(IOError):
            state = state_file.update(state, new_state)

        os.unlink(state_name)

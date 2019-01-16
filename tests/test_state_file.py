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

from skt import state_file


def exception_maker(*args, **kwargs):  # pylint: disable=W0613
    """Test function for throwing an exception."""
    raise IOError("Simulated test failure")


class TestStateFile(unittest.TestCase):
    """Test cases for state_file module."""

    def setUp(self):
        """Text fixtures."""
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        """Teardown steps when testing is complete."""
        # Some tests remove the work directory, so we should check for it
        # before deleting it.
        if os.path.isdir(self.tmpdir):
            shutil.rmtree(self.tmpdir)

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

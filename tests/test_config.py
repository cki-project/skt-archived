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
import unittest

import mock

from skt.config import ConfigFile
from skt.config import ConfigSet


class TestRunner(unittest.TestCase):
    """Test cases for runner module."""

    # (Too many public methods) pylint: disable=too-many-public-methods

    def setUp(self):
        """Set up test fixtures"""
        self.config_set = ConfigSet()

    def tearDown(self):
        pass

    def test_obj_init(self):
        """Tests that ConfigSet() is initialized correctly."""
        self.assertEqual(self.config_set.data, {})

    def test_add_argument(self):
        """Tests that adding argument that starts with -/-- succeeds."""
        self.config_set.add_argument(mock.MagicMock(), 'testsection',
                                     '-o', '--output')

        self.assertIn('testsection', self.config_set.data)
        self.assertEqual(self.config_set.output, None)

    def test_add_argument_fails(self):
        """Tests that adding argument that doesn't start with -/-- fails."""
        with self.assertRaises(RuntimeError):
            self.config_set.add_argument(mock.MagicMock(), 'testsection',
                                         'Option')

    def test_load_args(self):
        """Tests that modified flat getter/setter works."""

        class FakeArgs:
            """Fakes commandline arguments argparse.Namespace."""
            # pylint: disable=too-few-public-methods
            def __init__(self):
                self.output = 500

        self.config_set.add_argument(mock.MagicMock(), 'testsection',
                                     '-o', '--output')

        # set value
        self.config_set.output = 5
        # test that modifies setters/getters work
        self.assertEqual(self.config_set.output, 5)

        # test that fakeargs's output value overrides output
        self.config_set.load_args(FakeArgs())

        self.assertEqual(self.config_set.output, 500)

    def test_section_collision(self):
        """Having a key with the same name in different sections fails."""
        self.config_set.set_value('section1', 'duplicate_key', 1)

        self.config_set.set_value('section2', 'duplicate_key', 2)

        with self.assertRaises(Exception):
            print(self.config_set.duplicate_key)

    def test_maybe_set_value(self):
        """ Test maybe_set_value semantics."""

        # set initial value: maybe_set_value will create section
        self.config_set.maybe_set_value('section', 'key', 1)

        # make sure it was changed
        self.assertEqual(self.config_set.key, 1)

        # set initial value
        self.config_set.set_value('section', 'nonekey', None)

        # use maybe_ to try to change it
        self.config_set.maybe_set_value('section', 'key', 2)

        # make sure it wasn't changed
        self.assertEqual(self.config_set.key, 1)

        # use maybe_ to write to key with none value
        self.config_set.maybe_set_value('section', 'nonekey', 2)

        # make sure it was changed
        self.assertEqual(self.config_set.nonekey, 2)

    def test_save_state(self):
        """Make sure save_state works."""

        self.config_set.add_argument(mock.MagicMock(), 'state', '-o',
                                     '--output')
        self.config_set.add_argument(mock.MagicMock(), 'state', '--workdir')
        self.config_set.output = 5
        self.config_set.workdir = '/tmp/'

        with mock.patch('skt.config.open', create=True) as mock_open:
            mock_open.return_value = mock.MagicMock()

            # run method under test
            self.config_set.save_state({'whatever': 0, 'workdir': 'newval'})

            self.assertEqual(self.config_set.data['state'],
                             {'whatever': 0, 'output': 5, 'workdir': 'newval'})

    def test_config_file(self):
        """Test that overriding args works."""

        class FakeParser:
            """Fakes RawConfigParser. Uses prefilled values."""
            # pylint: disable=no-self-use,unused-argument
            def items(self, section):
                """Fakes items() method."""
                return [('output', 'val')]

            def sections(self):
                """Fakes sections() method."""
                return ['sec']

            def read(self, *args, **kwargs):
                """Fakes read() method."""
                return '[sec]output=val'

        self.config_set.add_argument(mock.MagicMock(), 'state', '-o',
                                     '--output')
        # defined, but None. Will be overriden by config.
        self.config_set.output = None

        with mock.patch('configparser.RawConfigParser') as mock_open:
            mock_open.return_value = FakeParser()
            conf_file = ConfigFile(self.config_set, '/tmp/whatever')

            self.assertEqual(conf_file.config_set.output, 'val')

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
"""Test cases for publisher module."""
import unittest

from skt import publisher


class TestPublisher(unittest.TestCase):
    """Test cases for publisher.Publisher class."""
    def test_geturl(self):
        """Check if the source url is built correctly."""
        pub = publisher.Publisher('dest', 'file:///tmp/test', '')
        self.assertEqual(pub.geturl('source'), 'file:///tmp/test/source')

        pub_with_prefix = publisher.Publisher('dest', 'file:///tmp/test', 'p')
        self.assertEqual(pub_with_prefix.geturl('source'),
                         'file:///tmp/test/psource')

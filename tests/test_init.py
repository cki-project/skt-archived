"""
Test cases for __init__.py.
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
import unittest

import skt


class TestInit(unittest.TestCase):
    """Test cases for skt's __init__.py"""

    def test_stringify_with_integer(self):
        """Ensure stringify() can handle an integer."""
        myinteger = int(42)
        result = skt.stringify(myinteger)
        self.assertIsInstance(result, str)
        self.assertEqual(result, str(myinteger))


    def test_stringify_with_string(self):
        """Ensure stringify() can handle a plain string."""
        mystring = "Test text"
        result = skt.stringify(mystring)
        self.assertIsInstance(result, str)
        self.assertEqual(result, mystring)

    def test_stringify_with_unicode(self):
        """Ensure stringify() can handle a unicode byte string."""
        myunicode = unicode("Test text")
        result = skt.stringify(myunicode)
        self.assertIsInstance(result, str)
        self.assertEqual(result, myunicode.encode('utf-8'))

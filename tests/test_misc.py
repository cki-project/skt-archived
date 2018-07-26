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
"""Test cases for misc.py."""
import unittest

import skt.misc


class TestIndependent(unittest.TestCase):
    """Test cases for independent functions in misc.py."""

    def test_join_with_slash(self):
        """Ensure join_with_slash return a good url, path string."""
        base = "path/to/dir"
        suffix = "file"
        self.assertEqual("path/to/dir/file",
                         skt.misc.join_with_slash(base, suffix))
        base = "path/to/dir/"
        suffix = "file"
        self.assertEqual("path/to/dir/file",
                         skt.misc.join_with_slash(base, suffix))
        base = "path/to/dir1/"
        suffix = "dir2/"
        self.assertEqual("path/to/dir1/dir2/",
                         skt.misc.join_with_slash(base, suffix))
        base = "path/to/dir1/"
        suffix1 = "dir2/"
        suffix2 = "file"
        self.assertEqual("path/to/dir1/dir2/file",
                         skt.misc.join_with_slash(base, suffix1, suffix2))
        base = "http://url.com/"
        suffix = "part"
        self.assertEqual("http://url.com/part",
                         skt.misc.join_with_slash(base, suffix))
        base = "http://url.com"
        suffix = "part"
        self.assertEqual("http://url.com/part",
                         skt.misc.join_with_slash(base, suffix))

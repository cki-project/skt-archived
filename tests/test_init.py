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
import os
import tempfile
import unittest

import skt

GIT_URI = "git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git"


class TestInit(unittest.TestCase):
    """Test cases for skt's __init__.py"""

    def test_stringify_with_integer(self):
        """Ensure stringify() can handle an integer"""
        myinteger = int(42)
        result = skt.stringify(myinteger)
        self.assertIsInstance(result, str)
        self.assertEqual(result, str(myinteger))

    def test_stringify_with_string(self):
        """Ensure stringify() can handle a plain string"""
        mystring = "Test text"
        result = skt.stringify(mystring)
        self.assertIsInstance(result, str)
        self.assertEqual(result, mystring)

    def test_stringify_with_unicode(self):
        """Ensure stringify() can handle a unicode byte string"""
        myunicode = unicode("Test text")
        result = skt.stringify(myunicode)
        self.assertIsInstance(result, str)
        self.assertEqual(result, myunicode.encode('utf-8'))

    def test_parse_bad_patchwork_url(self):
        """Ensure parse_patchwork_url() handles a parsing exception"""
        patchwork_url = "garbage"
        with self.assertRaises(Exception) as context:
            skt.parse_patchwork_url(patchwork_url)

        self.assertTrue(
            "Can't parse patchwork url: '{}'".format(patchwork_url)
            in context.exception
        )

    def test_ktree_init(self):
        """Test ktree.__init__ without specifying a work directory"""
        result = skt.ktree(GIT_URI)
        self.assertIsInstance(result, skt.ktree)
        self.assertEqual(result.uri, GIT_URI)

    def test_ktree_init_workdir(self):
        """Test ktree.__init__ with specifying a work directory"""
        workdir = tempfile.mkdtemp()
        result = skt.ktree(GIT_URI, wdir=workdir)
        self.assertEqual(result.wdir, workdir)

    def test_ktree_create_workdir(self):
        """Ensure create_workdir() creates a work dir if it does not exist"""
        workdir = tempfile.mkdtemp()
        os.rmdir(workdir)
        skt.ktree(GIT_URI, wdir=workdir)
        self.assertTrue(os.path.isdir(workdir))

    def test_ktree_remove_mergelog(self):
        """Ensure remove_mergelog really removes the merge.log"""
        workdir = tempfile.mkdtemp()
        merge_log = "{}/merge.log".format(workdir)

        # Write some sample log content
        with open(merge_log, 'w') as fileh:
            fileh.write("merge log sample content")

        skt.ktree(GIT_URI, wdir=workdir)
        self.assertFalse(os.path.isfile(merge_log))

    def test_ktree_getpath(self):
        """Ensure ktree.getpath() returns the workdir path"""
        workdir = tempfile.mkdtemp()
        ktree_obj = skt.ktree(GIT_URI, wdir=workdir)
        result = ktree_obj.getpath()
        self.assertEqual(result, workdir)

    def test_ktree_dumpinfo(self):
        """Ensure ktree.getpath() dumps CSV data"""
        workdir = tempfile.mkdtemp()
        ktree_obj = skt.ktree(GIT_URI, wdir=workdir)
        ktree_obj.info = [["test", "result"]]
        ktree_obj.dumpinfo("test.csv")
        self.assertTrue(os.path.isfile("{}/test.csv".format(workdir)))

        with open("{}/test.csv".format(workdir), 'r') as fileh:
            csv_content = fileh.read()

        self.assertEqual(csv_content, "test,result\n")

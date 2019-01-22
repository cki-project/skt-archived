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
import os
import shutil
import tempfile
import unittest

import mock

from skt import publisher


class TestPublisher(unittest.TestCase):
    """Test cases for publisher.Publisher class."""

    def setUp(self):
        """Test fixtures."""
        self.tmpdir = tempfile.mkdtemp()

        self.pub = publisher.Publisher(
            pub_type='cp',
            source="{}/kernel.tar.gz".format(self.tmpdir),
            dest="{}/published".format(self.tmpdir),
            baseurl="http://localhost/published"
        )

    def tearDown(self):
        """Tear down test fixtures."""
        shutil.rmtree(self.tmpdir)

    def test_cp_file(self):
        """Test cp with a file."""
        # Create a test file.
        temp_file = "{}/kernel.tar.gz".format(self.tmpdir)
        with open(temp_file, 'w') as fileh:
            fileh.write("Kernel data")

        # Add an existing publish directory to ensure that publisher won't
        # try to create it again.
        if not os.path.isdir(self.pub.destination):
            os.mkdir(self.pub.destination)

        # Test with a file.
        self.pub.publish()

        # Ensure the file is present with the right data inside.
        self.assertTrue(os.path.isfile(temp_file))
        with open(temp_file, 'r') as fileh:
            self.assertEqual(fileh.read(), "Kernel data")

    def test_cp_directory(self):
        """Test cp with a directory."""
        # Create a test directory with files.
        repo_dir = "{}/repo_dir".format(self.tmpdir)
        os.mkdir(repo_dir)

        with open("{}/kernel.rpm".format(repo_dir), 'w') as fileh:
            fileh.write("Kernel RPM data")

        # Test with a directory.
        self.pub.source = repo_dir
        self.pub.publish()

        # Verify that the directory was made.
        published_dir = "{}/published/repo_dir".format(self.tmpdir)
        self.assertTrue(os.path.isdir(published_dir))

        # Try it once more to ensure that the RPM repo dir will be removed
        # and re-created.
        self.pub.publish()
        self.assertTrue(os.path.isdir(published_dir))

    @mock.patch('subprocess.check_call')
    def test_scp_file(self, mock_call):
        """Test scp with a file."""
        # Create a test file.
        temp_file = "{}/kernel.tar.gz".format(self.tmpdir)
        with open(temp_file, 'w') as fileh:
            fileh.write("Kernel data")

        # Test with a file.
        self.pub.pub_type = 'scp'
        self.pub.publish()

        check_call_args = mock_call.call_args[0]
        expected_call_args = [
            'scp',
            '-r',
            self.pub.source,
            self.pub.destination
        ]
        self.assertEqual(check_call_args[0], expected_call_args)

    def test_invalid_publisher(self):
        """Verify that an exception occurs with an invalid publisher."""
        self.pub.pub_type = 'unladen_swallow'
        with self.assertRaises(KeyError):
            self.pub.publish()

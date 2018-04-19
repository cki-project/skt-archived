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
from __future__ import division
import unittest
import tempfile
import shutil
import os
import subprocess
import mock
from mock import Mock

import skt


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


class KBuilderTest(unittest.TestCase):
    # (Too many instance attributes) pylint: disable=R0902
    """Test cases for skt.kbuilder class"""
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmpconfig = tempfile.NamedTemporaryFile()
        self.kbuilder = skt.kbuilder(self.tmpdir, self.tmpconfig.name)
        self.m_popen = Mock()
        self.m_popen.returncode = 0
        self.ctx_popen = mock.patch('subprocess.Popen',
                                    Mock(return_value=self.m_popen))
        self.ctx_check_call = mock.patch('subprocess.check_call', Mock())
        self.m_io_open = Mock()
        self.m_io_open.__enter__ = lambda *args: self.m_io_open
        self.m_io_open.__exit__ = lambda *args: None
        self.ctx_io_open = mock.patch('io.open',
                                      Mock(return_value=self.m_io_open))
        self.kernel_tarball = 'linux-4.16.0.tar.gz'
        self.success_str = 'Tarball successfully created in ./{}\n'
        self.success_str = self.success_str.format(self.kernel_tarball)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_mktgz_clean(self):
        """
        Check if `mrproper` target is called when `clean` is True and not
        called when `clean` is False.
        """
        with self.ctx_popen, self.ctx_check_call as m_check_call:
            self.assertRaises(Exception, self.kbuilder_mktgz_silent,
                              clean=True)
            self.assertEqual(
                m_check_call.mock_calls[0],
                mock.call(['make', '-C', self.tmpdir, 'mrproper'])
            )
            m_check_call.reset_mock()
            self.assertRaises(Exception, self.kbuilder_mktgz_silent,
                              clean=False)
            self.assertNotEqual(
                m_check_call.mock_calls[0],
                mock.call(['make', '-C', self.tmpdir, 'mrproper']),
            )

    def test_mktgz_timeout(self):
        """
        Check if timeout error is raised when kernel building takes longer than
        specified timeout.
        """
        self.m_popen.poll = Mock(side_effect=[None, None, -15])
        self.m_popen.returncode = -15
        from time import sleep as real_sleep

        def m_sleep(seconds):
            """sleep function but acelerated 100x"""
            real_sleep(seconds/100)
        ctx_sleep = mock.patch('time.sleep', m_sleep)
        with ctx_sleep, self.ctx_check_call, self.ctx_popen:
            self.assertRaises(skt.CommandTimeoutError,
                              self.kbuilder_mktgz_silent, timeout=0.001)

    def test_mktgz_parsing_error(self):
        """Check if ParsingError is raised when no kernel is found in stdout"""
        self.m_io_open.readlines = Mock(return_value=['foo\n', 'bar\n'])
        with self.ctx_popen, self.ctx_check_call, self.ctx_io_open:
            self.assertRaises(skt.ParsingError, self.kbuilder_mktgz_silent)

    def test_mktgz_ioerror(self):
        """Check if IOError is raised when tarball path does not exist"""
        self.m_io_open.readlines = Mock(
            return_value=['foo\n', self.success_str]
        )
        with self.ctx_io_open, self.ctx_popen, self.ctx_check_call:
            self.assertRaises(IOError, self.kbuilder_mktgz_silent)

    def test_mktgz_make_fail(self):
        """
        Check if subprocess.CalledProcessError is raised when make command
        fails to spawn.
        """
        self.m_popen.returncode = 1
        with self.ctx_popen:
            self.assertRaises(subprocess.CalledProcessError,
                              self.kbuilder_mktgz_silent)

    def test_mktgz_success(self):
        """Check if mktgz can finish successfully"""
        self.m_io_open.readlines = Mock(
            return_value=['foo\n', self.success_str, 'bar']
        )
        self.m_popen.returncode = 0
        with self.ctx_io_open, self.ctx_popen, self.ctx_check_call:
            with open(os.path.join(self.tmpdir, self.kernel_tarball), 'w'):
                pass
            full_path = self.kbuilder_mktgz_silent()
            self.assertEqual(os.path.join(self.tmpdir, self.kernel_tarball),
                             full_path)

    def kbuilder_mktgz_silent(self, *args, **kwargs):
        """Run self.kbuilder.mktgz with disabled output"""
        with mock.patch('sys.stdout'):
            return self.kbuilder.mktgz(*args, **kwargs)

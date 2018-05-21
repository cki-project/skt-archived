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
from requests.exceptions import RequestException

import skt


class TestIndependent(unittest.TestCase):
    """Test cases for independent functions in __init__.py"""

    def test_invalid_patch_url(self):
        """Ensure get_patch_mbox() throws exception if the URL is invalid"""
        self.assertRaises(RequestException,
                          skt.get_patch_mbox,
                          'this-is-invalid')

    def test_nonexistent_patch_subject(self):
        """Ensure get_patch_name() handles nonexistent 'Subject' in mbox"""
        mbox_body = 'nothing useful here'
        self.assertEqual('<SUBJECT MISSING>', skt.get_patch_name(mbox_body))

    def test_ok_patch_subject(self):
        """Ensure get_patch_name() returns correct 'Subject' if present"""
        mbox_body = 'From Test Thu May 2 17:49:51 2018\nSubject: GOOD SUBJECT'
        self.assertEqual('GOOD SUBJECT', skt.get_patch_name(mbox_body))

    def test_encoded_patch_subject(self):
        """Ensure get_patch_name() correctly decodes UTF-8 'Subject'"""
        mbox_body = ('From Test Thu May 2 17:49:51 2018\n'
                     'Subject: =?utf-8?q?=5BTEST=5D?=')
        self.assertEqual('[TEST]', skt.get_patch_name(mbox_body))

    def test_multipart_encoded_subject(self):
        """
        Ensure get_patch_name() correctly decodes multipart encoding
        of 'Subject'
        """
        mbox_body = ('From Test Thu May 2 17:49:51 2018\nSubject: '
                     '=?ISO-8859-1?B?SWYgeW91IGNhbiByZWFkIHRoaXMgeW8=?=\n'
                     '    =?ISO-8859-2?B?dSB1bmRlcnN0YW5kIHRoZSBleGFtcGxlLg'
                     '==?=')
        self.assertEqual('If you can read this you understand the example.',
                         skt.get_patch_name(mbox_body))


class KBuilderTest(unittest.TestCase):
    # (Too many instance attributes) pylint: disable=R0902
    """Test cases for skt.KernelBuilder class"""
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.tmpconfig = tempfile.NamedTemporaryFile()
        self.kbuilder = skt.KernelBuilder(self.tmpdir, self.tmpconfig.name)
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

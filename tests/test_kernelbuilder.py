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
"""Test cases for KernelBuilder class."""

from __future__ import division
import unittest
import tempfile
import shutil
import os
import subprocess
import mock
from mock import Mock

import skt.kernelbuilder as kernelbuilder


class KBuilderTest(unittest.TestCase):
    # (Too many instance attributes) pylint: disable=R0902
    """Test cases for KernelBuilder class."""

    def setUp(self):
        """Test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.tmpconfig = tempfile.NamedTemporaryFile()
        self.kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name
        )
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
        """Tear down test fixtures."""
        shutil.rmtree(self.tmpdir)

    def test_clean_kernel_source(self):
        """Ensure clean_kernel_source() calls 'make mrproper'."""
        with self.ctx_check_call as m_check_call:
            self.kbuilder.clean_kernel_source()
            self.assertEqual(
                m_check_call.mock_calls[0],
                mock.call(self.kbuilder.make_argv_base + ['mrproper'])
            )

    def test_get_cfgpath(self):
        """Ensure get_cfgpath() get cfg path."""
        result = self.kbuilder.get_cfgpath()
        self.assertEqual(result, "{}/.config".format(self.tmpdir))

    def test_adjust_config_option(self):
        """Ensure __adjust_config_option() calls the correct commands."""
        # pylint: disable=W0212,E1101
        expected_args = [
            "{}/scripts/config".format(self.tmpdir),
            "--file",
            "{}/.config".format(self.tmpdir),
            "--disable",
            "some_option"
        ]

        with self.ctx_check_call as m_check_call:
            self.kbuilder._KernelBuilder__adjust_config_option('disable',
                                                               'some_option')
            self.assertEqual(
                m_check_call.mock_calls[0],
                mock.call(expected_args)
            )

    def test_get_build_arch(self):
        """Ensure __get_build_arch() returns the ARCH env variable."""
        # pylint: disable=W0212,E1101
        os.environ['ARCH'] = 's390x'
        result = self.kbuilder._KernelBuilder__get_build_arch()

        self.assertEqual('s390x', result)

    def test_mktgz_timeout(self):
        """Ensure the build fails properly when it exceeds the timeout."""
        self.m_popen.poll = Mock(side_effect=[None, None, -15])
        self.m_popen.returncode = -15
        from time import sleep as real_sleep

        def m_sleep(seconds):
            """Sleep function but acelerated 100x."""
            real_sleep(seconds/100)

        ctx_sleep = mock.patch('time.sleep', m_sleep)
        with ctx_sleep, self.ctx_check_call, self.ctx_popen:
            self.assertRaises(
                kernelbuilder.CommandTimeoutError,
                self.kbuilder_mktgz_silent,
                timeout=0.001
            )

    def test_mktgz_parsing_error(self):
        """Check if ParsingError is raised when no kernel found in stdout."""
        self.m_io_open.readlines = Mock(return_value=['foo\n', 'bar\n'])
        with self.ctx_popen, self.ctx_check_call, self.ctx_io_open:
            self.assertRaises(
                kernelbuilder.ParsingError,
                self.kbuilder_mktgz_silent
            )

    def test_mktgz_ioerror(self):
        """Check if IOError is raised when tarball path does not exist."""
        self.m_io_open.readlines = Mock(
            return_value=['foo\n', self.success_str]
        )
        with self.ctx_io_open, self.ctx_popen, self.ctx_check_call:
            self.assertRaises(IOError, self.kbuilder_mktgz_silent)

    def test_mktgz_make_fail(self):
        """Ensure exception is raised when make command fails to spawn."""
        self.m_popen.returncode = 1
        with self.ctx_popen:
            self.assertRaises(subprocess.CalledProcessError,
                              self.kbuilder_mktgz_silent)

    def test_mktgz_success(self):
        """Check if mktgz can finish successfully."""
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
        """Run self.kbuilder.mktgz with disabled output."""
        with mock.patch('sys.stdout'):
            return self.kbuilder.mktgz(*args, **kwargs)

    def test_extra_make_args(self):
        """Ensure KernelBuilder handles extra_make_args properly."""
        extra_make_args_example = '-j10'
        kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            extra_make_args=extra_make_args_example
        )
        self.assertEqual(kbuilder.extra_make_args, [extra_make_args_example])

    @mock.patch("skt.kernelbuilder.KernelBuilder."
                "_KernelBuilder__prepare_kernel_config")
    def test_getrelease(self, mock_prepare):
        """Ensure get_release() handles a valid kernel version string."""
        kernel_version = '4.17.0-rc6+\n'
        mock_popen = self.ctx_popen
        self.m_popen.communicate = Mock(return_value=(kernel_version, None))

        with mock_popen, mock_prepare:
            result = self.kbuilder.getrelease()
            self.assertEqual(kernel_version.strip(), result)

    @mock.patch("skt.kernelbuilder.KernelBuilder."
                "_KernelBuilder__prepare_kernel_config")
    def test_getrelease_regex_fail(self, mock_prepare):
        """Ensure get_release() fails if the regex doesn't match."""
        mock_popen = self.ctx_popen
        self.m_popen.communicate = Mock(return_value=('this_is_silly', None))

        with mock_popen, mock_prepare:
            with self.assertRaises(Exception):
                self.kbuilder.getrelease()

    @mock.patch("skt.kernelbuilder.KernelBuilder."
                "_KernelBuilder__prepare_kernel_config")
    def test_getrelease_ready(self, mock_prepare):
        """Ensure get_release() skips prepare when _ready=1."""
        kernel_version = '4.17.0-rc6+\n'
        mock_popen = self.ctx_popen
        self.m_popen.communicate = Mock(return_value=(kernel_version, None))
        self.kbuilder._ready = 1  # pylint: disable=protected-access

        with mock_popen, mock_prepare:
            result = self.kbuilder.getrelease()
            self.assertEqual(kernel_version.strip(), result)

            # The prepare_kernel_config() method should not have been called
            # since self._ready was set to 1.
            mock_prepare.assert_not_called()

    @mock.patch("skt.kernelbuilder.KernelBuilder."
                "_KernelBuilder__adjust_config_option")
    @mock.patch('shutil.copyfile')
    @mock.patch("glob.glob")
    @mock.patch("subprocess.check_call")
    def test_prep_config_redhat(self, mock_check_call, mock_glob, mock_shutil,
                                mock_adjust_cfg):
        """Ensure KernelBuilder handles Red Hat configs."""
        # pylint: disable=W0212,E1101
        self.kbuilder.cfgtype = 'rh-configs'
        self.kbuilder.enable_debuginfo = True
        self.kbuilder.rh_configs_glob = "redhat/configs/kernel-*-x86_64.config"
        mock_glob.return_value = ['configs/config-3.10.0-x86_64.config']
        self.kbuilder._KernelBuilder__prepare_kernel_config()

        # Ensure the configs were built using the correct command
        check_call_args = mock_check_call.call_args[0]
        expected_args = self.kbuilder.make_argv_base + ['rh-configs']
        self.assertEqual(expected_args, check_call_args[0])

        mock_shutil.assert_called_once()
        mock_adjust_cfg.assert_called_once()

    @mock.patch('logging.error')
    @mock.patch('logging.info')
    @mock.patch("glob.glob")
    @mock.patch("subprocess.check_call")
    def test_redhat_config_glob_failure(self, mock_check_call, mock_glob,
                                        mock_info, mock_err):
        """Ensure that skt exits when no Red Hat config files match."""
        # pylint: disable=W0212,E1101
        mock_check_call.return_value = ''
        mock_glob.return_value = []
        self.kbuilder.rh_configs_glob = "redhat/configs/kernel-*-x86_64.config"
        with self.assertRaises(SystemExit):
            self.kbuilder._KernelBuilder__make_redhat_config()

        mock_info.assert_called_once()
        mock_err.assert_called_once()

    @mock.patch("skt.kernelbuilder.KernelBuilder."
                "_KernelBuilder__adjust_config_option")
    @mock.patch("subprocess.check_call")
    def test_prep_config_tinyconfig(self, mock_check_call, mock_adjust_cfg):
        """Ensure KernelBuilder handles tinyconfig."""
        # pylint: disable=W0212,E1101
        self.kbuilder.cfgtype = 'tinyconfig'
        self.kbuilder._KernelBuilder__prepare_kernel_config()

        # Ensure the config was built using the correct command
        check_call_args = mock_check_call.call_args[0]
        expected_args = self.kbuilder.make_argv_base + ['tinyconfig']
        self.assertEqual(expected_args, check_call_args[0])

        mock_adjust_cfg.assert_called()

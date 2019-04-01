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
    # pylint: disable=too-many-public-methods
    """Test cases for KernelBuilder class."""

    def setUp(self):
        """Test fixtures."""
        self.tmpdir = tempfile.mkdtemp()
        self.tmpconfig = tempfile.NamedTemporaryFile()
        self.kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            make_target='targz-pkg',
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

        self.m_multipipe = mock.patch(
            'skt.kernelbuilder.KernelBuilder.run_multipipe',
            Mock(return_value=0)
        )

        self.kernel_tarball = 'linux-4.16.0.tar.gz'
        self.success_str = 'Tarball successfully created in ./{}\n'
        self.success_str = self.success_str.format(self.kernel_tarball)

    def tearDown(self):
        """Tear down test fixtures."""
        shutil.rmtree(self.tmpdir)

    def test_assemble_make_options(self):
        """Ensure assemble_make_options() provides valid make options."""
        make_opts = self.kbuilder.assemble_make_options()

        self.assertIsInstance(make_opts, list)
        self.assertIn('make', make_opts)
        self.assertIn('-C', make_opts)
        self.assertIn('INSTALL_MOD_STRIP=1', make_opts)

    def test_clean_kernel_source(self):
        """Ensure clean_kernel_source() calls 'make mrproper'."""
        with self.m_multipipe as m_multipipe:
            self.kbuilder.clean_kernel_source()
            self.assertEqual(
                m_multipipe.mock_calls[0],
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

        with self.m_multipipe as m_multipipe:
            self.kbuilder._KernelBuilder__adjust_config_option(
                'disable',
                'some_option'
            )
            self.assertEqual(
                m_multipipe.mock_calls[0],
                mock.call(expected_args)
            )

    def test_get_build_arch(self):
        """Ensure __get_build_arch() returns the ARCH_CONFIG env variable."""
        # pylint: disable=W0212,E1101
        os.environ['ARCH_CONFIG'] = 's390x'
        result = self.kbuilder._KernelBuilder__get_build_arch()

        self.assertEqual('s390x', result)

    def test_get_cross_compiler_prefix(self):
        """Ensure CROSS_COMPILE environment variable is returned."""
        # pylint: disable=W0212,E1101
        os.environ['CROSS_COMPILE'] = 'powerpc64-linux-gnu-'
        build = self.kbuilder
        result = build._KernelBuilder__get_cross_compiler_prefix()

        self.assertEqual('powerpc64-linux-gnu-', result)

    def test_extra_make_args(self):
        """Ensure KernelBuilder handles extra_make_args properly."""
        extra_make_args_example = '-j10'
        kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            extra_make_args=extra_make_args_example,
            make_target='targz-pkg'
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
    def test_prep_config_redhat(self, mock_glob, mock_shutil,
                                mock_adjust_cfg):
        """Ensure KernelBuilder handles Red Hat configs."""
        # pylint: disable=W0212,E1101
        self.kbuilder.cfgtype = 'rh-configs'
        self.kbuilder.enable_debuginfo = True
        self.kbuilder.rh_configs_glob = "redhat/configs/kernel-*-x86_64.config"
        mock_glob.return_value = ['configs/config-3.10.0-x86_64.config']

        with self.m_multipipe as m_multipipe:
            self.kbuilder._KernelBuilder__prepare_kernel_config()

            # Ensure the configs were built using the correct command
            check_call_args = m_multipipe.call_args[0]
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
    def test_prep_config_tinyconfig(self, mock_adjust_cfg):
        """Ensure KernelBuilder handles tinyconfig."""
        # pylint: disable=W0212,E1101
        self.kbuilder.cfgtype = 'tinyconfig'
        with self.m_multipipe as m_multipipe:
            self.kbuilder._KernelBuilder__prepare_kernel_config()

            # Ensure the config was built using the correct command
            check_call_args = m_multipipe.call_args[0]
            expected_args = self.kbuilder.make_argv_base + ['tinyconfig']
            self.assertEqual(expected_args, check_call_args[0])

        mock_adjust_cfg.assert_called()

    def test_build_tarball(self):
        """Test the building and handling of tarballs."""
        kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            make_target='targz-pkg',
        )

        # Test with a build log that contains no tarball lines at all.
        with open(kbuilder.buildlog, 'w') as fileh:
            fileh.write("This is a sample line without a tarball.")
        with self.m_multipipe, self.assertRaises(kernelbuilder.ParsingError):
            kbuilder.compile_kernel()

        # Add the tarball to the build log, but don't write a matching file
        # to the filesystem.
        with open(kbuilder.buildlog, 'w+') as fileh:
            fileh.write(self.success_str)

        with self.m_multipipe, self.assertRaises(IOError):
            kbuilder.compile_kernel()

        # Create a real matching tarball so this will complete with success.
        test_tarball = "{}/{}".format(kbuilder.source_dir, self.kernel_tarball)
        with open(test_tarball, 'w') as fileh:
            fileh.write("Kernel data")

        with self.m_multipipe:
            fpath = kbuilder.compile_kernel()

        self.assertEqual(test_tarball, fpath)

    def test_build_rpm(self):
        """Test the building and handling of RPMs."""
        kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            make_target='binrpm-pkg',
        )

        test_rpms = [
            "{}/linux-4.20.rpm".format(self.tmpdir),
            "{}/linux-headers-4.20.rpm".format(self.tmpdir)
        ]

        # Test with a build log that contains no RPM lines at all.
        with open(kbuilder.buildlog, 'w') as fileh:
            fileh.write("This is a sample line without RPMs.")
        with self.m_multipipe, self.assertRaises(kernelbuilder.ParsingError):
            kbuilder.compile_kernel()

        # Add the RPMs to the build log, but don't write any matching files
        # to the filesystem.
        with open(kbuilder.buildlog, 'w+') as fileh:
            for test_rpm in test_rpms:
                fileh.write("Wrote: {}\n".format(test_rpm))

        with self.m_multipipe, self.assertRaises(IOError):
            kbuilder.compile_kernel()

        # Create real files for our RPMs so this will complete with success.
        for test_rpm in test_rpms:
            with open(test_rpm, 'w') as fileh:
                fileh.write("Kernel data")

        # Ensure compile_kernel can handle an existing repo directory.
        os.mkdir("{}/rpm_repo".format(kbuilder.source_dir))

        with self.m_multipipe:
            fpath = kbuilder.compile_kernel()

        self.assertEqual("{}/rpm_repo/".format(kbuilder.source_dir), fpath)

    def test_bad_make_target(self):
        """Test what happens when an unsupported make target is used."""
        with self.m_multipipe, self.assertRaises(KeyError):
            kernelbuilder.KernelBuilder(
                self.tmpdir,
                self.tmpconfig.name,
                make_target='"this-is-not-a-make-target-silly"',
            )

    def test_compile_failures(self):
        """Ensure compile_kernel() handles compile failures properly."""
        # Exit code 1 should throw a CalledProcessError.
        with self.m_multipipe as m_multipipe:
            m_multipipe.return_value = 1
            with self.assertRaises(subprocess.CalledProcessError):
                self.kbuilder.compile_kernel()

        # Exit code 1 should throw a CalledProcessError.
        with self.m_multipipe as m_multipipe:
            m_multipipe.return_value = 124
            with self.assertRaises(kernelbuilder.CommandTimeoutError):
                self.kbuilder.compile_kernel()

    def test_reset_buildlog(self):
        """Test resetting the buildlog when it is present."""
        # pylint: disable=W0212,E1101
        with open(self.kbuilder.buildlog, 'w') as fileh:
            fileh.write('foo')

        self.kbuilder._KernelBuilder__reset_build_log()

        with open(self.kbuilder.buildlog, 'r') as fileh:
            buildlog = fileh.read()

        self.assertNotIn('foo', buildlog)

    def test_rpm_repo_failure(self):
        """Test exception when the RPM repo fails to create."""
        kbuilder = kernelbuilder.KernelBuilder(
            self.tmpdir,
            self.tmpconfig.name,
            make_target='binrpm-pkg',
        )

        with self.m_multipipe as m_multipipe:
            m_multipipe.return_value = 1
            with self.assertRaises(subprocess.CalledProcessError):
                kbuilder.make_rpm_repo([])

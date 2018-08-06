# Copyright (c) 2018 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""Class for building kernels"""
import glob
import io
import logging
import multiprocessing
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
from threading import Timer

from skt.misc import join_with_slash


class KernelBuilder(object):
    def __init__(self, source_dir, basecfg, cfgtype=None,
                 extra_make_args=None, enable_debuginfo=False,
                 rh_configs_glob=None):
        self.source_dir = source_dir
        self.basecfg = basecfg
        self.cfgtype = cfgtype
        self._ready = 0
        self.buildlog = join_with_slash(self.source_dir, "build.log")
        self.make_argv_base = ["make", "-C", self.source_dir]
        self.enable_debuginfo = enable_debuginfo
        self.build_arch = self.__get_build_arch()
        self.rh_configs_glob = rh_configs_glob

        # Split the extra make arguments provided by the user
        if extra_make_args:
            self.extra_make_args = shlex.split(extra_make_args)
        else:
            self.extra_make_args = []

        logging.info("basecfg: %s", self.basecfg)
        logging.info("cfgtype: %s", self.cfgtype)

    def __adjust_config_option(self, action, option):
        """Adjust a kernel config option using kernel scripts."""
        if action not in ['enable', 'disable']:
            raise LookupError("Only 'enable' and 'disable' are supported.")

        args = [
            join_with_slash(self.source_dir, "scripts", "config"),
            "--file", self.get_cfgpath(),
            "--{}".format(action),
            option
        ]
        logging.info("%s config option '%s': %s", action, option, args)
        subprocess.check_call(args)

    def clean_kernel_source(self):
        """Clean the kernel source directory with 'make mrproper'."""
        args = self.make_argv_base + ["mrproper"]
        logging.info("cleaning up tree: %s", args)
        subprocess.check_call(args)

    def __glob_escape(self, pathname):
        """Escape any wildcard/glob characters in pathname."""
        return re.sub(r"[]*?[]", "[\g<0>]", pathname)

    def __prepare_kernel_config(self):
        """Prepare the kernel config for the compile."""
        if self.cfgtype == 'rh-configs':
            # Build Red Hat configs and copy the correct one into place
            self.__make_redhat_config()
        elif self.cfgtype == 'tinyconfig':
            # Build an extremely small config file for quick testing
            self.__make_tinyconfig()
        else:
            # Copy the existing config file into place
            shutil.copyfile(self.basecfg,
                            join_with_slash(self.source_dir, ".config"))
            args = self.make_argv_base + [self.cfgtype]
            logging.info("prepare config: %s", args)
            subprocess.check_call(args)

        # NOTE(mhayden): Building kernels with debuginfo can increase the
        # final kernel tarball size by 3-4x and can increase build time
        # slightly. Debug symbols are really only needed for deep diagnosis
        # of kernel issues on a specific system. This is why debuginfo is
        # disabled by default.
        if not self.enable_debuginfo:
            self.__adjust_config_option('disable', 'debug_info')

        self._ready = 1

    def __make_redhat_config(self):
        """Prepare the Red Hat kernel config files."""
        args = self.make_argv_base + ['rh-configs']
        logging.info("building Red Hat configs: %s", args)
        subprocess.check_call(args)

        # Copy the correct kernel config into place
        escaped_source_dir = self.__glob_escape(self.source_dir)
        config = join_with_slash(escaped_source_dir, self.rh_configs_glob)
        config_filename = glob.glob(config)

        # We should exit with an error if there are no matches
        if not config_filename:
            logging.error(
                "The glob string provided with --rh-configs-glob did not "
                "match any of the kernel configuration files built with "
                "`make rh-configs`."
            )
            sys.exit(1)

        logging.info("copying Red Hat config: %s", config_filename[0])
        shutil.copyfile(
            config_filename[0],
            join_with_slash(self.source_dir, ".config")
        )

    def __make_tinyconfig(self):
        """Make the smallest kernel config file possible for quick testing."""
        args = self.make_argv_base + ['tinyconfig']
        logging.info("building tinyconfig: %s", args)
        subprocess.check_call(args)

    def __get_build_arch(self):
        """Determine the build architecture for the kernel build."""
        # Detect cross-compiling via the ARCH= environment variable
        if 'ARCH' in os.environ:
            return os.environ['ARCH']

        return platform.machine()

    def get_cfgpath(self):
        return join_with_slash(self.source_dir, ".config")

    def getrelease(self):
        krelease = None
        if not self._ready:
            self.__prepare_kernel_config()

        args = self.make_argv_base + ["kernelrelease"]
        mk = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, _) = mk.communicate()
        for line in stdout.split("\n"):
            m = re.match(r'^\d+\.\d+\.\d+.*$', line)
            if m:
                krelease = m.group()
                break

        if krelease is None:
            raise Exception("Failed to find kernel release in stdout")

        return krelease

    def mktgz(self, timeout=60 * 60 * 12):
        """
        Build kernel and modules, after that, pack everything into a tarball.

        Args:
            timeout:    Max time in seconds will wait for build.
        Returns:
            The full path of the tarball generated.
        Raises:
            CommandTimeoutError: When building kernel takes longer than the
                                 specified timeout.
            CalledProcessError:  When a command returns an exit code different
                                 than zero.
            ParsingError:        When can not find the tarball path in stdout.
            IOError:             When tarball file doesn't exist.
        """
        fpath = None
        stdout_list = []
        self.__prepare_kernel_config()

        # Set up the arguments and options for the kernel build
        targz_pkg_argv = [
            "INSTALL_MOD_STRIP=1",
            "-j%d" % multiprocessing.cpu_count(),
            "targz-pkg"
        ]
        kernel_build_argv = (
            self.make_argv_base
            + targz_pkg_argv
            + self.extra_make_args
        )

        logging.info("building kernel: %s", kernel_build_argv)

        with io.open(self.buildlog, 'wb') as writer, \
                io.open(self.buildlog, 'rb') as reader:
            make = subprocess.Popen(kernel_build_argv,
                                    stdout=writer,
                                    stderr=subprocess.STDOUT)
            make_timedout = []

            def stop_process(proc):
                """
                Terminate the process with SIGTERM and flag it as timed out.
                """
                if proc.poll() is None:
                    proc.terminate()
                    make_timedout.append(True)
            timer = Timer(timeout, stop_process, [make])
            timer.setDaemon(True)
            timer.start()
            try:
                while make.poll() is None:
                    self.append_and_log2stdout(reader.readlines(), stdout_list)
                    time.sleep(1)
                self.append_and_log2stdout(reader.readlines(), stdout_list)
            finally:
                timer.cancel()
            if make_timedout:
                raise CommandTimeoutError(
                    "'{}' was taking too long".format(
                        ' '.join(kernel_build_argv)
                    )
                )
            if make.returncode != 0:
                raise subprocess.CalledProcessError(
                    make.returncode,
                    ' '.join(kernel_build_argv)
                )

        match = re.search("^Tarball successfully created in (.*)$",
                          ''.join(stdout_list), re.MULTILINE)
        if match:
            fpath = os.path.realpath(
                join_with_slash(
                    self.source_dir,
                    match.group(1)
                )
            )
        else:
            raise ParsingError('Failed to find tgz path in stdout')

        if not os.path.isfile(fpath):
            raise IOError("Built kernel tarball {} not found".format(fpath))

        return fpath

    @staticmethod
    def append_and_log2stdout(lines, full_log):
        """
        Append `lines` into `full_log` and show `lines` on stdout.

        Args:
            lines:      list of strings.
            full_log:   list where `lines` members are appended.
        """
        full_log.extend(lines)
        sys.stdout.write(''.join(lines))
        sys.stdout.flush()


class CommandTimeoutError(Exception):
    """
    Exception raised when a timeout occurs on a process which has had timeouts
    enabled. The accompanying value is a string whose value is the command
    launched plus a small explanation.
    """


class ParsingError(Exception):
    """
    Exception raised when a regex does not match and it is impossible to
    continue. The accompanying value is a string which explains what it can not
    find.
    """

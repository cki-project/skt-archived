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
import logging
import multiprocessing
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys

from skt.misc import join_with_slash


class KernelBuilder(object):
    """
    KernelBuilder - a class used to build a kernel, e.g. call 'make',
    clean kernel source tree, etc.
    """
    # pylint: disable=too-many-instance-attributes,too-many-arguments
    def __init__(self, source_dir, basecfg, cfgtype=None,
                 extra_make_args=None, enable_debuginfo=False,
                 rh_configs_glob=None, localversion=None):
        self.source_dir = source_dir
        self.basecfg = basecfg
        self.cfgtype = cfgtype if cfgtype is not None else "olddefconfig"
        self._ready = 0
        self.buildlog = join_with_slash(self.source_dir, "build.log")
        self.make_argv_base = [
            "make", "-C", self.source_dir
        ]
        self.enable_debuginfo = enable_debuginfo
        self.build_arch = self.__get_build_arch()
        self.cross_compiler_prefix = self.__get_cross_compiler_prefix()
        self.rh_configs_glob = rh_configs_glob
        self.localversion = localversion

        self.targz_pkg_argv = [
            "INSTALL_MOD_STRIP=1",
            "-j%d" % multiprocessing.cpu_count(),
            "targz-pkg"
        ]

        # Split the extra make arguments provided by the user
        if extra_make_args:
            self.extra_make_args = shlex.split(extra_make_args)
        else:
            self.extra_make_args = []

        # Truncate the buildlog, if it exists.
        self.__reset_build_log()

        logging.info("basecfg: %s", self.basecfg)
        logging.info("cfgtype: %s", self.cfgtype)

    def __adjust_config_option(self, action, *options):
        """Adjust a kernel config option using kernel scripts."""
        args = [
            join_with_slash(self.source_dir, "scripts", "config"),
            "--file", self.get_cfgpath(),
            "--{}".format(action)
        ] + list(options)
        logging.info("%s config option '%s': %s", action, options, args)
        self.run_multipipe(args)

    def clean_kernel_source(self):
        """Clean the kernel source directory with 'make mrproper'."""
        # pylint: disable=no-self-use
        args = self.make_argv_base + ["mrproper"]
        logging.info("cleaning up tree: %s", args)
        self.run_multipipe(args)

    @classmethod
    def __glob_escape(cls, pathname):
        """Escape any wildcard/glob characters in pathname."""
        return re.sub(r"[]*?[]", r"[\g<0>]", pathname)

    def __prepare_kernel_config(self):
        """Prepare the kernel config for the compile."""
        if self.cfgtype == 'rh-configs':
            # Build Red Hat configs and copy the correct one into place
            self.__make_redhat_config()
        elif self.cfgtype in ['tinyconfig', 'allyesconfig', 'allmodconfig']:
            # Use the cfgtype provided with the kernel's Makefile.
            self.__make_config()
        else:
            # Copy the existing config file into place. Use a subprocess call
            # for it just for the nice logs and exception in case the call
            # fails.
            args = [
                'cp',
                self.basecfg,
                join_with_slash(self.source_dir, ".config")
            ]
            self.run_multipipe(args)

            args = self.make_argv_base + [self.cfgtype]
            logging.info("prepare config: %s", args)
            self.run_multipipe(args)

        # NOTE(mhayden): Building kernels with debuginfo can increase the
        # final kernel tarball size by 3-4x and can increase build time
        # slightly. Debug symbols are really only needed for deep diagnosis
        # of kernel issues on a specific system. This is why debuginfo is
        # disabled by default.
        if not self.enable_debuginfo:
            self.__adjust_config_option('disable', 'debug_info')

        # Set CONFIG_LOCALVERSION
        self.__adjust_config_option(
            'set-str',
            'LOCALVERSION',
            '.{}'.format(self.localversion)
        )

        self._ready = 1

    def __make_redhat_config(self):
        """Prepare the Red Hat kernel config files."""
        args = self.make_argv_base + ['rh-configs']
        logging.info("building Red Hat configs: %s", args)

        # Unset CROSS_COMPILE because rh-configs doesn't handle the cross
        # compile args correctly in some cases
        environ = os.environ.copy()
        environ.pop('CROSS_COMPILE', None)
        self.run_multipipe(args, env=environ)

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

    def __make_config(self):
        """Make a config using the kernels Makefile."""
        args = self.make_argv_base + [self.cfgtype]
        logging.info("building %s: %s", self.cfgtype, args)
        self.run_multipipe(args)

    def __get_build_arch(self):
        """Determine the build architecture for the kernel build."""
        # pylint: disable=no-self-use
        # Detect cross-compiling via the ARCH= environment variable
        if 'ARCH' in os.environ:
            return os.environ['ARCH']

        return platform.machine()

    def __reset_build_log(self):
        """Truncate the build log."""
        if os.path.isfile(self.buildlog):
            with open(self.buildlog, 'w') as fileh:
                fileh.write('')

    @classmethod
    def __get_cross_compiler_prefix(cls):
        """
        Determine the cross compiler prefix for the kernel build.

        Returns:
            The cross compiler prefix, if defined in the environment.
        """
        if 'CROSS_COMPILE' in os.environ:
            return os.environ['CROSS_COMPILE']

        return None

    def get_cfgpath(self):
        """
        Get path to kernel .config file.

        Returns:
            Absolute path to kernel .config.
        """
        return join_with_slash(self.source_dir, ".config")

    def getrelease(self):
        """
        Get kernel release.

        Returns:
             kernel release like '4.17.0-rc6+'.
        """
        krelease = None
        if not self._ready:
            self.__prepare_kernel_config()

        args = self.make_argv_base + ["kernelrelease"]
        make = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, _) = make.communicate()
        for line in stdout.split("\n"):
            match = re.match(r'^\d+\.\d+\.\d+.*$', line)
            if match:
                krelease = match.group()
                break

        if krelease is None:
            raise Exception("Failed to find kernel release in stdout")

        return krelease

    def assemble_make_options(self):
        """Assemble all of the make options into a list."""
        kernel_build_argv = (
            self.make_argv_base
            + self.targz_pkg_argv
            + self.extra_make_args
        )
        return kernel_build_argv

    def find_tarball(self):
        """
        Find a tarball in the buildlog.

        Returns:
            The full path to the tarball (as a string), or None if the tarball
            line was not found in the log.
        """
        # Compile a regex to look for the tarball filename.
        tgz_regex = re.compile("^Tarball successfully created in (.*)$")

        # Read the buildlog line by line looking for the tarball.
        fpath = None
        with open(self.buildlog, 'r') as fileh:
            for log_line in fileh:
                match = tgz_regex.search(log_line)

                # If we find a match, store the path to the tarball and stop
                # reading the buildlog.
                if match:
                    fpath = os.path.realpath(
                        join_with_slash(
                            self.source_dir,
                            match.group(1)
                        )
                    )
                    break

        return fpath

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

        self.__prepare_kernel_config()

        # Get the kernel build options.
        kernel_build_argv = self.assemble_make_options()
        logging.info("building kernel: %s", kernel_build_argv)

        # Prepend a timeout to the make options.
        kernel_build_argv = (
            ['timeout', str(timeout)]
            + kernel_build_argv
        )

        # Compile the kernel.
        returncode = self.run_multipipe(kernel_build_argv)

        # The timeout command exits with 124 if a timeout occurred.
        if returncode == 124:
            raise CommandTimeoutError(
                "'{}' was taking too long".format(
                    ' '.join(kernel_build_argv)
                )
            )

        # The build failed for a reason other than a timeout.
        if returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                ' '.join(kernel_build_argv)
            )

        # Search the build log for a tarball file.
        fpath = self.find_tarball()

        # Raise an exception if we did not find a tarball.
        if not fpath:
            raise ParsingError('Failed to find tgz path in stdout')

        # Does the tarball mentioned in the buidlog actually exist on the
        # filesystem?
        if not os.path.isfile(fpath):
            raise IOError("Built kernel tarball {} not found".format(fpath))

        return fpath

    def run_multipipe(self, args, env=os.environ.copy()):
        """
        Run a process while writing to stdout/file simultaneously.

        Args:
            args:   A command to run in list format.
            env:    An option environment to set for the subcommand. The
                    existing environment is used if one is not specified.

        Returns:
            The return code from the Popen call.

        """
        # Set up a basic logger.
        root = logging.getLogger('multipipe')

        # Don't adjust the parent logger's settings with the changes we make
        # here.
        root.propagate = False

        # Set up a handler that writes directly to the build log.
        file_handler = logging.FileHandler(self.buildlog)
        file_handler.setLevel(logging.DEBUG)

        # Set up another handler that writes to stdout.
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)

        # Ensure that the log format is unaltered -- just messages are
        # written without any time prefixes.
        formatter = logging.Formatter('%(message)s')
        file_handler.setFormatter(formatter)
        stdout_handler.setFormatter(formatter)

        # Add the file and stdout handlers to our logger.
        root.addHandler(file_handler)
        root.addHandler(stdout_handler)

        # Run the command.
        logging.debug("Running multipipe command: %s", ' '.join(args))
        root.info("$ %s", ' '.join(args))
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            env=env
        )

        # Loop over the buffered stdout/stderr from the command and write the
        # output to our logger.
        for line in iter(process.stdout.readline, ''):
            root.info(line.strip())

        # Ensure the process has exited.
        exit_code = process.wait()

        # Remove the extra handlers we added.
        # NOTE(mhayden): Removing this step will cause all log lines to be
        #                written *twice* to stdout/log. This was very annoying
        #                to debug, so please don't remove these unless you
        #                know what you're doing.
        for handler in root.handlers[:]:
            root.removeHandler(handler)

        return exit_code


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

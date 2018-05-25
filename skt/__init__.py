# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted
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
"""Class for working with the kernel source tree."""
from email.errors import HeaderParseError
import email.header
import email.parser
import logging
import multiprocessing
import os
import re
import shlex
import shutil
import subprocess
import sys
import io
import time
from threading import Timer
import requests


def get_patch_mbox(url):
    """
    Retrieve a string representing mbox of the patch.

    Args:
        url: Patchwork URL of the patch to retrieve

    Returns:
        String representing body of the patch mbox

    Raises:
        Exception in case the URL is currently unavailable or invalid
    """
    # Use os.path for manipulation with URL because urlparse can't deal
    # with URLs ending both with and without slash.
    mbox_url = os.path.join(url, 'mbox')

    try:
        response = requests.get(mbox_url)
    except requests.exceptions.RequestException as exc:
        raise(exc)

    if response.status_code != requests.codes.ok:
        raise Exception('Failed to retrieve patch from %s, returned %d' %
                        (url, response.status_code))

    return response.content


def get_patch_name(content):
    """
    Retrieve patch name from 'Subject' header from the mbox string
    representing a patch.

    Args:
        content: String representing patch mbox

    Returns:
        Name of the patch. <SUBJECT MISSING> is returned if no subject is
        found, and <SUBJECT ENCODING INVALID> if header decoding fails.
    """
    headers = email.parser.Parser().parsestr(content, True)
    subject = headers['Subject']
    if not subject:
        # Emails return None if the header is not found so use a stub subject
        # instead of it
        return '<SUBJECT MISSING>'

    # skt's custom CSV parsing doesn't understand multiline values, until we
    # switch to a proper parser we need a temporary fix. Use separate
    # replacements to handle Windows / *nix endlines and mboxes which contain
    # '\n' and a space instead of '\n\t' as well.
    # Tracking issue: https://github.com/RH-FMK/skt/issues/119
    subject = subject.replace('\n', ' ').replace('\t', '').replace('\r', '')

    try:
        # decode_header() returns a list of tuples (value, charset)
        decoded = [value for value, _ in email.header.decode_header(subject)]
    except HeaderParseError:
        # We can't parse the original subject so use a stub one instead
        return '<SUBJECT ENCODING INVALID>'

    return ''.join(decoded)


class KernelBuilder(object):
    def __init__(self, source_dir, basecfg, cfgtype=None,
                 extra_make_args=None, enable_debuginfo=False):
        self.source_dir = source_dir
        self.basecfg = basecfg
        self.cfgtype = cfgtype if cfgtype is not None else "olddefconfig"
        self._ready = 0
        self.buildlog = "%s/build.log" % self.source_dir
        self.make_argv_base = ["make", "-C", self.source_dir]
        self.enable_debuginfo = enable_debuginfo

        # Split the extra make arguments provided by the user
        if extra_make_args:
            self.extra_make_args = shlex.split(extra_make_args)
        else:
            self.extra_make_args = []

        try:
            os.unlink(self.buildlog)
        except OSError:
            pass

        logging.info("basecfg: %s", self.basecfg)
        logging.info("cfgtype: %s", self.cfgtype)

    def prepare(self, clean=True):
        if (clean):
            args = self.make_argv_base + ["mrproper"]
            logging.info("cleaning up tree: %s", args)
            subprocess.check_call(args)

        shutil.copyfile(self.basecfg, "%s/.config" % self.source_dir)

        # NOTE(mhayden): Building kernels with debuginfo can increase the
        # final kernel tarball size by 3-4x and can increase build time
        # slightly. Debug symbols are really only needed for deep diagnosis
        # of kernel issues on a specific system. This is why debuginfo is
        # disabled by default.
        if not self.enable_debuginfo:
            args = ["%s/scripts/config" % self.source_dir,
                    "--file", self.get_cfgpath(),
                    "--disable", "debug_info"]
            logging.info("disabling debuginfo: %s", args)
            subprocess.check_call(args)

        args = self.make_argv_base + [self.cfgtype]
        logging.info("prepare config: %s", args)
        subprocess.check_call(args)
        self._ready = 1

    def get_cfgpath(self):
        return "%s/.config" % self.source_dir

    def getrelease(self):
        krelease = None
        if not self._ready:
            self.prepare(False)

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

    def mktgz(self, clean=True, timeout=60 * 60 * 12):
        """
        Build kernel and modules, after that, pack everything into a tarball.

        Args:
            clean:      If it is True, run the `mrproper` target before build.
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
        self.prepare(clean)

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
                os.path.join(
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

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

from email.errors import HeaderParseError
import email.header
import email.parser
import logging
import multiprocessing
import os
import re
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


class KernelTree(object):
    """
    KernelTree - a kernel git repository "checkout", i.e. a clone with a
    working directory
    """
    def __init__(self, uri, ref=None, wdir=None, fetch_depth=None):
        """
        Initialize a KernelTree.

        Args:
            uri:    The Git URI of the repository's origin remote.
            ref:    The remote reference to checkout. Assumed to be "master",
                    if not specified.
            wdir:   The directory to house the clone and to checkout into.
                    Creates and uses a temporary directory if not specified.
            fetch_depth:
                    The amount of git history to include with the clone.
                    Smaller depths lead to faster repo clones.
        """
        # The git "working directory" (the "checkout")
        self.wdir = wdir
        # The cloned git repository
        self.gdir = "%s/.git" % self.wdir
        # The origin remote's URL
        self.uri = uri
        # The remote reference to checkout
        self.ref = ref if ref is not None else "master"
        self.info = []
        self.mergelog = "%s/merge.log" % self.wdir

        try:
            os.mkdir(self.wdir)
        except OSError:
            pass

        try:
            os.unlink(self.mergelog)
        except OSError:
            pass

        self.git_cmd("init")

        try:
            self.git_cmd("remote", "set-url", "origin", self.uri)
        except subprocess.CalledProcessError:
            self.git_cmd("remote", "add", "origin", self.uri)

        self.fetch_depth = fetch_depth

        logging.info("base repo url: %s", self.uri)
        logging.info("base ref: %s", self.ref)
        logging.info("work dir: %s", self.wdir)

    def git_cmd(self, *args, **kwargs):
        args = list(["git", "--work-tree", self.wdir, "--git-dir",
                     self.gdir]) + list(args)
        logging.debug("executing: %s", " ".join(args))
        subprocess.check_call(args,
                              env=dict(os.environ, **{'LC_ALL': 'C'}),
                              **kwargs)

    def getpath(self):
        return self.wdir

    def dumpinfo(self, fname='buildinfo.csv'):
        """
        Write build information to the specified file in ad-hoc CSV format.
        The order of the "columns" in the file depends on the order various
        member functions were called in.

        Args:
            fname:  Name of the output file, relative to the workdir.
                    Default is "buildinfo.csv".

        Returns:
            Full path to the written file.
        """
        fpath = '/'.join([self.wdir, fname])
        with open(fpath, 'w') as f:
            for iitem in self.info:
                f.write(','.join(iitem) + "\n")
        return fpath

    def get_commit_date(self, ref=None):
        """
        Get the committer date of the commit pointed at by the specified
        reference, or of the currently checked-out commit, if not specified.

        Args:
            ref:    The reference to commit to get the committer date of,
                    or None, if the currently checked-out commit should be
                    used instead.
        Returns:
            The epoch timestamp string of the commit's committer date.
        """
        args = ["git",
                "--work-tree", self.wdir,
                "--git-dir", self.gdir,
                "show",
                "--format=%ct",
                "-s"]

        if ref is not None:
            args.append(ref)

        logging.debug("git_commit_date: %s", args)
        grs = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, stderr) = grs.communicate()

        return int(stdout.rstrip())

    # FIXME Rename to say the hash is being retrieved
    def get_commit(self, ref=None):
        """
        Get the full hash of the commit pointed at by the specified reference,
        or of the currently checked-out commit, if not specified.

        Args:
            ref:    The reference to commit to get the hash of, or None, if
                    the currently checked-out commit should be used instead.
        Returns:
            The commit's full hash string.
        """
        args = ["git",
                "--work-tree", self.wdir,
                "--git-dir", self.gdir,
                "show",
                "--format=%H",
                "-s"]

        if ref is not None:
            args.append(ref)

        logging.debug("git_commit: %s", args)
        grs = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, stderr) = grs.communicate()

        return stdout.rstrip()

    def checkout(self):
        """
        Clone and checkout the specified reference from the specified repo URL
        to the specified working directory. Requires "ref" (reference) to be
        specified upon creation.

        Returns:
            Full hash of the last commit.
        """
        dstref = "refs/remotes/origin/%s" % (self.ref.split('/')[-1])
        logging.info("fetching base repo")
        git_fetch_args = [
            "fetch", "-n", "origin",
            "+%s:%s" % (self.ref, dstref)
        ]
        # If the user provided extra arguments for the git fetch step, append
        # them to the existing set of arguments.
        if self.fetch_depth:
            git_fetch_args.extend(['--depth', self.fetch_depth])

        # The git_cmd() method expects a list of args, not a list of strings,
        # so we need to expand our list into args with *.
        self.git_cmd(*git_fetch_args)

        logging.info("checking out %s", self.ref)
        self.git_cmd("checkout", "-q", "--detach", dstref)
        self.git_cmd("reset", "--hard", dstref)

        head = self.get_commit()
        self.info.append(("base", self.uri, head))
        logging.info("baserepo %s: %s", self.ref, head)
        return str(head).rstrip()

    def cleanup(self):
        logging.info("cleaning up %s", self.wdir)
        shutil.rmtree(self.wdir)

    def get_remote_url(self, remote):
        rurl = None
        try:
            grs = subprocess.Popen(["git",
                                    "--work-tree", self.wdir,
                                    "--git-dir", self.gdir,
                                    "remote", "show", remote],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
            (stdout, stderr) = grs.communicate()
            for line in stdout.split("\n"):
                m = re.match('Fetch URL: (.*)', line)
                if m:
                    rurl = m.group(1)
                    break
        except subprocess.CalledProcessError:
            pass

        return rurl

    def getrname(self, uri):
        rname = (uri.split('/')[-1].replace('.git', '')
                 if not uri.endswith('/')
                 else uri.split('/')[-2].replace('.git', ''))
        while self.get_remote_url(rname) == uri:
            logging.warning(
                "remote '%s' already exists with a different uri, adding '_'",
                rname
            )
            rname += '_'

        return rname

    def merge_git_ref(self, uri, ref="master"):
        rname = self.getrname(uri)
        head = None

        try:
            self.git_cmd("remote", "add", rname, uri, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            pass

        dstref = "refs/remotes/%s/%s" % (rname, ref.split('/')[-1])
        logging.info("fetching %s", dstref)
        self.git_cmd("fetch", "-n", rname,
                     "+%s:%s" % (ref, dstref))

        logging.info("merging %s: %s", rname, ref)
        try:
            grargs = {'stdout': subprocess.PIPE} if \
                logging.getLogger().level > logging.DEBUG else {}

            self.git_cmd("merge", "--no-edit", dstref, **grargs)
            head = self.get_commit(dstref)
            self.info.append(("git", uri, head))
            logging.info("%s %s: %s", rname, ref, head)
        except subprocess.CalledProcessError:
            logging.warning("failed to merge '%s' from %s, skipping", ref,
                            rname)
            self.git_cmd("reset", "--hard")
            return (1, None)

        return (0, head)

    def merge_patchwork_patch(self, uri):
        patch_content = get_patch_mbox(uri)

        logging.info("Applying %s", uri)

        gam = subprocess.Popen(
            ["git", "am", "-"],
            cwd=self.wdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, **{'LC_ALL': 'C'})
        )

        (stdout, stderr) = gam.communicate(patch_content)
        retcode = gam.wait()

        if retcode != 0:
            self.git_cmd("am", "--abort")

            with open(self.mergelog, "w") as fp:
                fp.write(stdout)

            raise Exception("Failed to apply patch %s" %
                            os.path.basename(os.path.normpath(uri)))

        patchname = get_patch_name(patch_content)
        # FIXME Do proper CSV escaping, or switch data format instead of
        #       maiming subjects (ha-ha). See issue #119.
        # Replace commas with semicolons to avoid clashes with CSV separator
        self.info.append(("patchwork", uri, patchname.replace(',', ';')))

    def merge_patch_file(self, path):
        if not os.path.exists(path):
            raise Exception("Patch %s not found" % path)
        args = ["git", "am", path]
        try:
            subprocess.check_output(args,
                                    cwd=self.wdir,
                                    env=dict(os.environ, **{'LC_ALL': 'C'}))
        except subprocess.CalledProcessError as exc:
            self.git_cmd("am", "--abort")

            with open(self.mergelog, "w") as fp:
                fp.write(exc.output)

            raise Exception("Failed to apply patch %s" % path)

        self.info.append(("patch", path))

    def bisect_start(self, good):
        os.chdir(self.wdir)
        binfo = None
        gbs = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "bisect", "start", "HEAD", good],
                               stdout=subprocess.PIPE)
        (stdout, stderr) = gbs.communicate()

        for line in stdout.split("\n"):
            m = re.match('^Bisecting: (.*)$', line)
            if m:
                binfo = m.group(1)
                logging.info(binfo)
            else:
                logging.info(line)

        return binfo

    def bisect_iter(self, bad):
        os.chdir(self.wdir)
        ret = 0
        binfo = None
        status = "good"

        if bad == 1:
            status = "bad"

        logging.info("git bisect %s", status)
        gbs = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "bisect", status],
                               stdout=subprocess.PIPE)
        (stdout, stderr) = gbs.communicate()

        for line in stdout.split("\n"):
            m = re.match('^Bisecting: (.*)$', line)
            if m:
                binfo = m.group(1)
                logging.info(binfo)
            else:
                m = re.match('^(.*) is the first bad commit$', line)
                if m:
                    binfo = m.group(1)
                    ret = 1
                    logging.warning("Bisected, bad commit: %s", binfo)
                    break
                else:
                    logging.info(line)

        return (ret, binfo)


class KernelBuilder(object):
    def __init__(self, path, basecfg, cfgtype=None, makeopts=None,
                 enable_debuginfo=False):
        self.path = path
        self.basecfg = basecfg
        self.cfgtype = cfgtype if cfgtype is not None else "olddefconfig"
        self._ready = 0
        self.makeopts = None
        self.buildlog = "%s/build.log" % self.path
        self.defmakeargs = ["make", "-C", self.path]
        self.enable_debuginfo = enable_debuginfo

        if makeopts is not None:
            # FIXME: Might want something a bit smarter here, something that
            # would parse it the same way bash does
            self.makeopts = makeopts.split(' ')
            self.defmakeargs += self.makeopts

        try:
            os.unlink(self.buildlog)
        except OSError:
            pass

        logging.info("basecfg: %s", self.basecfg)
        logging.info("cfgtype: %s", self.cfgtype)

    def prepare(self, clean=True):
        if (clean):
            args = self.defmakeargs + ["mrproper"]
            logging.info("cleaning up tree: %s", args)
            subprocess.check_call(args)

        shutil.copyfile(self.basecfg, "%s/.config" % self.path)

        # NOTE(mhayden): Building kernels with debuginfo can increase the
        # final kernel tarball size by 3-4x and can increase build time
        # slightly. Debug symbols are really only needed for deep diagnosis
        # of kernel issues on a specific system. This is why debuginfo is
        # disabled by default.
        if not self.enable_debuginfo:
            args = ["%s/scripts/config" % self.path,
                    "--file", self.get_cfgpath(),
                    "--disable", "debug_info"]
            logging.info("disabling debuginfo: %s", args)
            subprocess.check_call(args)

        args = self.defmakeargs + [self.cfgtype]
        logging.info("prepare config: %s", args)
        subprocess.check_call(args)
        self._ready = 1

    def get_cfgpath(self):
        return "%s/.config" % self.path

    def getrelease(self):
        krelease = None
        if not self._ready:
            self.prepare(False)

        args = self.defmakeargs + ["kernelrelease"]
        mk = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, stderr) = mk.communicate()
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

        args = self.defmakeargs + ["INSTALL_MOD_STRIP=1",
                                   "-j%d" % multiprocessing.cpu_count(),
                                   "targz-pkg"]

        logging.info("building kernel: %s", args)

        with io.open(self.buildlog, 'wb') as writer, \
                io.open(self.buildlog, 'rb') as reader:
            make = subprocess.Popen(args,
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
                    "'{}' was taking too long".format(' '.join(args))
                )
            if make.returncode != 0:
                raise subprocess.CalledProcessError(make.returncode,
                                                    ' '.join(args))

        match = re.search("^Tarball successfully created in (.*)$",
                          ''.join(stdout_list), re.MULTILINE)
        if match:
            fpath = os.path.realpath(os.path.join(self.path, match.group(1)))
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

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

import email
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile

import requests


def get_patchwork_mbox(patchwork_url):
    """
    Retrieve the mbox file for a patch in Patchwork

    Args:
        patchwork_url: The URL to the patchwork patch.

    Returns:
        The mbox text from Patchwork as a string.
    """
    # NOTE(mhayden): Old versions of patchwork do not like a uri that has
    # double slashes, like https://patchwork.kernel.org/patch/10326957//mbox/.
    # We must ensure that any trailing slashes are removed before adding
    # 'mbox' to the uri.
    patchwork_url = patchwork_url.rstrip('/ ')

    mbox_url = "{}/mbox/".format(patchwork_url)
    r = requests.get(mbox_url)
    return r.text


class ktree(object):
    """
    Ktree - a kernel git repository "checkout", i.e. a clone with a working
    directory
    """
    def __init__(self, uri, ref=None, wdir=None):
        """
        Initialize a ktree.

        Args:
            uri:    The Git URI of the repository's origin remote.
            ref:    The remote reference to checkout. Assumed to be "master",
                    if not specified.
            wdir:   The directory to house the clone and to checkout into.
                    Creates and uses a temporary directory if not specified.
        """
        # FIXME Move expansion up the call stack, as this limits the class
        # usefulness, because tilde is a valid path character.
        # The git "working directory" (the "checkout")
        self.wdir = (os.path.expanduser(wdir)
                     if wdir is not None
                     else tempfile.mkdtemp())
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

        logging.info("base repo url: %s", self.uri)
        logging.info("base ref: %s", self.ref)
        logging.info("work dir: %s", self.wdir)

    def git_cmd(self, *args, **kwargs):
        args = list(["git", "--work-tree", self.wdir, "--git-dir",
                     self.gdir]) + list(args)
        logging.debug("executing: %s", " ".join(args))
        subprocess.check_call(args, **kwargs)

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
        self.git_cmd("fetch", "-n", "origin",
                     "+%s:%s" % (self.ref, dstref))

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
            logging.warning("remote '%s' already exists with a different uri, "
                            "adding '_'" % rname)
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
        mbox_text = get_patchwork_mbox(uri)

        if not mbox_text:
            raise Exception(
                "Failed to fetch patch info for %s" % uri
            )

        patch_mbox = email.message_from_string(mbox_text)

        logging.info("Applying %s", uri)

        gam = subprocess.Popen(
            ["git", "am", "-"],
            cwd=self.wdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )

        (stdout, stderr) = gam.communicate(mbox_text)
        retcode = gam.wait()

        if retcode != 0:
            self.git_cmd("am", "--abort")

            with open(self.mergelog, "w") as fp:
                fp.write(stdout)

            raise Exception(
                "Failed to apply patch %s" % patch_mbox['X-Patchwork-Id']
            )

        self.info.append(
            (
                "patchwork",
                uri,
                patch_mbox['Subject'].replace(',', ';')
            )
        )

    def merge_patch_file(self, path):
        try:
            self.git_cmd("am", path)
        except subprocess.CalledProcessError:
            self.git_cmd("am", "--abort")
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
                    logging.warning("Bisected, bad commit: %s" % binfo)
                    break
                else:
                    logging.info(line)

        return (ret, binfo)


class kbuilder(object):
    def __init__(self, path, basecfg, cfgtype=None, makeopts=None,
                 enable_debuginfo=False):
        # FIXME Move expansion up the call stack, as this limits the class
        # usefulness, because tilde is a valid path character.
        self.path = os.path.expanduser(path)
        # FIXME Move expansion up the call stack, as this limits the class
        # usefulness, because tilde is a valid path character.
        self.basecfg = os.path.expanduser(basecfg)
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
            m = re.match('^\d+\.\d+\.\d+.*$', line)
            if m:
                krelease = m.group()
                break

        if krelease is None:
            raise Exception("Failed to find kernel release in stdout")

        return krelease

    def mktgz(self, clean=True):
        tgzpath = None
        self.prepare(clean)

        args = self.defmakeargs + ["INSTALL_MOD_STRIP=1",
                                   "-j%d" % multiprocessing.cpu_count(),
                                   "targz-pkg"]

        logging.info("building kernel: %s", args)

        mk = subprocess.Popen(args,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT)
        (stdout, stderr) = mk.communicate()
        for line in stdout.split("\n"):
            m = re.match("^Tarball successfully created in (.*)$", line)
            if m:
                tgzpath = m.group(1)
                break

        fpath = None
        if tgzpath is not None:
            fpath = "/".join([self.path, tgzpath])

        if fpath is None or not os.path.isfile(fpath):
            with open(self.buildlog, "w") as fp:
                fp.write(stdout)

            raise Exception("Failed to find tgz path in stdout")

        return fpath

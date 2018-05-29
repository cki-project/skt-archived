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
"""Class for managing a kernel source tree"""
import logging
import os
import re
import shutil
import subprocess

import skt


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
        (stdout, _) = grs.communicate()

        return int(stdout.rstrip())

    def get_commit_hash(self, ref=None):
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
        (stdout, _) = grs.communicate()

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

        head = self.get_commit_hash()
        self.info.append(("base", self.uri, head))
        logging.info("baserepo %s: %s", self.ref, head)
        return str(head).rstrip()

    def cleanup(self):
        logging.info("cleaning up %s", self.wdir)
        shutil.rmtree(self.wdir)

    def get_remote_url(self, remote):
        rurl = None
        grs = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "remote", "show", remote],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        (stdout, _) = grs.communicate()
        for line in stdout.split("\n"):
            m = re.match('Fetch URL: (.*)', line)
            if m:
                rurl = m.group(1)
                break

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
            head = self.get_commit_hash(dstref)
            self.info.append(("git", uri, head))
            logging.info("%s %s: %s", rname, ref, head)
        except subprocess.CalledProcessError:
            logging.warning("failed to merge '%s' from %s, skipping", ref,
                            rname)
            self.git_cmd("reset", "--hard")
            return (1, None)

        return (0, head)

    def merge_patchwork_patch(self, uri):
        patch_content = skt.get_patch_mbox(uri)

        logging.info("Applying %s", uri)

        gam = subprocess.Popen(
            ["git", "am", "-"],
            cwd=self.wdir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, **{'LC_ALL': 'C'})
        )

        (stdout, _) = gam.communicate(patch_content)
        retcode = gam.wait()

        if retcode != 0:
            self.git_cmd("am", "--abort")

            with open(self.mergelog, "w") as fileh:
                fileh.write(stdout)

            raise Exception("Failed to apply patch %s" %
                            os.path.basename(os.path.normpath(uri)))

        patchname = skt.get_patch_name(patch_content)
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

            with open(self.mergelog, "w") as fileh:
                fileh.write(exc.output)

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
        (stdout, _) = gbs.communicate()

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
        (stdout, _) = gbs.communicate()

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

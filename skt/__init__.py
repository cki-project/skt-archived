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

import logging
import multiprocessing
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import xmlrpclib


def stringify(v):
    """Convert any value to a str object

    xmlrpc is not consistent: sometimes the same field
    is returned a str, sometimes as unicode. We need to
    handle both cases properly.
    """
    if type(v) is str:
        return v
    elif type(v) is unicode:
        return v.encode('utf-8')
    else:
        return str(v)


# PatchWork adds a magic API version with each call
# this class just magically adds/removes it
class RpcWrapper:
    def __init__(self, real_rpc):
        self.rpc = real_rpc
        # patchwork api coded to
        self.version = 1010

    def _wrap_call(self, rpc, name):
        # Wrap a RPC call, adding the expected version number as argument
        fn = getattr(rpc, name)

        def wrapper(*args, **kwargs):
            return fn(self.version, *args, **kwargs)
        return wrapper

    def _return_check(self, r):
        # Returns just the real return value, without the version info.
        v = self.version
        if r[0] != v:
            raise RpcProtocolMismatch(
                'Patchwork API mismatch (%i, expected %i)' % (r[0], v)
            )
        return r[1]

    def _return_unwrapper(self, fn):
        def unwrap(*args, **kwargs):
            return self._return_check(fn(*args, **kwargs))
        return unwrap

    def __getattr__(self, name):
        # Add the RPC version checking call/return wrappers
        return self._return_unwrapper(self._wrap_call(self.rpc, name))


# FIXME This doesn't just "parse" patchwork URL. Rename/refactor.
def parse_patchwork_url(uri):
    """
    Create a Patchwork XML RPC interface and extract the patch ID from a
    Patchwork patch URL.

    Args:
        uri:    The Patchwork patch URL.

    Returns:
        The created Patchwork XML RPC interface and the patch ID string.

    Raises:
        str: URL format was not recognized, or an XML RPC
             compatibility/communication error has occurred and the string
             describes it.
    """
    m = re.match("^(.*)/patch/(\d+)/?$", uri)
    if not m:
        raise Exception("Can't parse patchwork url: '%s'" % uri)

    baseurl = m.group(1)
    patchid = m.group(2)

    rpc = xmlrpclib.ServerProxy("%s/xmlrpc/" % baseurl)
    try:
        ver = rpc.pw_rpc_version()
        # check for normal patchwork1 xmlrpc version numbers
        if not (ver == [1, 3, 0] or ver == 1):
            raise Exception("Unknown xmlrpc version %s", ver)

    except xmlrpclib.Fault as err:
        if err.faultCode == 1 and \
           re.search("index out of range", err.faultString):
            # possible internal RH instance
            rpc = RpcWrapper(rpc)
            ver = rpc.pw_rpc_version()
            if ver < 1010:
                raise Exception("Unsupported xmlrpc version %s", ver)
        else:
            raise Exception("Unknown xmlrpc fault: %s", err.faultString)

    return (rpc, patchid)


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
        Return:
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
        Return:
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
        (rpc, patchid) = parse_patchwork_url(uri)

        patchinfo = rpc.patch_get(patchid)

        if not patchinfo:
            raise Exception(
                "Failed to fetch patch info for patch %s" % patchid
            )

        pdata = stringify(rpc.patch_get_mbox(patchid))

        logging.info("Applying %s", uri)

        gam = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "am", "-"],
                               stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)

        (stdout, stderr) = gam.communicate(pdata)
        retcode = gam.wait()

        if retcode != 0:
            self.git_cmd("am", "--abort")

            with open(self.mergelog, "w") as fp:
                fp.write(stdout)

            raise Exception("Failed to apply patch %s" % patchid)

        self.info.append(("patchwork", uri,
                          patchinfo.get("name").replace(',', ';')))

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
    def __init__(self, path, basecfg, cfgtype=None, makeopts=None):
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
        if mk.returncode != 0:
            raise Exception("Kernel release lookup command failed: {0}".format(
                " ".join(args)))

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
        stdout_fd = mk.stdout.fileno()
        stdout = ""
        while stdout_fd > -1:
            ret = select.select([stdout_fd], [], [], 10)
            for fd in ret[0]:
                if fd == stdout_fd:
                    data = os.read(fd, 1024)
                    if not data:
                        stdout_fd = -1
                        mk.stdout.close()
                    sys.stdout.write(data)
                    stdout += data.decode('utf-8', 'replace')

        mk.wait()
        if mk.returncode != 0:
            raise Exception("Kernel build command failed: {0}".format(
                            " ".join(args)))

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

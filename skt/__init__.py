# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted material
# is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import logging
import multiprocessing
import re
import shutil
import subprocess
import tempfile
import os

class ktree(object):
    def __init__(self, uri, ref=None, wdir=None):
        self.wdir = os.path.expanduser(wdir) if wdir != None else tempfile.mkdtemp()
        self.gdir = "%s/.git" % self.wdir
        self.uri = uri
        self.ref = ref if ref != None else "master"
        self.info = []

        try:
            os.mkdir(self.wdir)
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

    def get_head(self, ref):
        head = None
        with open(os.path.join(self.gdir, ref), 'r') as f:
            head = f.readline().rstrip()

        return head

    def checkout(self):
        dstref = "refs/remotes/origin/%s" % (self.ref.split('/')[-1])
        logging.info("fetching base repo")
        self.git_cmd("fetch", "-n", "origin",
                     "+%s:%s" %
                      (self.ref, dstref))

        logging.info("checking out %s", self.ref)
        self.git_cmd("reset", "--hard", dstref)

        head = self.get_head(dstref)
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
                                   stdout = subprocess.PIPE,
                                   stderr = subprocess.PIPE)
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
        rname = uri.split('/')[-1].replace('.git', '') if not uri.endswith('/') else uri.split('/')[-2].replace('.git', '')
        while self.get_remote_url(rname) == uri:
            logging.warning("remote '%s' already exists with a different uri, adding '_'" % rname)
            rname += '_'

        return rname

    def merge_git_ref(self, uri, ref="master"):
        rname = self.getrname(uri)
        head = None

        try:
            self.git_cmd("remote", "add", rname, uri, stderr = subprocess.PIPE)
        except subprocess.CalledProcessError:
            pass

        dstref = "refs/remotes/%s/%s" % (rname, ref.split('/')[-1])
        logging.info("fetching %s", dstref)
        self.git_cmd("fetch", "-n", rname,
                     "+%s:%s" %
                      (ref, dstref))

        logging.info("merging %s: %s", rname, ref)
        try:
            grargs = { 'stdout' : subprocess.PIPE } if \
                logging.getLogger().level > logging.DEBUG else {}

            self.git_cmd("merge", "--no-edit", dstref, **grargs)
            head = self.get_head(dstref)
            self.info.append(("git", uri, head))
            logging.info("%s %s: %s", rname, ref, head)
        except subprocess.CalledProcessError:
            logging.warning("failed to merge '%s' from %s, skipping", ref,
                            rname)
            self.git_cmd("reset", "--hard")
            return (1, None)

        return (0, head)

    def merge_patchwork_patch(self, uri):
        pass

    def bisect_start(self, good):
        os.chdir(self.wdir)
        binfo = None
        gbs = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "bisect", "start", "HEAD", good],
                               stdout = subprocess.PIPE)
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
                               stdout = subprocess.PIPE)
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
    def __init__(self, path, basecfg, cfgtype = None):
        self.path = os.path.expanduser(path)
        self.basecfg = os.path.expanduser(basecfg)
        self.cfgtype = cfgtype if cfgtype != None else "olddefconfig"
        self._ready = 0

        logging.info("basecfg: %s", self.basecfg)
        logging.info("cfgtype: %s", self.cfgtype)

    def prepare(self, clean=True):
        if (clean):
            logging.info("cleaning up tree with mrproper")
            subprocess.check_call(["make", "-C", self.path, "mrproper"])
        shutil.copyfile(self.basecfg, "%s/.config" % self.path)
        logging.info("prepare config: make %s", self.cfgtype)
        subprocess.check_call(["make", "-C", self.path, self.cfgtype])
        self._ready = 1

    def getrelease(self):
        krelease = None
        if not self._ready:
            self.prepare(False)

        mk = subprocess.Popen(["make",
                               "-C",
                               self.path,
                               "kernelrelease"],
                              stdout = subprocess.PIPE)
        (stdout, stderr) = mk.communicate()
        for line in stdout.split("\n"):
            m = re.match('^\d+\.\d+\.\d+.*$', line)
            if m:
                krelease = m.group()
                break

        if krelease == None:
            raise Exception("Failed to find kernel release in stdout")

        return krelease


    def mktgz(self, clean=True):
        tgzpath = None
        self.prepare(clean)
        logging.info("building kernel")
        mk = subprocess.Popen(["make",
                               "INSTALL_MOD_STRIP=1",
                               "-j%d" % multiprocessing.cpu_count(),
                               "-C", self.path,
                               "targz-pkg"],
                              stdout = subprocess.PIPE)
        (stdout, stderr) = mk.communicate()
        for line in stdout.split("\n"):
            m = re.match("^Tarball successfully created in (.*)$", line)
            if m:
                tgzpath = m.group(1)
                break

        if tgzpath == None:
            raise Exception("Failed to find tgz path in stdout")

        return "/".join([self.path, tgzpath])

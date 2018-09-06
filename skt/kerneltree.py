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
"""Class for managing a kernel source tree."""
import logging
import os
import re
import subprocess

from skt.misc import join_with_slash, get_patch_mbox, SKT_SUCCESS, SKT_FAIL


class KernelTree(object):
    """
    KernelTree - a kernel git repository "checkout", i.e. a clone with a
    working directory.
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
        self.gdir = join_with_slash(self.wdir, ".git")
        # The origin remote's URL
        self.uri = uri
        # The remote reference to checkout
        self.ref = ref if ref is not None else "master"
        self.mergelog = join_with_slash(self.wdir, "merge.log")
        self.fetch_depth = fetch_depth

        try:
            os.mkdir(self.wdir)
        except OSError:
            pass

        # Initialize the repository
        self.__setup_repository()

        logging.info("base repo url: %s", self.uri)
        logging.info("base ref: %s", self.ref)
        logging.info("work dir: %s", self.wdir)

    def __git_cmd_call(self, func, *args, **kwargs):
        """
        Call a subprocess-module-compatible function with arguments required
        to run a git command.

        Args:
            func:       The subprocess-module-compatible function to call.
            *args:      Git command arguments.
            **kwargs:   Keyword arguments to the function to call.

        Returns:
            The function return value.
        """
        base_argv = [
            "git",
            "--work-tree", self.wdir,
            "--git-dir", self.gdir,
            "-c", "user.name=skt",
            "-c", "user.email=skt",
        ]
        cmd_args = list(base_argv) + list(args)

        logging.debug("executing: %s", " ".join(cmd_args))
        return func(
            cmd_args,
            env=dict(os.environ, **{'LC_ALL': 'C'}),
            stderr=subprocess.STDOUT,
            **kwargs
        )

    def __git_cmd(self, *args, **kwargs):
        """
        Run a git command and return its output.

        Args:
            *args:      Git command arguments.
            **kwargs:   Extra keyword arguments to subprocess.check_output.

        Returns:
            Git command output.
        """
        try:
            return self.__git_cmd_call(subprocess.check_output,
                                       *args,
                                       **kwargs)
        except subprocess.CalledProcessError as exc:
            logging.debug(exc.output)
            raise exc

    def __git_cmd_pipe(self, command_input, *args, **kwargs):
        """
        Feed input to a git command and return its output.

        Args:
            command_input:      Input to feed to the git command.
            *args:              Git command arguments.
            **kwargs:           Extra keyword arguments to subprocess.Popen.

        Returns:
            Git command exit status and output.
        """
        process = self.__git_cmd_call(subprocess.Popen,
                                      *args,
                                      stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,
                                      **kwargs)
        (output, _) = process.communicate(command_input)
        status = process.wait()
        return status, output

    def getpath(self):
        return self.wdir

    def __setup_repository(self):
        """Initialize the repo and set the origin."""
        self.__git_cmd("init")

        # Does the repo have an origin set?
        if 'origin' in self.__git_cmd("remote").split('\n'):
            # Ensure the origin is set to the correct URL
            logging.debug("Setting URL for remote 'origin': %s", self.uri)
            self.__git_cmd("remote", "set-url", "origin", self.uri)
        else:
            # Add the origin remote
            logging.debug("Adding missing remote 'origin': %s", self.uri)
            self.__git_cmd("remote", "add", "origin", self.uri)

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
        dstref = join_with_slash("refs", "remotes", "origin",
                                 self.ref.split('/')[-1])
        logging.info("fetching base repo")
        git_fetch_args = [
            "fetch", "origin",
            "+%s:%s" % (self.ref, dstref)
        ]
        # If the user provided extra arguments for the git fetch step, append
        # them to the existing set of arguments.
        if self.fetch_depth:
            git_fetch_args.extend(['--depth', self.fetch_depth])

        # The __git_cmd() method expects a list of args, not a list of strings,
        # so we need to expand our list into args with *.
        self.__git_cmd(*git_fetch_args)

        logging.info("checking out %s", self.ref)
        self.__git_cmd("checkout", "-q", "--detach", dstref)
        self.__git_cmd("reset", "--hard", dstref)

        head = self.get_commit_hash()
        logging.info("baserepo %s: %s", self.ref, head)
        return str(head).rstrip()

    def __get_remote_url(self, remote):
        """
        Get the URL of a specific remote.

        Args:
            remote: The name of the remote.

        Returns:
            URL of the remote, None if the remote doesn't exist.
        """
        grs = subprocess.Popen(["git",
                                "--work-tree", self.wdir,
                                "--git-dir", self.gdir,
                                "remote", "show", remote],
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        (stdout, _) = grs.communicate()
        for line in stdout.split("\n"):
            match = re.match('Fetch URL: (.*)', line)
            if match:
                return match.group(1)
        return None

    def __get_remote_name(self, uri):
        remote_name = (uri.split('/')[-1].replace('.git', '')
                       if not uri.endswith('/')
                       else uri.split('/')[-2].replace('.git', ''))
        while self.__get_remote_url(remote_name) == uri:
            logging.warning(
                "remote '%s' already exists with a different uri, adding '_'",
                remote_name
            )
            remote_name += '_'

        return remote_name

    def merge_git_ref(self, uri, ref="master"):
        remote_name = self.__get_remote_name(uri)
        head = None

        try:
            self.__git_cmd("remote", "add", remote_name, uri)
        except subprocess.CalledProcessError:
            pass

        dstref = join_with_slash("refs",
                                 "remotes",
                                 remote_name,
                                 ref.split('/')[-1])
        logging.info("fetching %s", dstref)
        self.__git_cmd("fetch", remote_name,
                       "+%s:%s" % (ref, dstref))

        logging.info("merging %s: %s", remote_name, ref)
        try:
            grargs = {'stdout': subprocess.PIPE} if \
                logging.getLogger().level > logging.DEBUG else {}

            self.__git_cmd("merge", "--no-edit", dstref, **grargs)
            head = self.get_commit_hash(dstref)
            logging.info("%s %s: %s", remote_name, ref, head)
        except subprocess.CalledProcessError:
            logging.warning("failed to merge '%s' from %s, skipping", ref,
                            remote_name)
            self.__git_cmd("reset", "--hard")
            return (SKT_FAIL, None)

        return (SKT_SUCCESS, head)

    def merge_patchwork_patch(self, uri):
        patch_content = get_patch_mbox(uri)

        logging.info("Applying %s", uri)

        # Run in workdir to workaround "git am" ignoring --work-tree
        status, output = self.__git_cmd_pipe(patch_content,
                                             "am", "-",
                                             cwd=self.wdir)
        if status != 0:
            logging.error('Patch application failed with:\n%s', output)

            self.__git_cmd("am", "--abort", cwd=self.wdir)

            with open(self.mergelog, "w") as fileh:
                fileh.write(output)

            raise PatchApplicationError(
                "Failed to apply patch %s" %
                os.path.basename(os.path.normpath(uri))
            )

    def merge_patch_file(self, path):
        if not os.path.exists(path):
            raise Exception("Patch %s not found" % path)

        # Run in workdir to workaround "git am" ignoring --work-tree
        try:
            self.__git_cmd("am", path, cwd=self.wdir)
        except subprocess.CalledProcessError as exc:
            logging.error('Patch application failed with:\n%s', exc.output)

            self.__git_cmd("am", "--abort", cwd=self.wdir)

            with open(self.mergelog, "w") as fileh:
                fileh.write(exc.output)

            raise PatchApplicationError("Failed to apply patch %s" % path)


class PatchApplicationError(Exception):
    """Exception raised when the patch fails to apply."""

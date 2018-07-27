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
import os
import shutil
import subprocess

from skt.misc import join_with_slash


class Publisher(object):
    """An abstract result publisher."""
    # TODO This probably shouldn't be here as we never use it, and it should
    # not be inherited
    TYPE = 'default'

    def __init__(self, dest, url):
        """
        Initialize an abstract result publisher.

        Args:
            dest:   Type-specific destination string.
            url:    Base URL prefix of the published result,
                    without '/' on the end.
        """
        self.destination = dest
        self.baseurl = url

        logging.info("publisher type: %s", self.TYPE)
        logging.info("publisher destination: %s", self.destination)

    def geturl(self, source):
        """
        Get published URL for a source file path.

        Args:
            source: Source file path.

        Returns:
            Published URL corresponding to the specified source.
        """
        return join_with_slash(self.baseurl, os.path.basename(source))

    # TODO Define abstract "publish" method.


class CpPublisher(Publisher):
    TYPE = 'cp'

    def publish(self, source):
        """
        Copy the source file to public destination.

        Args:
            source: Source file path.

        Returns:
            Published URL corresponding to the specified source.
        """
        shutil.copy(source, self.destination)
        return self.geturl(source)


class ScpPublisher(Publisher):
    TYPE = 'scp'

    def publish(self, source):
        """
        Copy the source file to public destination.

        Args:
            source: Source file path.

        Returns:
            Published URL corresponding to the specified source.
        """
        subprocess.check_call(["scp", source, self.destination])
        return self.geturl(source)


class SftpPublisher(Publisher):
    TYPE = 'sftp'

    def publish(self, source):
        """
        Copy the source file to public destination.

        Args:
            source: Source file path.

        Returns:
            Published URL corresponding to the specified source.
        """
        sp = subprocess.Popen(['sftp', self.destination],
                              shell=False, stdin=subprocess.PIPE)
        sp.stdin.write("put -r %s\n" % source)
        sp.stdin.close()
        sp.wait()
        return self.geturl(source)


def getpublisher(ptype, parg, pburl):
    """
    Create an instance of a "publisher" subclass with specified arguments.

    Args:
        rtype:  The value of the class "TYPE" member to match.
        rarg:   A dictionary with the instance creation arguments.

    Returns:
        The created class instance.

    Raises:
        ValueError if the rtype match wasn't found.
    """
    for cls in Publisher.__subclasses__():
        if cls.TYPE == ptype:
            return cls(parg, pburl)
    raise ValueError("Unknown publisher type: %s" % ptype)

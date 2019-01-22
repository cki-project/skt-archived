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
"""Class for managing Publisher."""
import logging
import os
import subprocess

from skt.misc import join_with_slash


class Publisher(object):
    """Publish artifacts to destinations.."""

    def __init__(self, pub_type=None, source=None, dest=None, baseurl=None):
        """
        Initialize an abstract result publisher.

        Args:
            pub_type:   A type of publisher found in this class.
            source:     Current location of the artifact to be published.
            dest:       A destination for the artifact.
            baseurl:    Base URL of the published result without a
                        trailing slash.
        """
        self.pub_type = pub_type
        self.source = source
        self.destination = join_with_slash(dest, "")
        self.baseurl = baseurl

    def publish(self):
        """
        Publish an artifact and return the URL to the file.

        Returns:
            Published URL corresponding to the specified source.

        """
        # NOTE(mhayden): scp can handle local file copies properly. Both
        # 'cp' and 'scp' are left here for backwards compatibility.
        if self.pub_type in ['cp', 'scp']:
            self.scp()
        else:
            raise(
                KeyError,
                "Publisher type not supported: {}".format(self.pub_type)
            )

        return self.geturl()

    def geturl(self):
        """
        Get published URL for a source file path.

        Args:
            source: Source file path.

        Returns:
            Published URL corresponding to the specified source.

        """
        return join_with_slash(self.baseurl, os.path.basename(self.source))

    def scp(self):
        """Use scp to copy the source to the destination."""
        # Create the destination directory.
        if not os.path.isdir(self.destination):
            os.makedirs(self.destination)

        # Copy the files.
        logging.debug(
            "copying file with scp: %s > %s",
            self.source,
            self.destination
        )
        args = ['scp', '-r', self.source, self.destination]
        subprocess.check_call(args)

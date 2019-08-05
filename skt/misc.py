# Copyright (c) 2017-2018 Red Hat, Inc. All rights reserved. This copyrighted
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
"""Functions and constants used by multiple parts of skt."""
import logging
import subprocess


# SKT Result
SKT_SUCCESS = 0
SKT_FAIL = 1
SKT_ERROR = 2
SKT_BOOT = 3

LOGGER = logging.getLogger()


def shell_run(cmd, stdin_data=None, **kwargs):
    """ Runs a shell command with specified stdin data and keyword args.

        Args:
            stdin_data: None or str, use None when you don't want to input
                        stdin string
            kwargs:     keyword arguments to pass to Popen
    """
    subproc = subprocess.Popen(cmd, **kwargs)

    out, cmd_err = subproc.communicate(stdin_data)

    out = out.decode('utf-8')
    cmd_err = cmd_err.decode('utf-8')
    return out, cmd_err, subproc.returncode


class WaivingWrap(object):
    """ This handles getting/updating waived data and simplifies mocking."""
    # pylint: disable=too-few-public-methods
    def __init__(self, waiving):
        self.waiving = waiving

    @classmethod
    def is_task_waived(cls, task):
        """ Check XML param to see if the test is waived.
            Args:
                task: xml node

            Returns: True if test is waived, otherwise False
        """
        is_task_waived = False
        for param in task.findall('.//param'):
            try:
                if param.attrib.get('name').lower() == 'cki_waived' and \
                        param.attrib.get('value').lower() == 'true':
                    is_task_waived = True
                    break
            except ValueError:
                pass

        return is_task_waived

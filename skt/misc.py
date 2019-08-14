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


def safe_popen(*args, stdin_data=None, **kwargs):
    """ Open a process with specified arguments, keyword arguments and
        possibly stdin data. This function blocks until process finishes. Uses
        utf-8 to decode stdout/stderr, if there's any output on them.
        Intended as a common interface to bkr or other shell commands skt uses.

        Args:
            args:       arguments to pass to Popen
            stdin_data: None or str, use None when you don't want to pass
                        string data to stdin
            kwargs:     keyword arguments to pass to Popen
        Returns:
            tuple (stdout, stderr, returncode) where
                stdout is a string
                stderr is a string
                returncode is an integer
    """
    subproc = subprocess.Popen(*args, **kwargs)

    stdout, stderr = subproc.communicate(stdin_data)
    stdout = stdout.decode('utf-8') if stdout else ''
    stderr = stderr.decode('utf-8') if stderr else ''

    return stdout, stderr, subproc.returncode


def is_task_waived(task):
    """ Check XML param to see if the test is waived.
        Args:
            task: xml node

        Returns: True if test is waived, otherwise False
    """
    is_task_waived_val = False
    for param in task.findall('.//param'):
        try:
            if param.attrib.get('name').lower() == 'cki_waived' and \
                    param.attrib.get('value').lower() == 'true':
                is_task_waived_val = True
                break
        except ValueError:
            pass

    return is_task_waived_val

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


"""SKT Result"""
SKT_SUCCESS = 0
SKT_FAIL = 1
SKT_ERROR = 2


def join_with_slash(base, *suffix_tuple):
    """
    Join parts of URL or path by slashes. Trailing slash of base, and each
    arg in suffix_tupple are removed. It only keeps trailing slash at the
    end of the part if it is specified.

    Args:
        base:          Base URL or path.
        *suffix_tuple: Tuple of suffixes

    Returns:
        The URL or path string
    """
    parts = [base.rstrip('/')]
    for arg in suffix_tuple:
        parts.append(arg.strip('/'))
    ending = '/' if arg.endswith('/') else ''
    return '/'.join(parts) + ending

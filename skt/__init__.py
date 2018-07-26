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
"""Functions used by multiple parts of skt."""
import requests

from skt.misc import join_with_slash


def get_patch_mbox(url):
    """
    Retrieve a string representing mbox of the patch.

    Args:
        url: Patchwork URL of the patch to retrieve

    Returns:
        String representing body of the patch mbox

    Raises:
        Exception in case the URL is currently unavailable or invalid
    """
    mbox_url = join_with_slash(url, 'mbox')

    try:
        response = requests.get(mbox_url)
    except requests.exceptions.RequestException as exc:
        raise exc

    if response.status_code != requests.codes.ok:
        raise Exception('Failed to retrieve patch from %s, returned %d' %
                        (url, response.status_code))

    return response.content

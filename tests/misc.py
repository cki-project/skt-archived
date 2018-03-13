"""
Miscellaneous for tests.
"""
# Copyright (c) 2018 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General Public
# License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
import os


ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')


def get_asset_path(filename):
    """Return the absolute path of an asset passed as parameter.

    Args:
        filename: Asset's filename.
    Returns:
        The absolute path of the corresponding asset.
    """
    return os.path.join(ASSETS_DIR, filename)


def get_asset_content(filename):
    """Return the content of an asset passed as parameter.

    Args:
        filename: Asset's filename.
    Returns:
        The content of the corresponding asset.
    """
    with open(get_asset_path(filename)) as asset:
        return asset.read()

"""
Test cases for __init__.py.
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
import unittest

import mock

import skt


def mocked_requests_get(*args):
    """Function to handle mocked HTTP requests"""
    class MockResponse(object):
        # pylint: disable=too-few-public-methods
        """MockResponse class for returning HTTP requests"""
        def __init__(self, text_data, status_code):
            self.text = text_data
            self.status_code = status_code

    if args[0] == 'https://patchwork.kernel.org/patch/10326957/mbox/':
        return MockResponse("mbox text goes here", 200)

    return MockResponse(None, 404)


class TestInit(unittest.TestCase):
    """Test cases for skt's __init__.py"""

    @mock.patch('requests.get', side_effect=mocked_requests_get)
    def test_get_patchwork_mbox(self, mock_get):
        # pylint: disable=unused-argument
        """Ensure get_patchwork_mbox() can get an mbox file"""
        patchwork_uri = 'https://patchwork.kernel.org/patch/10326957/'
        result = skt.get_patchwork_mbox(patchwork_uri)
        self.assertEqual(result, 'mbox text goes here')

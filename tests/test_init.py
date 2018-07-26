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
"""Test cases for __init__.py."""
from __future__ import division
import unittest

import requests
import responses

import skt


class TestIndependent(unittest.TestCase):
    """Test cases for independent functions in __init__.py."""

    @responses.activate
    def test_get_patch_mbox(self):
        """Ensure get_patch_mbox() succeeds with a good request."""
        responses.add(
            responses.GET,
            'http://patchwork.example.com/patch/1/mbox',
            json={'result': 'good'},
            status=200
        )

        resp = skt.get_patch_mbox('http://patchwork.example.com/patch/1')
        self.assertEqual('{"result": "good"}', resp)

    @responses.activate
    def test_get_patch_mbox_fail(self):
        """Ensure get_patch_mbox() handles an exception from requests."""
        responses.add(
            responses.GET,
            'http://patchwork.example.com/patch/1/mbox',
            body=requests.exceptions.RequestException('Fail'),
        )

        with self.assertRaises(requests.exceptions.RequestException):
            skt.get_patch_mbox('http://patchwork.example.com/patch/1')

    @responses.activate
    def test_get_patch_mbox_bad_status(self):
        """Ensure get_patch_mbox() handles a bad status code."""
        responses.add(
            responses.GET,
            'http://patchwork.example.com/patch/1/mbox',
            json={'error': 'failure'},
            status=500
        )

        with self.assertRaises(Exception):
            skt.get_patch_mbox('http://patchwork.example.com/patch/1')

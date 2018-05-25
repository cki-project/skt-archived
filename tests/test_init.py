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
from __future__ import division
import unittest

from requests.exceptions import RequestException

import skt


class TestIndependent(unittest.TestCase):
    """Test cases for independent functions in __init__.py"""

    def test_invalid_patch_url(self):
        """Ensure get_patch_mbox() throws exception if the URL is invalid"""
        self.assertRaises(RequestException,
                          skt.get_patch_mbox,
                          'this-is-invalid')

    def test_nonexistent_patch_subject(self):
        """Ensure get_patch_name() handles nonexistent 'Subject' in mbox"""
        mbox_body = 'nothing useful here'
        self.assertEqual('<SUBJECT MISSING>', skt.get_patch_name(mbox_body))

    def test_ok_patch_subject(self):
        """Ensure get_patch_name() returns correct 'Subject' if present"""
        mbox_body = 'From Test Thu May 2 17:49:51 2018\nSubject: GOOD SUBJECT'
        self.assertEqual('GOOD SUBJECT', skt.get_patch_name(mbox_body))

    def test_encoded_patch_subject(self):
        """Ensure get_patch_name() correctly decodes UTF-8 'Subject'"""
        mbox_body = ('From Test Thu May 2 17:49:51 2018\n'
                     'Subject: =?utf-8?q?=5BTEST=5D?=')
        self.assertEqual('[TEST]', skt.get_patch_name(mbox_body))

    def test_multipart_encoded_subject(self):
        """
        Ensure get_patch_name() correctly decodes multipart encoding
        of 'Subject'
        """
        mbox_body = ('From Test Thu May 2 17:49:51 2018\nSubject: '
                     '=?ISO-8859-1?B?SWYgeW91IGNhbiByZWFkIHRoaXMgeW8=?=\n'
                     '    =?ISO-8859-2?B?dSB1bmRlcnN0YW5kIHRoZSBleGFtcGxlLg'
                     '==?=')
        self.assertEqual('If you can read this you understand the example.',
                         skt.get_patch_name(mbox_body))

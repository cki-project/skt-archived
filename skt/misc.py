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
import cookielib
from email.errors import HeaderParseError
import email.header
import email.parser
import re

import requests


# SKT Result
SKT_SUCCESS = 0
SKT_FAIL = 1
SKT_ERROR = 2


class SoakWrap(object):
    """ This handles getting/updating soaking data and simplifies mocking."""
    # pylint: disable=too-few-public-methods
    def __init__(self, soak):
        self.soak = soak

    @classmethod
    def is_soaking(cls, task):
        """ Check XML param to see if the test is being soaked.
            Args:
                task: xml node

            Returns: True if test is soaking, otherwise False
        """
        is_soaking = False
        for param in task.findall('.//param'):
            try:
                if param.attrib.get('name').lower() == '_waived' and \
                        param.attrib.get('value').lower() == 'true':
                    is_soaking = True
                    break
            except ValueError:
                pass

        return is_soaking


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
    ending = '/' if suffix_tuple[-1].endswith('/') else ''
    return '/'.join(parts) + ending


def get_patch_name(content):
    """
    Retrieve patch name from 'Subject' header from the mbox string
    representing a patch.

    Args:
        content: String representing patch mbox

    Returns:
        Name of the patch. <SUBJECT MISSING> is returned if no subject is
        found, and <SUBJECT ENCODING INVALID> if header decoding fails.
    """
    headers = email.parser.Parser().parsestr(content, True)
    subject = headers['Subject']
    if not subject:
        # Emails return None if the header is not found so use a stub subject
        # instead of it
        return '<SUBJECT MISSING>'

    # Remove header folding
    subject = re.sub(r'\r?\n[ \t]', ' ', subject)

    try:
        # decode_header() returns a list of tuples (value, charset)
        decoded = [value for value, _ in email.header.decode_header(subject)]
    except HeaderParseError:
        # We can't parse the original subject so use a stub one instead
        return '<SUBJECT ENCODING INVALID>'

    return ''.join(decoded)


def get_patch_mbox(url, session_cookie=None):
    """
    Retrieve a string representing mbox of the patch.

    Args:
        url:            Patchwork URL of the patch to retrieve.
        session_cookie: Patchwork session cookie, in case login is required.

    Returns:
        String representing body of the patch mbox

    Raises:
        Exception in case the URL is currently unavailable or invalid
    """
    # pylint: disable=no-member
    mbox_url = join_with_slash(url, 'mbox')

    # Get cookies if specified
    cookie_jar = None
    if session_cookie:
        patchwork_domain = url.rsplit('patch', 1)[0].strip('/').split('/')[-1]
        cookie = cookielib.Cookie(0, 'sessionid', session_cookie, None, False,
                                  patchwork_domain, False, False, '/', False,
                                  False, None, False, None, None, {})
        cookie_jar = cookielib.CookieJar()
        cookie_jar.set_cookie(cookie)

    try:
        response = requests.get(mbox_url, cookies=cookie_jar)
    except requests.exceptions.RequestException as exc:
        raise exc

    if response.status_code != requests.codes.ok:
        raise Exception('Failed to retrieve patch from %s, returned %d' %
                        (url, response.status_code))

    return response.content

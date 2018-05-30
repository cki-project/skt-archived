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
"""Class for working with the kernel source tree."""
from email.errors import HeaderParseError
import email.header
import email.parser
import os
import requests


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
    # Use os.path for manipulation with URL because urlparse can't deal
    # with URLs ending both with and without slash.
    mbox_url = os.path.join(url, 'mbox')

    try:
        response = requests.get(mbox_url)
    except requests.exceptions.RequestException as exc:
        raise(exc)

    if response.status_code != requests.codes.ok:
        raise Exception('Failed to retrieve patch from %s, returned %d' %
                        (url, response.status_code))

    return response.content


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

    # skt's custom CSV parsing doesn't understand multiline values, until we
    # switch to a proper parser we need a temporary fix. Use separate
    # replacements to handle Windows / *nix endlines and mboxes which contain
    # '\n' and a space instead of '\n\t' as well.
    # Tracking issue: https://github.com/RH-FMK/skt/issues/119
    subject = subject.replace('\n', ' ').replace('\t', '').replace('\r', '')

    try:
        # decode_header() returns a list of tuples (value, charset)
        decoded = [value for value, _ in email.header.decode_header(subject)]
    except HeaderParseError:
        # We can't parse the original subject so use a stub one instead
        return '<SUBJECT ENCODING INVALID>'

    return ''.join(decoded)


class CommandTimeoutError(Exception):
    """
    Exception raised when a timeout occurs on a process which has had timeouts
    enabled. The accompanying value is a string whose value is the command
    launched plus a small explanation.
    """


class ParsingError(Exception):
    """
    Exception raised when a regex does not match and it is impossible to
    continue. The accompanying value is a string which explains what it can not
    find.
    """

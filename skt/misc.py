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
import logging
import os
import re

import redis
import requests


# SKT Result
SKT_SUCCESS = 0
SKT_FAIL = 1
SKT_ERROR = 2


class SoakWrap(object):
    """ This handles getting/updating soaking data and simplifies mocking."""
    def __init__(self, redis_inst):
        # redis instance to allow getting/setting data
        self.redis_inst = redis_inst

    @classmethod
    def is_soaking(cls, task):
        """ Check redis if the test is being soaked.
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

    def insert_test_run(self, name, recipe, date, result):
        """ Insert info about testrun into redis.
            Creates keys like '<testname>:<recipeid>'.
            Args:
                name:   name of the test that ran
                recipe: recipe number like 123456
                date:   date when test ran like "2018-12-03"
                result: testrun result like 'PASS'
        """
        # turns to no-op if we don't have redis connected
        if not self.redis_inst:
            return

        data2insert = {
            "date": date,
            "result": result
        }

        with self.redis_inst.pipeline(transaction=True) as pipe:
            pipe.hmset('{}:{}'.format(name, recipe), data2insert).execute()


def connect_redis(soak):
    """ Connect to redis service inside the container using <REDIS_SERVICE>
    {_SERVICE_HOST,_SERVICE_PORT}.

    Args:
        soak: True if we want to enable soaking and conenct to redis
    Returns:
        SoakWrap object
    """

    redis_inst = None
    redis_service = os.environ.get('REDIS_SERVICE', None)
    if not soak:
        logging.info(
            "Soaking disabled -- "
            "skt will NOT hide failing soaking tests or update stats"
        )
    elif redis_service:
        # Two environment variables should be present:
        #   _SERVICE_HOST -> IP address of redis server
        #   _SERVICE_PORT -> port that redis server listens on
        redis_host = os.environ.get('{}_SERVICE_HOST'.format(redis_service))
        redis_port = os.environ.get('{}_SERVICE_PORT'.format(redis_service))
        logging.info(
            "Found REDIS_SERVICE environment variable -- "
            "skt will hide failing soaking tests"
        )
        redis_inst = redis.Redis(host=redis_host, port=redis_port, db=0)
    else:
        logging.info(
            "Cannot find REDIS_SERVICE environment variable -- "
            "skt will NOT hide failing soaking tests or update stats"
        )

    return SoakWrap(redis_inst)


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

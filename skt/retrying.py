# Copyright (c) 2019 Red Hat, Inc. All rights reserved. This copyrighted
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
"""Retrying decorator to retry method/function several times."""

import time
import logging


def retrying_on_exception(exception, retries=3, initial_delay=3):
    """Decorate method/function to be retried on exception after initial_delay.

    Do retries based on how many were set. The wait delay before next
    attempt is increased by initial_delay each time.

    Arguments:
        exception:     a type of exception to catch, e.g. RuntimeError
        retries:       max. number of times the decorated method will be run
        initial_delay: a number of seconds to wait after first exception;
                       the total number of seconds we wait is increased by this
                       amount after each retry
    """
    def wrapper(function):
        def wrapped(*args, **kwargs):
            wrapped.failed_count = 0
            wrapped.retries = retries

            delay = 0
            for _ in range(0, retries):
                delay += initial_delay

                try:
                    return function(*args, **kwargs)
                except exception:
                    wrapped.failed_count += 1

                    if wrapped.failed_count != retries:
                        logging.warning('RETRY func(%s), delaying for %ds',
                                        ', '.join(list(*args)), delay)
                        time.sleep(delay)
                    else:
                        logging.warning('RETRY giving up on func(%s)',
                                        ', '.join(list(*args)))
                        # don't sleep on last attempt, there's no point;
                        # instead raise exception
                        raise

        return wrapped

    return wrapper

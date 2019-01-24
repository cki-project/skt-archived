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
"""Test cases for console checker module."""
import gzip
import re
import StringIO
import tempfile
import unittest

from contextlib import contextmanager
import mock
import responses

from skt import console
from tests import misc


class TestConsoleLog(unittest.TestCase):
    """Test cases for console.ConsoleLog class."""

    @staticmethod
    @contextmanager
    def request_get_mocked(filename):
        """Mock request.get to allow feeding ConsoleLog with known inputs.

        When request.get is called, it "fetches" the content of the asset
        passed as parameter.

        Args:
            filename: Asset's filename.
        """
        get_mocked = mock.Mock()

        def remove_nt_marker(line):
            """Filter function for removing the 'nt ' markers on assets."""
            return not re.match(r'^nt ', line)
        get_mocked.text = filter(remove_nt_marker,
                                 misc.get_asset_content(filename))
        with mock.patch('requests.get', mock.Mock(return_value=get_mocked)):
            yield

    @staticmethod
    def get_expected_traces(filename):
        """Return expected traces from an asset.

        Each line started with 'nt ' is discarded.

        Args:
            filename: Asset's filename.
        Returns:
            A list where every member is a trace.

        """
        expected_traces = []
        tmp_trace = []
        for line in misc.get_asset_content(filename).splitlines()[1:]:
            if line.startswith('nt '):
                if tmp_trace:
                    expected_traces.append('\n'.join(tmp_trace))
                    tmp_trace = []
            else:
                tmp_trace.append(line)
        expected_traces.append('\n'.join(tmp_trace))
        return expected_traces

    def test_fetchdata(self):
        """Ensure __fetchdata() returns an empty list with no URL."""
        # pylint: disable=W0212,E1101
        consolelog = console.ConsoleLog(kver='4-4', url_or_path=None)
        result = consolelog._ConsoleLog__fetchdata()
        self.assertItemsEqual([], result)

    @responses.activate
    def test_fetchdata_normal(self):
        """Ensure __fetchdata() works."""
        # pylint: disable=W0212,E1101
        responses.add(
            responses.GET,
            'http://whatever.example.com/console/log',
            body=misc.get_asset_content('x86_one_trace.txt'),
            status=200
        )

        # get path to trace asset
        real_path = misc.get_asset_path('x86_one_trace.txt')

        consolelog = console.ConsoleLog(kver='4-5-fake', url_or_path=real_path)
        result = consolelog._ConsoleLog__fetchdata()

        # check we got expected file content
        self.assertIn('Linux version 4-5-fake', result[0])

    @responses.activate
    def test_fetchdata_gz(self):
        """Ensure __fetchdata() gzip handling works."""
        # pylint: disable=W0212,E1101
        content = misc.get_asset_content('x86_one_trace.txt')

        responses.add(
            responses.GET,
            'http://whatever.example.com/console/log.bz',
            body=content,
            status=200
        )

        with tempfile.NamedTemporaryFile(delete=True, suffix='.gz') as temp:
            with gzip.open(temp.name, 'wb') as gzip_file:
                gzip_file.write(content)

            consolelog = console.ConsoleLog(kver='4-5-fake',
                                            url_or_path=temp.name)
            result = consolelog._ConsoleLog__fetchdata()

            # check we got expected file content
            self.assertIn('Linux version 4-5-fake', result[0])

    def test_getfulllog(self):
        """Ensure getfulllog() returns gzipped data."""
        consolelog = console.ConsoleLog(kver='4-4', url_or_path=None)
        consolelog.data = ['foo']
        result = consolelog.getfulllog()

        # Decompress the string and make sure it matches our test data
        tstr = StringIO.StringIO(result)
        with gzip.GzipFile(fileobj=tstr, mode="r") as fileh:
            data_test = fileh.read()

        self.assertEqual(data_test, consolelog.data[0])

    def test_kernel_version_unmatch(self):
        """Ensure gettraces() doesn't catch trace when kver doesn't match."""
        with self.request_get_mocked('x86_one_trace.txt'):
            consolelog = console.ConsoleLog('4-4', 'someurl')
            traces = consolelog.gettraces()
        self.assertListEqual(traces, [])

    def test_match_one_trace(self):
        """Check one trace can be extracted from a console log."""
        with self.request_get_mocked('x86_one_trace.txt'):
            consolelog = console.ConsoleLog('4-5-fake', 'someurl')
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 1)
        expected_trace = self.get_expected_traces('x86_one_trace.txt')[0]
        self.assertEqual(expected_trace, traces[0])

    def test_match_one_trace2(self):
        """ Check one trace can be extracted from a console log. Check
            that exclude pattern is skipped.
        """
        # pylint: disable=W0212,E1101

        # append OK line to the testing content
        content = misc.get_asset_content('x86_one_trace.txt').splitlines()
        content = [content[0], '[166357.530080] aa OK aa'] + content[1:]
        content = '\n'.join(content) + '\n'

        with tempfile.NamedTemporaryFile(delete=True) as temp:
            temp.write(content)
            temp.seek(0)

            consolelog = console.ConsoleLog('4-5-fake', temp.name)
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 1)
        expected_trace = self.get_expected_traces('x86_one_trace.txt')[0]
        self.assertEqual(expected_trace, traces[0])

    def test_match_three_traces(self):
        """Check three traces can be extracted from a console log."""
        with self.request_get_mocked('x86_three_traces.txt'):
            consolelog = console.ConsoleLog('4.16-fake', 'someurl')
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 3)
        expected_traces = self.get_expected_traces('x86_three_traces.txt')
        for idx, trace in enumerate(traces):
            msg = ("Trace_{} doesn't match.\n"
                   "{!r} != {!r}").format(idx, trace, expected_traces[idx])
            self.assertEqual(trace, expected_traces[idx], msg=msg)

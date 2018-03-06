"""
Test cases for reporter module.
"""
# Copyright (c) 2018 Red Hat, Inc. All rights reserved. This copyrighted material
# is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
import unittest
from contextlib import contextmanager
import re
import mock
from skt import reporter
from tests import misc


class TestConsoleLog(unittest.TestCase):
    """Test cases for reporter.consolelog class"""
    @staticmethod
    @contextmanager
    def request_get_mocked(filename):
        """Mock request.get to allow feeding consolelog with known inputs. When
        request.get is called, it "fetches" the content of the asset passed as
        parameter.

        Args:
            filename: Asset's filename.
        """
        get_mocked = mock.Mock()
        def remove_nt_marker(line):
            """Filter function for removing the 'nt ' markers on assets"""
            return not re.match(r'^nt ', line)
        get_mocked.text = filter(remove_nt_marker,
                                 misc.get_asset_content(filename))
        with mock.patch('requests.get', mock.Mock(return_value=get_mocked)):
            yield

    @staticmethod
    def get_expected_traces(filename):
        """Return expected traces from an asset. Each line started with 'nt '
        is discarded.

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

    def test_kernel_version_unmatch(self):
        """Check it doesn't catch any trace when kernel version doesn't match"""
        consolelog = reporter.consolelog('4-4', 'someurl')
        with self.request_get_mocked('x86_one_trace.txt'):
            traces = consolelog.gettraces()
        self.assertListEqual(traces, [])

    def test_match_one_trace(self):
        """Check one trace can be extracted from a console log"""
        consolelog = reporter.consolelog('4-5-fake', 'someurl')
        with self.request_get_mocked('x86_one_trace.txt'):
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 1)
        expected_trace = self.get_expected_traces('x86_one_trace.txt')[0] + '\n'
        self.assertEqual(expected_trace, traces[0])

    def test_match_three_traces(self):
        """Check three traces can be extracted from a console log"""
        consolelog = reporter.consolelog('4.16-fake', 'someurl')
        with self.request_get_mocked('x86_three_traces.txt'):
            traces = consolelog.gettraces()
            self.assertEqual(len(traces), 3)
        expected_traces = self.get_expected_traces('x86_three_traces.txt')
        for idx, trace in enumerate(traces):
            msg = ("Trace_{} doesn't match.\n"
                   "{!r} != {!r}").format(idx, trace, expected_traces[idx])
            self.assertEqual(trace, expected_traces[idx], msg=msg)

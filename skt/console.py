# Copyright (c) 2017-2018 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A  PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
import gzip
import re

import requests
import six


def gzipdata(data):
    """
    Compress a string with gzip.

    Args:
        data:   The string to compress.

    Returns:
        String containing gzip-compressed data.
    """
    tstr = six.BytesIO()
    with gzip.GzipFile(fileobj=tstr, mode="wb") as fileh:
        fileh.write(data.encode('utf-8'))
    return tstr.getvalue()


class ConsoleLog(object):
    """Console log parser"""

    # List of regular expression strings matching
    # lines beginning an oops or a call trace output
    oopsmsg = [
        r"general protection fault:",
        r"BUG:",
        r"kernel BUG at",
        r"do_IRQ: stack overflow:",
        r"RTNL: assertion failed",
        r"Eeek! page_mapcount\(page\) went negative!",
        r"near stack overflow \(cur:",
        r"double fault:",
        r"Badness at",
        r"NETDEV WATCHDOG",
        r"WARNING: at",
        r"appears to be on the same physical disk",
        r"Unable to handle kernel",
        r"sysctl table check failed",
        r"------------\[ cut here \]------------",
        r"list_del corruption\.",
        r"list_add corruption\.",
        r"NMI watchdog: BUG: soft lockup",
        r"irq [0-9]+: nobody cared",
        r"INFO: task .* blocked for more than [0-9]+ seconds",
        r"vmwrite error: reg ",
        r"page allocation failure: order:",
        r"page allocation stalls for.*order:.*mode:",
        r"INFO: rcu_sched self-detected stall on CPU",
        r"INFO: rcu_sched detected stalls on CPUs/tasks:",
        r"NMI watchdog: Watchdog detected hard LOCKUP",
        r"Kernel panic - not syncing: ",
        r"Oops: Unrecoverable TM Unavailable Exception",
        r'\[\s+INFO:.*dependency detected.*\]',
        r'ERR: suspicious RCU usage'
    ]

    # List of regular expression strings matching
    # lines appearing in a call trace output
    ctvalid = [
        r"\[[\d\ \.]+\].*\[[0-9a-f<>]+\]",
        r"\[[\d\ \.]+\]\s+.+\s+[A-Z]\s[0-9a-fx ]+",
        r"\[[\d\ \.]+\]\s+[0-9a-fx ]+",
        r"Instruction dump",
        r"handlers:",
        r"Code: [0-9a-z]+",
        r"blocked for",
        r"Workqueue:",
        r"disables this message",
        r"Call (T|t)race",
        r"Hardware name",
        r'Exception stack',
        r"task: [0-9a-f]+.*task\.",
        r"^(Traceback)?[0-9a-f\s]+$",
        r"(\[[\d\ \.]+\]\s+)?([A-Z0-9]+: [0-9a-fx ]+)+",
        r"Stack:\s*$",
        r"Modules linked in:",
        r'Oops:',
        r'(PGD|EIP)',
        r'pde.*pte',
        r'stack backtrace:',
        r'->.*(lock|mutex)',
        r'shortest dependencies between .*lock',
        r'changed the state of lock',
        r'other info that might help us debug this',
        r'(acquire|holding) lock:',
        r'already depends on the new lock',
        r'existing dependency chain.*:',
        r'RCU used illegally',
        r'rcu_scheduler_active'
    ]

    # List of regular expression strings matching
    # lines ending a call trace output
    expend = [
        r"\[ end (trace|Kernel panic)",
        r'\[[\d\ \.]+\]\s+\S{1,4}\s*$',
        r'restraintd',
        r'[0-9a-f]+:[0-9a-f]+:',
        r'beah',
        r'\[-- MARK --',
        r'LTP'
    ]

    # Patterns to exclude from the log
    exclude = [
        r'\sOK\s',
        r'^\s*$'
    ]

    def __init__(self, kver, url_or_path):
        """
        Initialize a console log parser

        Args:
            kver:        Kernel version string to use to find the beginning of
                         the kernel log.
            url_or_path: URL or path to the console log file to fetch and
                         parse. Local files may be gzipped.
        """
        self.url_or_path = url_or_path
        self.kver = kver
        self.data = self.__fetchdata()
        self.start_pattern = re.compile('|'.join(self.oopsmsg))
        self.continue_pattern = re.compile('|'.join(self.ctvalid))
        self.end_pattern = re.compile('|'.join(self.expend))
        self.invalid_pattern = re.compile('|'.join(self.exclude), re.MULTILINE)

    def __fetchdata(self):
        """
        Fetch the console log and extract the specified kernel's log from it.

        Returns:
            List of console log lines related to tested kernel
        """
        if not self.url_or_path:
            return []

        try:
            console_text = requests.get(self.url_or_path).text
        except requests.exceptions.MissingSchema:  # We got a file path
            if self.url_or_path.endswith('.gz'):
                with gzip.open(self.url_or_path, 'rb') as gz_file:
                    console_text = gz_file.read()
            else:
                with open(self.url_or_path, 'r') as text_file:
                    console_text = text_file.read()

        try:
            str_data = console_text[
                console_text.index("Linux version %s" % self.kver):
            ]
        except ValueError:
            # Targeted kernel didn't even start booting
            str_data = ''

        data = [line.encode('utf-8') for line in str_data.split('\n') if line]

        return data

    def getfulllog(self):
        """
        Get the gzip-compressed text of the kernel console log.
        """
        return gzipdata("\n".join(self.data))

    def gettraces(self):
        """
        Get a list of non-overlapping oops and call stack outputs extracted
        from the kernel console log.

        Returns:
            A list of oops and call stack output strings.
        """
        result = []
        tmpdata = []

        for line in self.data:
            if self.invalid_pattern.search(line):
                continue
            if self.start_pattern.search(line):
                tmpdata = [line]
            elif tmpdata:
                if self.end_pattern.search(line):
                    tmpdata.append(line)
                    result.append('\n'.join(tmpdata))
                    tmpdata = []
                    continue
                if self.continue_pattern.search(line):
                    # Only include lines that look relevant, in case the log
                    # got flooded with a bunch of unrelated lines in the
                    # meanwhile. Yes, this can drop some lines that are useful
                    # too, but it's the best approach we currently have to
                    # handle messy logs.
                    tmpdata.append(line)

        return result

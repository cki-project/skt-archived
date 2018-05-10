# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted
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
from __future__ import print_function
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import gzip
import logging
import os
import re
import smtplib
import StringIO

import requests

import skt
import skt.runner


def gzipdata(data):
    """
    Compress a string with gzip.

    Args:
        data:   The string to compress.

    Returns:
        String containing gzip-compressed data.
    """
    tstr = StringIO.StringIO()
    with gzip.GzipFile(fileobj=tstr, mode="w") as f:
        f.write(data)
    return tstr.getvalue()


class consolelog(object):
    """Console log parser"""

    # List of regular expression strings matching
    # lines beginning an oops or a call trace output
    oopsmsg = [r"general protection fault:",
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
               r"Oops: Unrecoverable TM Unavailable Exception"
               ]

    # List of regular expression strings matching
    # lines appearing in a call trace output
    ctvalid = [
        r"\[[\d\ \.]+\].*\[[0-9a-f<>]+\]",
        r"\[[\d\ \.]+\]\s+.+\s+[A-Z]\s[0-9a-fx ]+",
        r"\[[\d\ \.]+\]\s+[0-9a-fx ]+",
        r"\[-- MARK --",
        r"Instruction dump",
        r"handlers:",
        r"Code: [0-9a-z]+",
        r"blocked for",
        r"Workqueue:",
        r"disables this message",
        r"Call Trace",
        r"Hardware name",
        r"task: [0-9a-f]+ ti: [0-9a-f]+ task\.ti: [0-9a-f]+",
        r"^(Traceback)?[0-9a-f\s]+$",
        r"(\[[\d\ \.]+\]\s+)?([A-Z0-9]+: [0-9a-fx ]+)+",
        r"Stack:\s*$",
        r"Modules linked in:"
    ]

    # List of regular expression strings matching
    # lines ending a call trace output
    expend = [
        r"\[ end (trace|Kernel panic)"
    ]

    def __init__(self, kver, url):
        """
        Initialize a console log parser

        Args:
            kver:   Kernel version string to use to find the beginning of the
                    kernel log.
            url:    URL of the console log file to fetch and parse.
        """
        self.url = url
        self.kver = kver
        self.data = self.fetchdata()
        self.oopspattern = re.compile("(%s)" % "|".join(self.oopsmsg))
        self.ctvpattern = re.compile("(%s)" % "|".join(self.ctvalid))
        self.eendpattern = re.compile("(%s)" % "|".join(self.expend))

    def fetchdata(self):
        """
        Fetch the console log and extract the specified kernel's log from it.

        Returns:
            List of console log lines related to tested kernel
        """
        if not self.url:
            return []

        r = requests.get(self.url)

        try:
            str_data = r.text[r.text.index("Linux version %s" % self.kver):]
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
        Get a list of oops and call stack outputs extracted from the kernel
        console log.

        Returns:
            A list of oops and call stack output strings.
        """
        result = []

        insplat = False
        inct = False
        tmpdata = []
        # FIXME Check if line == True otherwise it adds an empty line at the
        # end of the extracted trace.
        for line in self.data:
            if self.oopspattern.search(line):
                insplat = True
            elif re.search("Call Trace:", line):
                inct = True

            if insplat and ((inct and not self.ctvpattern.search(line)) or
                            self.eendpattern.search(line)):
                tmpdata.append(line)
                result.append("\n".join(tmpdata))
                tmpdata = []
                insplat = False
                inct = False

            if insplat:
                tmpdata.append(line)

        if tmpdata:
            result.append("\n".join(tmpdata))

        return result


class reporter(object):
    """Abstract test result reporter"""
    # TODO This probably shouldn't be here as we never use it, and it should
    # not be inherited
    TYPE = 'default'

    def __init__(self, cfg):
        """
        Initialize an abstract result reporter.

        Args:
            cfg:    The skt configuration and state.
        """
        # skt configuration and state
        # FIXME Switch to using an explicitly-defined type
        self.cfg = cfg
        # List of attachment tuples, each containing attachment file name and
        # contents.
        self.attach = list()
        # TODO Describe
        self.mergedata = None

    def infourldata(self, mergedata):
        r = requests.get(self.cfg.get("infourl"))
        for line in r.text.split('\n'):
            if line:
                idata = line.split(',')
                if idata[0] == 'base':
                    mergedata['base'] = (idata[1], idata[2])
                elif idata[0] == 'git':
                    mergedata['merge_git'].append((idata[1], idata[2]))
                elif idata[0] == 'patch':
                    mergedata['localpatch'].append(os.path.basename(idata[1]))
                elif idata[0] == 'patchwork':
                    mergedata['patchwork'].append((idata[1], idata[2]))
                else:
                    logging.warning("Unknown infotype: %s", idata[0])

        return mergedata

    def stateconfigdata(self, mergedata):
        mergedata['base'] = (self.cfg.get("baserepo"),
                             self.cfg.get("basehead"))
        if self.cfg.get("mergerepos"):
            mrl = self.cfg.get("mergerepos")
            mhl = self.cfg.get("mergeheads")
            for idx, mrl_item in enumerate(mrl):
                mergedata['merge_git'].append((mrl_item, mhl[idx]))

        if self.cfg.get("localpatches"):
            mergedata['localpatch'] = self.cfg.get("localpatches")

        if self.cfg.get("patchworks"):
            for purl in self.cfg.get("patchworks"):
                patch_mbox = skt.get_patch_mbox(purl)
                patchname = skt.get_patch_subject(patch_mbox)
                mergedata['patchwork'].append((purl, patchname))

        return mergedata

    def update_mergedata(self):
        mergedata = {
            'base': None,
            'merge_git': [],
            'localpatch': [],
            'patchwork': [],
            'config': None
        }

        if self.cfg.get("infourl"):
            mergedata = self.infourldata(mergedata)
        else:
            mergedata = self.stateconfigdata(mergedata)

        if self.cfg.get("cfgurl"):
            r = requests.get(self.cfg.get("cfgurl"))
            if r:
                mergedata['config'] = r.text
        else:
            with open("%s/.config" % self.cfg.get("workdir"), "r") as fp:
                mergedata['config'] = fp.read()

        self.mergedata = mergedata

    def getmergeinfo(self):
        """
        Retrieve information about applied patches and base repository as a
        list of strings which should be then appended to the report. Add the
        configuration which was used to build the kernel to reporter's list
        of attachments.

        Returns: A list of strings representing data about applied patches and
                 base repository.
        """
        result = ['We applied the following patch']
        if len(self.mergedata['localpatch'] + self.mergedata['patchwork']) > 1:
            result[0] += 'es:\n'
        else:
            result[0] += ':\n'

        for patchpath in self.mergedata['localpatch']:
            result += ['  - %s' % patchpath]

        for (purl, pname) in self.mergedata['patchwork']:
            result += ['  - %s,' % pname,
                       '    grabbed from %s\n' % purl]

        result += ['on top of commit %s from the repository at' %
                   self.mergedata['base'][1][:12],
                   '  %s' % self.mergedata['base'][0]]

        if not self.cfg.get("mergelog"):
            cfgname = "config.gz"
            result.append('\nThe kernel was built with the attached '
                          'configuration (%s).' % cfgname)
            self.attach.append((cfgname, gzipdata(self.mergedata["config"])))

        return result

    def getjobids(self):
        jobids = []
        if self.cfg.get("jobs"):
            for jobid in sorted(self.cfg.get("jobs")):
                jobids.append(jobid)
        return jobids

    def getmergefailure(self):
        result = ['\nHowever, the application of the last patch above '
                  'failed with the',
                  'following output:\n']

        with open(self.cfg.get("mergelog"), 'r') as fp:
            for line in fp:
                # Skip the useless part of the 'git am' output
                if "The copy of the patch" in line:
                    break
                result.append('    ' + line.strip())

        return result

    def getbuildfailure(self):
        attname = "build.log.gz"
        result = ['However, the build failed. We are attaching the build '
                  'output for',
                  'more information (%s).' % attname]

        with open(self.cfg.get("buildlog"), 'r') as fp:
            self.attach.append((attname, gzipdata(fp.read())))

        return result

    def getjobresults(self):
        """
        Retrieve job results which should be appended to the report.

        Get job results from runner, check console logs (if present) to filter
        out infrastructure issues and find call traces. For each test run, add
            1. Number of test run
            2. It's result
            3. If present, info about the machine the test ran on
        If the testing failed, add first found trace call and attach related
        console log.

        Returns:
            A list of lines representing results of test runs.
        """
        result = ['\n\nWe ran the following tests:']

        # TODO: Get info from sktrc when we have it there
        for test in ['Boot test']:
            result.append("  - %s" % test)

        result += ['\nwhich produced the results below:']

        runner = skt.runner.getrunner(*self.cfg.get("runner"))
        job_list = sorted(list(self.cfg.get("jobs", [])))
        vresults = runner.getverboseresults(job_list)

        minfo = {"short": {}, "long": {}}
        jidx = 1
        for jobid in job_list:
            for (recipe, rdata) in vresults[jobid].iteritems():
                if recipe == "result":
                    continue

                (res, system, clogurl, slshwurl, llshwurl) = rdata

                clog = consolelog(self.cfg.get("krelease"), clogurl)
                if not clog.data and res != 'Pass':
                    # The targeted kernel either didn't start booting or the
                    # console wasn't logged. The second one isn't an issue if
                    # everything went well, however reporting a failure without
                    # any details is useless so skip it.
                    continue

                result.append("Test run #%d" % jidx)
                result.append("Result: %s" % res)

                if res != "Pass":
                    logging.info("Failure detected in recipe %s, attaching "
                                 "console log", recipe)
                    ctraces = clog.gettraces()
                    if ctraces:
                        result.append("This is the first call trace we found:")
                        result.append(ctraces[0])

                    clfname = "%02d_console.log.gz" % jidx
                    result.append("For more information about the failure, see"
                                  " attached console log: %s" % clfname)
                    self.attach.append((clfname, clog.getfulllog()))

                if slshwurl is not None:
                    if system not in minfo["short"]:
                        r = requests.get(slshwurl)
                        if r:
                            result.append("\nMachine info:")
                            result += r.text.split('\n')
                            minfo["short"][system] = jidx
                    else:
                        result.append("Machine info: same as #%d" %
                                      minfo["short"].get(system))

                result.append('')
                jidx += 1

        return result

    def getreport(self):
        msg = ['Hello,\n',
               'We appreciate your contributions to the Linux kernel and '
               'would like to help',
               'test them. Below are the results of automatic tests we ran on '
               'a patchset',
               'you\'re involved with, with hope it will help you find '
               'possible issues sooner.',
               '\n']

        msg += self.getmergeinfo()

        if self.cfg.get("mergelog"):
            msg += self.getmergefailure()
        elif self.cfg.get("buildlog"):
            msg += self.getbuildfailure()
        else:
            msg += self.getjobresults()

        msg += ['\nPlease reply to this email if you find an issue with our '
                'testing process,',
                'or wish to not receive these reports anymore.',
                '\nSincerely,',
                '  Kernel CI Team']

        if self.attach and self.attach[0][0] == "config":
            self.attach.append(self.attach.pop(0))

        return '\n'.join(msg)

    def getsubject(self):
        if not self.cfg.get('mergelog') and \
           not self.cfg.get('buildlog') and \
           self.cfg.get('retcode') == '0':
            subject = 'PASS: '
        else:
            subject = 'FAIL: '

        if self.cfg.get("mergelog"):
            subject += "Patch application failed"
        elif self.cfg.get("buildlog"):
            subject += "Build failed"
        else:
            subject += "Report"

        if self.cfg.get("krelease"):
            subject += " for kernel %s" % self.cfg.get("krelease")

        return subject

    # TODO Define abstract "report" method.


class stdioreporter(reporter):
    """A reporter sending results to stdout"""
    TYPE = 'stdio'

    def report(self):
        self.update_mergedata()
        print("Subject:", self.getsubject())
        print(self.getreport())

        for (name, att) in self.attach:
            if name.endswith(('.log', '.txt', 'config')):
                print("\n---------------\n", name, sep='')
                print(att)


class mailreporter(reporter):
    """A reporter sending results by e-mail"""
    TYPE = 'mail'

    def __init__(self, cfg, mailfrom, mailto, mailinreplyto=None):
        """
        Initialize an e-mail reporter

        Args:
            cfg:            The skt configuration and state.
            mailfrom:       A string containing the From: address for e-mails.
            mailto:         A string containing comma-separated e-mail
                            addresses to send the result messages to.
            mailinreplyto:  A string containing the value of the "In-Reply-To"
                            header to add to the message. No header is added
                            if evaluates to False.
        """
        # The From: address string
        self.mailfrom = mailfrom
        # A list of addresses to send reports to
        self.mailto = [to.strip() for to in mailto.split(",")]
        # The value of "In-Reply-To" header
        self.mailinreplyto = mailinreplyto
        super(mailreporter, self).__init__(cfg)

    def report(self):
        self.update_mergedata()
        msg = MIMEMultipart()
        msg['Subject'] = self.getsubject()
        msg['To'] = ', '.join(self.mailto)
        msg['From'] = self.mailfrom
        if self.mailinreplyto:
            msg['In-Reply-To'] = self.mailinreplyto
        msg['X-SKT-JIDS'] = ' '.join(self.getjobids())
        msg.attach(MIMEText(self.getreport()))

        for (name, att) in self.attach:
            # TODO Store content type and charset when adding attachments
            if name.endswith(('.log', '.txt', 'config')):
                tmp = MIMEText(att, _charset='utf-8')
                tmp.add_header("content-disposition", "attachment",
                               filename=name)
            else:
                tmp = MIMEApplication(att)
                tmp.add_header("content-disposition", "attachment",
                               filename=name)

            msg.attach(tmp)

        s = smtplib.SMTP('localhost')
        s.sendmail(self.mailfrom, self.mailto, msg.as_string())
        s.quit()


def getreporter(rtype, rarg):
    """
    Create an instance of a "reporter" subclass with specified arguments.

    Args:
        rtype:  The value of the class "TYPE" member to match.
        rarg:   A dictionary with the instance creation arguments.

    Returns:
        The created class instance.

    Raises:
        ValueError if the rtype match wasn't found.
    """
    for cls in reporter.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown reporter type: %s" % rtype)

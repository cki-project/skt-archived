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
import ConfigParser
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import gzip
import logging
import os
import re
import smtplib
import sys
import StringIO

from jinja2 import Environment, FileSystemLoader
import requests

import skt
import skt.runner

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))

MULTI_PASS = 0
MULTI_MERGE = 1
MULTI_BUILD = 2
MULTI_TEST = 3


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


class ConsoleLog(object):
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

        response = requests.get(self.url)

        try:
            str_data = response.text[
                response.text.index("Linux version %s" % self.kver):
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


class Reporter(object):
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
        # Use explicit flag to determine if a single report for multiple test
        # runs should be generated
        self.multireport = True if cfg.get('result') else False
        # Save list of state files because self.cfg will be overwritten. This
        # can be changed to access a specific parameter after the FIXME with
        # passing only explicit parameters is implemented. Only test run and
        # runner info is used during reporting so we are good to go.
        self.statefiles = cfg.get('result', [])
        # Notion of failure for subject creation with multireporting. The
        # earliest problem in the pipeline is reported.
        self.multireport_failed = MULTI_PASS
        # We need to save the job IDs when iterating over state files when
        # multireporting
        self.multi_job_ids = []

    def infourldata(self, mergedata):
        response = requests.get(self.cfg.get("infourl"))
        for line in response.text.split('\n'):
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
                patchname = skt.get_patch_name(patch_mbox)
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

        if not self.cfg.get('mergelog'):
            if self.cfg.get("cfgurl"):
                response = requests.get(self.cfg.get("cfgurl"))
                if response:
                    mergedata['config'] = response.text
            else:
                with open("%s/.config" % self.cfg.get("workdir"),
                          "r") as fileh:
                    mergedata['config'] = fileh.read()

        self.mergedata = mergedata

    def get_kernel_config(self, suffix=None):
        cfgname = "config.gz" if not suffix else "config_{}.gz".format(suffix)
        self.attach.append((cfgname, gzipdata(self.mergedata["config"])))

    def getjobids(self):
        jobids = []
        if self.cfg.get("jobs"):
            for jobid in sorted(self.cfg.get("jobs")):
                jobids.append(jobid)
        return jobids

    def getbuildfailure(self, suffix=None):
        attname = "build.log.gz" if not suffix else "build_%s.log.gz" % suffix
        with open(self.cfg.get("buildlog"), 'r') as fileh:
            self.attach.append((attname, gzipdata(fileh.read())))

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
        for test in ['Boot test', 'LTP lite']:
            result.append("  - %s" % test)

        result = []
        runner = skt.runner.getrunner(*self.cfg.get("runner"))
        job_list = sorted(list(self.cfg.get("jobs", [])))
        vresults = runner.getverboseresults(job_list)

        minfo = {"short": {}, "long": {}}
        jidx = 1
        for jobid in job_list:
            for (recipe, rdata) in vresults[jobid].iteritems():
                temp_result = {}
                if recipe == "result":
                    continue

                (res, system, clogurl, slshwurl, _) = rdata

                clog = ConsoleLog(self.cfg.get("krelease"), clogurl)
                if not clog.data and res != 'Pass':
                    # The targeted kernel either didn't start booting or the
                    # console wasn't logged. The second one isn't an issue if
                    # everything went well, however reporting a failure without
                    # any details is useless so skip it.
                    continue

                temp_result['test_run'] = jidx
                temp_result['result'] = res

                if res != "Pass":
                    logging.info("Failure detected in recipe %s, attaching "
                                 "console log", recipe)
                    ctraces = clog.gettraces()

                    temp_result['ctraces'] = None
                    if ctraces:
                        temp_result['ctraces'] = ctraces[0]

                    console_log_file_name = "%02d_console.log.gz" % jidx
                    temp_result['console_log'] = console_log_file_name
                    self.attach.append(
                        (console_log_file_name, clog.getfulllog())
                    )

                if slshwurl is not None:
                    response = requests.get(slshwurl)
                    if response:
                        temp_result['machine_info'] = response.text
                        minfo["short"][system] = jidx

                result.append(temp_result)
                jidx += 1

        return result

    def getreport(self):
        j2_env = Environment(
            loader=FileSystemLoader(SCRIPT_PATH),
            trim_blocks=True,
            lstrip_blocks=True
        )
        template = j2_env.get_template('templates/email_report.j2')

        # For a single report, we load up our existing config.
        skt_states = [self.cfg]

        # For a multireport, we load up the configs provided by the user.
        if self.multireport:
            skt_states = [self.load_state_cfg(x) for x in self.statefiles]

        reports = []
        # Loop over each state and prepare the data for the report
        for skt_state in skt_states:
            # Load the state file and update our mergedata
            self.cfg = skt_state
            self.update_mergedata()

            # Create a temporary report section that we can append at the end
            temp_report = self.cfg
            temp_report['mergedata'] = self.mergedata

            # If the merge failed, get the merge log
            temp_report['mergelog_output'] = None
            if self.cfg.get('mergelog'):
                with open(self.cfg.get('mergelog'), 'r') as fileh:
                    temp_report['mergelog_output'] = fileh.read()

            # Attach the kernel config to the report if the merge passed
            if not self.cfg.get('mergelog'):
                self.get_kernel_config()

            # If the compile failed, get the build log
            temp_report['buildlog_output'] = None
            if self.cfg.get('buildlog'):
                self.getbuildfailure()
                with open(self.cfg.get('buildlog'), 'r') as fileh:
                    temp_report['buildlog_output'] = fileh.read()

            # Get the test results
            temp_report['jobresults'] = self.getjobresults()

            # Add the section to the report
            reports.append(temp_report)

        # If we have a multreport, we need the overall summary
        multireport_summary = None
        if self.multireport:
            multireport_summary = self.get_multireport_summary()

        # Render the jinja2 template
        msg = template.render(
            reports=reports,
            summary=multireport_summary
        )

        # Move configuration attachments to the end because some mail clients
        # (eg. mutt) inline them and they are huge
        # It's not safe to iterate over changing list so let's use a helper
        config_attachments = [attachment for attachment in self.attach
                              if 'config' in attachment[0]]
        self.attach = [
            attachment for attachment in self.attach
            if attachment not in config_attachments
        ] + config_attachments

        return msg

    def load_state_cfg(self, statefile):
        """
        Load state info from statefile and reassign to self.cfg.

        Raises: Exception if required 'runner' section is missing
        """
        cfg = {}
        state_to_report = ConfigParser.ConfigParser()
        state_to_report.read(statefile)

        # FIXME This can be simplified or removed after configuration and
        # state split
        for (name, value) in state_to_report.items('state'):
            if not cfg.get(name):
                if name.startswith('jobid_'):
                    if 'jobs' not in cfg:
                        cfg['jobs'] = set()
                    cfg['jobs'].add(value)
                elif name.startswith('mergerepo_'):
                    if 'mergerepos' not in cfg:
                        cfg['mergerepos'] = list()
                    cfg['mergerepos'].append(value)
                elif name.startswith('mergehead_'):
                    if 'mergeheads' not in cfg:
                        cfg['mergeheads'] = list()
                    cfg['mergeheads'].append(value)
                elif name.startswith('localpatch_'):
                    if 'localpatches' not in cfg:
                        cfg['localpatches'] = list()
                    cfg['localpatches'].append(value)
                elif name.startswith('patchwork_'):
                    if 'patchworks' not in cfg:
                        cfg['patchworks'] = list()
                    cfg['patchworks'].append(value)
                cfg[name] = value

        # Get runner info
        if state_to_report.has_section('runner'):
            runner_config = {}
            for (key, val) in state_to_report.items('runner'):
                if key != 'type':
                    runner_config[key] = val
                cfg['runner'] = [state_to_report.get('runner', 'type'),
                                 runner_config]
        else:
            logging.debug('No runner info found in state file, test runs will'
                          ' not be reported')

        return cfg

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

    def get_multisubject(self):
        if self.multireport_failed == MULTI_PASS:
            subject = 'PASS: '
        else:
            subject = 'FAIL: '

        if self.multireport_failed == MULTI_MERGE:
            subject += "Patch application failed"
        elif self.multireport_failed == MULTI_BUILD:
            subject += "Build failed"
        else:
            subject += "Report"

        # Kernel release should be same for all kernels built
        if self.cfg.get("krelease"):
            subject += " for kernel %s" % self.cfg.get("krelease")

        return subject

    def get_multireport_summary(self):
        """Get a summary (pass / fail) of the multireport.

        Returns: A list of lines (strings) representing the summary.
        """

<<<<<<< HEAD
        if self.multireport_failed == MULTI_PASS:
            summary += ['  All builds and tests PASSED.']
=======
        if self.multireport_failed == MULTI_PASS and not self.multiretcode:
            summary = 'All builds and tests PASSED.'
>>>>>>> Use jinja2 for sending reports
        elif self.multireport_failed == MULTI_MERGE:
            summary = 'Patch application FAILED!'
        elif self.multireport_failed == MULTI_BUILD:
<<<<<<< HEAD
            summary += ['  One or more builds FAILED!']
        elif self.multireport_failed == MULTI_TEST:
            summary += ['  Testing FAILED!']

        summary += ['\nMore detailed data follows.', '------------']
=======
            summary = 'One or more builds FAILED!'
        elif self.multireport_failed == MULTI_TEST or self.multiretcode:
            summary = 'Testing FAILED!'

>>>>>>> Use jinja2 for sending reports
        return summary

    # TODO Define abstract "report" method.


class StdioReporter(Reporter):
    """A reporter sending results to stdout"""
    TYPE = 'stdio'

    def report(self, printer=sys.stdout):
        if self.multireport:
            # We need to run the reporting function first to get the aggregated
            # data to build subject from
            report = self.getreport()
            printer.write("Subject: {}\n".format(self.get_multisubject()))
            printer.write(report)
        else:
            self.update_mergedata()
            printer.write("Subject: {}\n".format(self.getsubject()))
            printer.write(self.getreport())

        for (name, att) in self.attach:
            if name.endswith(('.log', '.txt')):
                printer.write("\n---------------\n", name, sep='')
                printer.write(att)


class MailReporter(Reporter):
    """A reporter sending results by e-mail"""
    TYPE = 'mail'

    def __init__(self, cfg):
        """Initialize an e-mail reporter."""
        # Get all of the required fields to send an email
        self.mailfrom = cfg['reporter']['mail_from']
        self.mailto = [to.strip() for to in cfg['reporter']['mail_to']]
        self.headers = [headers.strip() for headers in
                        cfg['reporter']['mail_header']]
        self.subject = cfg['reporter']['mail_subject']
        self.smtp_url = cfg.get('smtp_url') or 'localhost'

        super(MailReporter, self).__init__(cfg)

    def report(self):
        msg = MIMEMultipart()

        # Add the most basic parts of the email message
        if self.subject:
            msg['Subject'] = self.subject
        msg['To'] = ', '.join(self.mailto)
        msg['From'] = self.mailfrom

        # Add any extra headers
        for header_line in self.headers:
            header, value = header_line.split(":", 1)
            msg[header] = value

        if self.multireport:
            # We need to run the reporting function first to get aggregates to
            # build subject from
            msg.attach(MIMEText(self.getreport()))
            if not msg['Subject']:
                msg['Subject'] = self.get_multisubject()
            # Add the SKT job IDs so we can correlate emails to jobs
            msg['X-SKT-JIDS'] = ' '.join(self.multi_job_ids)
        else:
            self.update_mergedata()
            if not msg['Subject']:
                msg['Subject'] = self.getsubject()
            msg.attach(MIMEText(self.getreport()))
            # Add the SKT job IDs so we can correlate emails to jobs
            msg['X-SKT-JIDS'] = ' '.join(self.getjobids())

        for (name, att) in self.attach:
            # TODO Store content type and charset when adding attachments
            if name.endswith(('.log', '.txt')):
                tmp = MIMEText(att, _charset='utf-8')
                tmp.add_header("content-disposition", "attachment",
                               filename=name)
            else:
                tmp = MIMEApplication(att)
                tmp.add_header("content-disposition", "attachment",
                               filename=name)

            msg.attach(tmp)

        s = smtplib.SMTP(self.smtp_url)
        s.sendmail(self.mailfrom, self.mailto, msg.as_string())
        s.quit()

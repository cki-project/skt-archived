# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted material
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

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import re
import requests
import smtplib
import tempfile
import xml.etree.ElementTree as etree
import skt.runner

class consolelog(object):
    oopsmsg = [
	"general protection fault:",
	"BUG:",
	"kernel BUG at",
	"do_IRQ: stack overflow:",
	"RTNL: assertion failed",
	"Eeek! page_mapcount\(page\) went negative!",
	"near stack overflow \(cur:",
	"double fault:",
	"Badness at",
	"NETDEV WATCHDOG",
	"WARNING: at",
	"appears to be on the same physical disk",
	"Unable to handle kernel",
	"sysctl table check failed",
	"------------\[ cut here \]------------",
	"list_del corruption\.",
	"list_add corruption\.",
	"NMI watchdog: BUG: soft lockup",
	"irq [0-9]+: nobody cared",
	"INFO: task .* blocked for more than [0-9]+ seconds",
	"vmwrite error: reg ",
	"page allocation failure: order:",
	"page allocation stalls for.*order:.*mode:",
	"INFO: rcu_sched self-detected stall on CPU",
	"INFO: rcu_sched detected stalls on CPUs/tasks:",
	"NMI watchdog: Watchdog detected hard LOCKUP",
	"Kernel panic - not syncing: ",
	"Oops: Unrecoverable TM Unavailable Exception",
    ]

    ctvalid = [
        "\[[\d\ \.]+\].*\[[0-9a-f<>]+\]",
        "\[[\d\ \.]+\]\s+.+\s+[A-Z]\s[0-9a-fx ]+",
        "\[[\d\ \.]+\]\s+[0-9a-fx ]+",
        "\[-- MARK --",
        "Instruction dump",
        "handlers:",
        "Code: [0-9a-z]+",
        "blocked for",
        "Workqueue:",
        "disables this message",
        "Call Trace",
        "Hardware name",
        "task: [0-9a-f]+ ti: [0-9a-f]+ task\.ti: [0-9a-f]+",
        "^(Traceback)?[0-9a-f\s]+$",
        "(\[[\d\ \.]+\]\s+)?([A-Z0-9]+: [0-9a-fx ]+)+",
        "Stack:\s*$",
        "Modules linked in:"
    ]

    expend = [
        "\[ end (trace|Kernel panic)"
    ]

    def __init__(self, url):
        self.url = url
        self.data = None
        self.oopspattern = re.compile("(%s)" % "|".join(self.oopsmsg))
        self.ctvpattern  = re.compile("(%s)" % "|".join(self.ctvalid))
        self.eendpattern = re.compile("(%s)" % "|".join(self.expend))

    def fetchdata(self):
        r = requests.get(self.url)
        self.data = r.text

    def gettraces(self):
        result = []
        if self.data == None:
            self.fetchdata()

        insplat = False
        inct = False
        tmpdata = []
        for line in self.data.split('\n'):
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

        if len(tmpdata) > 0:
            result.append("\n".join(tmpdata))

        return result

class reporter(object):
    TYPE = 'default'

    def __init__(self, cfg):
        self.cfg = cfg
        self.attach = list()

    def getmergeinfo(self):
        result = []

        r = requests.get(self.cfg.get("infourl"))
        for line in r.text.split('\n'):
            if line == "":
                continue
            (itype,iurl,idata) = line.split(',')
            if itype == 'base':
                result = [ "base repo: %s" % iurl,
                           "     HEAD: %s" % idata, "" ] + result
            elif itype == 'git':
                result += [ "merged git repo: %s" % iurl,
                            "           HEAD: %s" % idata ]
            else:
                logging.warning("Unknown infotype: %s", itype)

        result.insert(0, "\n-----------------------")
        return result

    def getjobresults(self):
        result = []
        runner = skt.runner.getrunner(*self.cfg.get("runner"))
        vresults = runner.getverboseresults(list(self.cfg.get("jobs")))

        result.append("\n-----------------------")
        for jobid in self.cfg.get("jobs"):
            result.append("jobid: %s" % jobid)

            result.append("result: %s" % vresults[jobid]["result"])

            for (recipe, rdata) in vresults[jobid].iteritems():
                if recipe == "result":
                    continue

                result.append("\n  recipe: %s" % recipe)
                result.append("  system: %s" % rdata[1])
                result.append("  result: %s" % rdata[0])
                if rdata[2] != None:
                    result.append("  console.log: %s" % rdata[2])
                    if rdata[0] == "Panic":
                        logging.info("Panic detected in recipe %s, attaching console log",
                                     recipe)
                        clog = consolelog(rdata[2])
                        self.attach += clog.gettraces()

            result.append("")

        return result

    def getreport(self):
        msg = list()

        msg.append("result report for kernel %s" % self.cfg.get("krelease"))
        msg.append("tarpkg url: %s" % self.cfg.get("buildurl"))

        msg += self.getmergeinfo()

        msg += self.getjobresults()

        return '\n'.join(msg)

class stdioreporter(reporter):
    TYPE = 'stdio'

    def report(self):
        print self.getreport()

class mailreporter(reporter):
    TYPE = 'mail'

    def __init__(self, cfg, mailfrom, mailto):
        self.mailfrom = mailfrom
        self.mailto = [to.strip() for to in mailto.split(",")]
        super(mailreporter, self).__init__(cfg)

    def report(self):
        msg = MIMEMultipart()
        msg['Subject'] = "[skt] result report for kernel %s [%s]" % (
                         self.cfg.get("krelease"),
                         "PASS" if self.cfg.get("retcode") == "0" else "FAIL")
        msg['To'] = ', '.join(self.mailto)
        msg['From'] = self.mailfrom
        msg.attach(MIMEText(self.getreport()))

        for att in self.attach:
            msg.attach(MIMEText(att, _charset='utf-8'))

        s = smtplib.SMTP('localhost')
        s.sendmail(self.mailfrom, self.mailto, msg.as_string())
        s.quit()

def getreporter(rtype, rarg):
    for cls in reporter.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown reporter type: %s" % rtype)

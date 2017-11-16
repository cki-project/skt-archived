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

import bkr.client
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import gzip
import logging
import os
import re
import requests
import smtplib
import StringIO
import tempfile
import xml.etree.ElementTree as etree
import xmlrpclib
import skt
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

    def __init__(self, kver, url):
        self.url = url
        self.kver = kver
        self.data = None
        self.oopspattern = re.compile("(%s)" % "|".join(self.oopsmsg))
        self.ctvpattern  = re.compile("(%s)" % "|".join(self.ctvalid))
        self.eendpattern = re.compile("(%s)" % "|".join(self.expend))

    def fetchdata(self):
        r = requests.get(self.url)
        tkernel = False

        self.data = []
        for line in r.text.split('\n'):
            if tkernel == False and line.find("Linux version %s" % self.kver) != -1:
                tkernel = True

            if tkernel == True:
                self.data.append(line.encode('utf-8'))

    def gzipdata(self, data):
        tstr = StringIO.StringIO()
        with gzip.GzipFile(fileobj = tstr, mode="w") as f:
            f.write(data)
        return tstr.getvalue()

    def getfulllog(self):
	return self.gzipdata("\n".join(self.data))

    def gettraces(self):
        result = []
        if self.data == None:
            self.fetchdata()

        insplat = False
        inct = False
        tmpdata = []
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

        if len(tmpdata) > 0:
            result.append("\n".join(tmpdata))

        return result

class reporter(object):
    TYPE = 'default'

    def __init__(self, cfg):
        self.cfg = cfg
        self.attach = list()

    def infourldata(self, mergedata):
        r = requests.get(self.cfg.get("infourl"))
        for line in r.text.split('\n'):
            if line == "":
                continue
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
        mergedata['base'] = (self.cfg.get("baserepo"), self.cfg.get("basehead"))
	if self.cfg.get("mergerepos"):
            mrl = self.cfg.get("mergerepos")
            mhl = self.cfg.get("mergeheads")
            for idx in range(0, len(mrl)):
                mergedata['merge_git'].append((mrl[idx], mhl[idx]))

	if self.cfg.get("localpatches"):
            mergedata['localpatch'] = self.cfg.get("localpatches")

	if self.cfg.get("patchworks"):
            for purl in self.cfg.get("patchworks"):
                (baseurl, patchid) = skt.parse_patchwork_url(purl)
                rpc = xmlrpclib.ServerProxy("%s/xmlrpc/" % baseurl)
                pinfo = rpc.patch_get(patchid)
                mergedata['patchwork'].append((purl, pinfo.get("name")))

        return mergedata

    def getmergeinfo(self):
        result = []
        mergedata = {
                'base' : None,
                'merge_git' : [],
                'localpatch' : [],
                'patchwork' : [],
                'config' : None,
                }

        if self.cfg.get("infourl"):
            mergedata = self.infourldata(mergedata)
        else:
            mergedata = self.stateconfigdata(mergedata)

        print mergedata

        if self.cfg.get("cfgurl"):
            r = requests.get(self.cfg.get("cfgurl"))
            if r != None:
                mergedata['config'] = r.text
        else:
            with open("%s/.config" % self.cfg.get("workdir"), "r") as fp:
                mergedata['config'] = fp.read()

        result += [ "base repo: %s" % mergedata['base'][0],
                    "     HEAD: %s" % mergedata['base'][1] ]

        for (repo, head) in mergedata['merge_git']:
            result += [ "\nmerged git repo: %s" % repo,
                        "           HEAD: %s" % head ]

        for (patchpath) in mergedata['localpatch']:
            result += [ "\npatch: %s" % patchpath ]

        for (purl, pname) in mergedata['patchwork']:
            result += [ "\npatchwork url: %s" % purl,
                        "         name: %s" % pname ]

        cfgname = "config"
        result.append("\nconfig: see attached '%s'" % cfgname)
        self.attach.append((cfgname, mergedata["config"]))

        result.insert(0, "\n-----------------------")
        return result

    def gettested(self):
        result = []

        result.append("\n-----------------------")
        result.append("Tested:")

        # TODO: Get info from sktrc when we have it there
        for test in ['Boot test']:
            result.append("  - %s" % test)
        return result

    def getjobids(self):
	jobids = []
        if self.cfg.get("jobs"):
            for jobid in sorted(self.cfg.get("jobs")):
                jobids.append(jobid)
        return jobids

    def getmergefailure(self):
        result = []

        result.append("\n-----------------------")
        result.append("Merge failed:")
        with open(self.cfg.get("mergelog"), 'r') as fp:
            result.append(fp.read())
        return result

    def getbuildfailure(self):
        result = []

        attname = "build.log"
        result.append("\n-----------------------")
        result.append("Build failed: see attached %s" % attname)

        with open(self.cfg.get("buildlog"), 'r') as fp:
            self.attach.append((attname, fp.read()))

        return result

    def getjobresults(self):
        result = []
        runner = skt.runner.getrunner(*self.cfg.get("runner"))
        vresults = runner.getverboseresults(list(self.cfg.get("jobs")))

        result.append("\n-----------------------")
        minfo = { "short": {}, "long": {} }
        jidx = 1
        for jobid in sorted(self.cfg.get("jobs")):
            for (recipe, rdata) in vresults[jobid].iteritems():
                if recipe == "result":
                    continue

                (res, system, clogurl, slshwurl, llshwurl) = rdata
                ssys = system.split(".")[0]

                result.append("index: #%d" % jidx)
                result.append("result: %s" % res)

                if slshwurl != None:
                    if system not in minfo["short"]:
                        r = requests.get(slshwurl)
                        if r != None:
                            result.append("\nmachine info:")
                            result += r.text.split('\n')
                            minfo["short"][system] = jidx
                    else:
                        result.append("machine info: same as #%d" %
                                      minfo["short"].get(system))

                if clogurl != None and res != "Pass":
                    logging.info("Panic detected in recipe %s, attaching console log",
                                 recipe)
                    clog = consolelog(self.cfg.get("krelease"), clogurl)
                    ctraces = clog.gettraces()
                    if len(ctraces) > 0:
                        result.append("first encountered call trace:")
                        result.append(ctraces[0])

                    clfname = "%02d_console.log.gz" % jidx
                    result.append("full console log attached: %s" % clfname)
                    self.attach.append((clfname, clog.getfulllog()))

                result.append("---")
                jidx += 1

        return result

    def getreport(self):
        msg = list()

        if self.cfg.get("krelease"):
            msg.append("result report for kernel %s" % self.cfg.get("krelease"))

        msg += self.getmergeinfo()

        if self.cfg.get("mergelog"):
            msg += self.getmergefailure()
        elif self.cfg.get("buildlog"):
            msg += self.getbuildfailure()
        else:
            msg += self.gettested()
            msg += self.getjobresults()

        if self.attach[0][0] == "config":
            self.attach.append(self.attach.pop(0))

        return '\n'.join(msg)

class stdioreporter(reporter):
    TYPE = 'stdio'

    def report(self):
        print self.getreport()

        for (name, att) in self.attach:
            if not (name.endswith('.log') or name.endswith('.txt') or \
                    name.endswith('config')):
                continue
            print "\n---------------\n%s\n" % name
            print att

class mailreporter(reporter):
    TYPE = 'mail'

    def __init__(self, cfg, mailfrom, mailto):
        self.mailfrom = mailfrom
        self.mailto = [to.strip() for to in mailto.split(",")]
        super(mailreporter, self).__init__(cfg)

    def getsubject(self):
	subject = "[skt] [%s] " % ("PASS" if self.cfg.get("retcode") == "0" \
                                          else "FAIL")

	if self.cfg.get("mergelog"):
            subject += "patch application failed"
        elif self.cfg.get("buildlog"):
            subject += "build failed"
        else:
            subject += "result report"
            if self.cfg.get("krelease"):
                subject += " for kernel %s" % self.cfg.get("krelease")

	return subject

    def report(self):
        msg = MIMEMultipart()
        msg['Subject'] = self.getsubject()
        msg['To'] = ', '.join(self.mailto)
        msg['From'] = self.mailfrom
        msg['X-SKT-JIDS'] = ' '.join(self.getjobids())
        msg.attach(MIMEText(self.getreport()))

        for (name, att) in self.attach:
            if (name.endswith('.log') or name.endswith('.txt') or \
                    name.endswith('config')):
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
    for cls in reporter.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown reporter type: %s" % rtype)

import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as etree

class runner(object):
    TYPE = 'default'

class beakerrunner(runner):
    TYPE = 'beaker'

    def __init__(self, jobtemplate, jobowner = None):
        self.template = os.path.expanduser(jobtemplate)
        self.jobowner = jobowner
        self.watchdelay = 60
        self.watchlist = set()

        logging.info("runner type: %s", self.TYPE)
        logging.info("beaker template: %s", self.template)

    def getxml(self, replacements):
        xml = ''
        with open(self.template, 'r') as f:
            for line in f:
                for match in re.finditer("##(\w+)##", line):
                    if match.group(1) in replacements:
                        line = line.replace(match.group(0),
                                replacements[match.group(1)])

                xml += line

        return xml

    def getresults(self, jobid):
        ret = 0

        if jobid != None:
            bkr = subprocess.Popen(["bkr", "job-results", "--no-logs",
                                    "--prettyxml", jobid],
                                   stdout=subprocess.PIPE)
            (stdout, stderr) = bkr.communicate()
            for line in stdout.split("\n"):
                m = re.match('^<job id=.*result="([^"]+)".*>$', line)
                if m:
                    result = m.group(1)
                    if result != "Pass":
                        ret = 1
                    logging.info("result: %s [%d]", result, ret)
                    break

        return ret

    def watchloop(self):
        while len(self.watchlist):
            time.sleep(self.watchdelay)
            for cid in self.watchlist.copy():
                bkr = subprocess.Popen(["bkr", "job-results", "--no-logs",
                                        cid],
                                       stdout=subprocess.PIPE)
                (stdout, stderr) = bkr.communicate()
                root = etree.fromstring(stdout)

                logging.debug("status %s: %s (%s)", cid,
                              root.attrib.get("status"),
                              root.attrib.get("result"))
                if root.attrib.get("status") in ["Completed", "Aborted"]:
                    logging.info("%s status changed to '%s', removing from watchlist",
                                 cid, root.attrib.get("status"))
                    self.watchlist.remove(cid)

    def wait(self, jobid):
        bkr = subprocess.Popen(["bkr", "job-results", "--no-logs", jobid],
                               stdout=subprocess.PIPE)
        (stdout, stderr) = bkr.communicate()
        root = etree.fromstring(stdout)

        if root.attrib.get("status") in ["Completed", "Aborted"]:
            return

        for el in root.findall("recipeSet/recipe"):
            cid = "R:%s" % el.attrib.get("id")
            self.watchlist.add(cid)
            logging.info("added %s to watchlist", cid)

        self.watchloop()

    def run(self, url, release, wait=False):
        ret = 0
        jobid = None
        args = ["bkr", "job-submit"]

        if self.jobowner != None:
            args += ["--job-owner=%s" % self.jobowner]

        args += ["-"]

        uid = url.split('/')[-1]

        bkr = subprocess.Popen(args, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)
        (stdout, stderr) = bkr.communicate(self.getxml({'KVER' : release,
                                                        'KPKG_URL' : url,
                                                        'UID': uid}))

        for line in stdout.split("\n"):
            m = re.match("^Submitted: \['([^']+)'\]$", line)
            if m:
                jobid = m.group(1)
                break

        logging.info("jobid: %s", jobid)

        if wait == True:
            self.wait(jobid)
            ret = self.getresults(jobid)

        return ret

def getrunner(rtype, rarg):
    for cls in runner.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown runner type: %s" % rtype)

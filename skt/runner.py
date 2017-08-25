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
        self.whiteboard = None
        self.failures = {}
        self.recipes = set()

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

    def jobresult(self, jobid):
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
                    logging.info("job result: %s [%d]", result, ret)
                    break

        return ret

    def getresults(self, jobid = None):
        ret = 0
        fhosts = set()

        if jobid != None:
            ret = self.jobresult(jobid)

        if jobid == None or ret != 0:
            for (recipe, data) in self.failures.iteritems():
                logging.info("%s failed %d/%d (%s)%s", recipe, len(data[0]),
                             data[2], ', '.join(data[1]),
                             "" if len(set(data[0])) > 1
                                else ": %s" % data[0][0])

                fhosts = fhosts.union(set(data[0]))
                ret = 1

        if ret != 0:
            msg = ""
            if len(fhosts) > 1:
                msg = "multiple hosts"
            else:
                msg = "a single host: %s" % fhosts.pop()

            logging.warning("FAILED %s/%s on %s", len(self.failures.keys()),
                            len(self.recipes), msg)

        return ret


    def recipe_to_job(self, recipe, samehost = False):
        tmp = recipe.copy()
        if (samehost):
            hreq = tmp.find("hostRequires")
            hostname = etree.Element("hostname")
            hostname.set("op", "=")
            hostname.set("value", tmp.attrib.get("system"))
            hreq.append(hostname)

        newrs = etree.Element("recipeSet")
        newrs.append(tmp)

        newwb = etree.Element("whiteboard")
        newwb.text = "%s [R:%s]" % (self.whiteboard, tmp.attrib.get("id"))

        if (samehost):
            newwb.text += " (%s)" % tmp.attrib.get("system")

        newroot = etree.Element("job")
        newroot.append(newwb)
        newroot.append(newrs)

        return newroot

    def watchloop(self):
        iteration = 0
        while len(self.watchlist):
            if iteration > 0:
                time.sleep(self.watchdelay)

            for (cid, reschedule, origin) in self.watchlist.copy():
                bkr = subprocess.Popen(["bkr", "job-results", "--no-logs",
                                        cid],
                                       stdout=subprocess.PIPE)
                (stdout, stderr) = bkr.communicate()
                root = etree.fromstring(stdout)

                if root.attrib.get("status") in ["Completed", "Aborted",
                                                 "Cancelled"]:
                    logging.info("%s status changed to '%s', removing from watchlist",
                                 cid, root.attrib.get("status"))
                    self.watchlist.remove((cid, reschedule, origin))

                    if root.attrib.get("status") ==  "Cancelled":
                        continue

                    if root.attrib.get("result") != "Pass":
                        if origin == None:
                            origin = cid

                        if not self.failures.has_key(origin):
                            self.failures[origin] = [[], set(), 1]

                        self.failures[origin][0].append(root.attrib.get("system"))
                        self.failures[origin][1].add(root.attrib.get("result"))

                        if reschedule:
                            logging.info("%s -> '%s', resubmitting",
                                         cid, root.attrib.get("result"))

                            newjob = self.recipe_to_job(root, False)
                            newjobid = self.jobsubmit(etree.tostring(newjob))
                            self.add_to_watchlist(newjobid, False, origin)

                            newjob = self.recipe_to_job(root, True)
                            newjobid = self.jobsubmit(etree.tostring(newjob))
                            self.add_to_watchlist(newjobid, False, origin)

            iteration += 1

    def add_to_watchlist(self, jobid, reschedule=True, origin=None):
        bkr = subprocess.Popen(["bkr", "job-results", "--no-logs", jobid],
                               stdout=subprocess.PIPE)
        (stdout, stderr) = bkr.communicate()
        root = etree.fromstring(stdout)

        if self.whiteboard == None:
            self.whiteboard = root.find("whiteboard").text

        for el in root.findall("recipeSet/recipe"):
            cid = "R:%s" % el.attrib.get("id")
            self.watchlist.add((cid, reschedule, origin))
            self.recipes.add(cid)
            if origin != None:
                self.failures[origin][2] += 1
            logging.info("added %s to watchlist", cid)

    def wait(self, jobid):
        self.add_to_watchlist(jobid, True)
        self.watchloop()

    def jobsubmit(self, xml):
        jobid = None
        args = ["bkr", "job-submit"]

        if self.jobowner != None:
            args += ["--job-owner=%s" % self.jobowner]

        args += ["-"]

        bkr = subprocess.Popen(args, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)

        (stdout, stderr) = bkr.communicate(xml)

        for line in stdout.split("\n"):
            m = re.match("^Submitted: \['([^']+)'\]$", line)
            if m:
                jobid = m.group(1)
                break

        logging.info("submitted jobid: %s", jobid)

        return jobid

    def run(self, url, release, wait=False):
        ret = 0
        uid = url.split('/')[-1]
        jobid = self.jobsubmit(self.getxml({'KVER' : release,
                                            'KPKG_URL' : url,
                                            'UID': uid}))

        if wait == True:
            self.wait(jobid)
            ret = self.getresults(jobid)

        return ret

def getrunner(rtype, rarg):
    for cls in runner.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown runner type: %s" % rtype)

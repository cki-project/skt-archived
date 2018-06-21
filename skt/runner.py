# Copyright (c) 2017 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General
# Public License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

import logging
import os
import platform
import re
import subprocess
import time
import xml.etree.ElementTree as etree


class Runner(object):
    """An abstract test runner"""
    # TODO This probably shouldn't be here as we never use it, and it should
    # not be inherited
    TYPE = 'default'

    # TODO Define abstract "run" method.


class BeakerRunner(Runner):
    """Beaker test runner"""
    TYPE = 'beaker'

    def __init__(self, jobtemplate, jobowner=None):
        """
        Initialize a runner executing tests on Beaker.

        Args:
            jobtemplate:    Path to a Beaker job template. Can contain a tilde
                            expression ('~' or '~user') to be expanded into
                            the current user's home directory.
            jobowner:       Name of a Beaker user on whose behalf the job
                            should be submitted, or None, if the owner should
                            be the current user.
        """
        # Beaker job template file path
        # FIXME Move expansion up the call stack, as this limits the class
        # usefulness, because tilde is a valid path character.
        self.template = os.path.expanduser(jobtemplate)
        # Name of a Beaker user on whose behalf the job should be submitted,
        # or None, if the owner should be the current user.
        self.jobowner = jobowner
        # Delay between checks of Beaker job statuses, seconds
        self.watchdelay = 60
        # A set of Beaker jobs to watch, each a 3-tuple containing:
        # * Taskspec (universal ID) of the job
        # * True if the job should be rescheduled when failing, false if not
        # * Taskspec of the origin job - the job this job is a resubmission of
        self.watchlist = set()
        self.whiteboard = None
        self.failures = {}
        self.recipes = set()
        self.jobs = set()
        self.lastsubmitted = None
        self.j2r = dict()

        logging.info("runner type: %s", self.TYPE)
        logging.info("beaker template: %s", self.template)

    def getxml(self, replacements):
        """
        Generate job XML with template replacements applied. Search the
        template for words surrounded by "##" strings and replace them with
        strings from the supplied dictionary.

        Args:
            replacements:   A dictionary of placeholder strings with "##"
                            around them, and their replacements.

        Returns:
            The job XML text with template replacements applied.
        """
        xml = ''
        with open(self.template, 'r') as fileh:
            for line in fileh:
                for match in re.finditer(r"##(\w+)##", line):
                    if match.group(1) in replacements:
                        line = line.replace(match.group(0),
                                            replacements[match.group(1)])

                xml += line

        return xml

    def getresultstree(self, jobid, logs=False):
        """
        Retrieve Beaker job results in Beaker's native XML format.

        Args:
            jobid: The Beaker job ID.
            logs:  Set to 'True' to retrieve logs from Beaker as part of the
                   job results. The default is 'False', which excludes logs
                   from the Beaker job results.

        Returns:
            The job results XML text with job logs (if logs==True).
        """
        args = ["bkr", "job-results"]
        if not logs:
            args.append("--no-logs")
        args.append(jobid)

        bkr = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, _) = bkr.communicate()
        return etree.fromstring(stdout)

    def dumpjunitresults(self, jobid, junit):
        """
        Retrieve junit XML from beaker and write it to a file

        Args:
            jobid: The beaker jobid
            junit: The directory where the junit XML will be written
        """
        args = ["bkr", "job-results", "--format=junit-xml"]
        args.append(jobid)

        fname = "%s/%s.xml" % (junit, jobid.replace(":", "_").lower())
        with open(fname, 'w') as fileh:
            bkr = subprocess.Popen(args, stdout=fileh)
            bkr.communicate()

    def getconsolelog(self, jobid=None):
        """
        Retrieve console log URL from a Beaker job.

        Args:
            jobid: The Beaker job ID.

        Returns:
            The URL for console logs within the Beaker environment.
        """
        url = None

        if jobid is None:
            jobid = self.lastsubmitted

        root = self.getresultstree(jobid, True)
        el = root.find("recipeSet/recipe/logs/log[@name='console.log']")
        if el is not None:
            url = el.attrib.get("href")

        return url

    def _forget_cid(self, cid):
        """
        Remove a job or recipe from the current list

        Args:
            cid: The job (J:xxxxx) or recipe (R:xxxxx) id
        """
        if cid.startswith("J:"):
            self.jobs.remove(cid)
            for rid in self.j2r[cid]:
                self.recipes.remove(rid)
        elif cid.startswith("R:"):
            self.recipes.remove(cid)
            deljids = set()
            for (jid, rset) in self.j2r.iteritems():
                if cid in rset:
                    rset.remove(cid)
                    if len(rset) == 0:
                        deljids.add(jid)
            for jid in deljids:
                del self.j2r[jid]
                self.jobs.remove(jid)
        else:
            raise ValueError("Unknown cid type: %s" % cid)

    def getverboseresults(self, joblist):
        """
        Retrieve verbose results for a list of beaker jobs

        Args:
            joblist: A list of beaker jobs

        Returns:
            A dictionary with the jobid as a key and job results as the value
        """
        result = dict()
        for jobid in joblist:
            result[jobid] = dict()
            root = self.getresultstree(jobid, True)

            result[jobid]['result'] = root.attrib.get("result")

            for recipe in root.findall("recipeSet/recipe"):
                rid = "R:%s" % recipe.attrib.get("id")
                clogurl = None
                slshwurl = None
                llshwurl = None

                tmp = recipe.find("logs/log[@name='console.log']")
                if tmp is not None:
                    clogurl = tmp.attrib.get("href")

                tmp = recipe.find("task[@name='/test/misc/machineinfo']/logs/"
                                  "log[@name='machinedesc.log']")
                if tmp is not None:
                    slshwurl = tmp.attrib.get("href")

                tmp = recipe.find("task[@name='/test/misc/machineinfo']/logs/"
                                  "log[@name='lshw.log']")
                if tmp is not None:
                    llshwurl = tmp.attrib.get("href")

                rdata = (recipe.attrib.get("result"),
                         recipe.attrib.get("system"),
                         clogurl,
                         slshwurl,
                         llshwurl)

                result[jobid][rid] = rdata

        return result

    def get_ltp_lite_logs(self, job_id):
        """
        Get logs produces by LTP lite, for each recipe in specified job.

        Args:
            job_id: ID of the job for which to retrieve the logs.

        Returns:
            A dictionary in format
              R:<recipe_id>: (<link_to_full_run_log>, <link_to_results>),
            for each recipe in the job.
        """
        job_results = self.getresultstree(job_id, True)
        ltp_logs = {}

        for recipe in job_results.findall('recipeSet/recipe'):
            recipe_id = 'R:%s' % recipe.attrib.get('id')

            ltp_node = recipe.find(
                "task[@name='/kernel/distribution/ltp/lite']"
            )
            if ltp_node is None:
                continue

            run_log = ltp_node.find(
                "logs/log[@name='RHELKT1LITE.FILTERED.run.log']"
            )
            if run_log is not None:
                run_log = run_log.attrib.get('href')

            ltp_results = ltp_node.find(
                "results/result/logs/log[@name='resultoutputfile.log']"
            )
            if ltp_results is not None:
                ltp_results = ltp_results.attrib.get('href')

            ltp_logs[recipe_id] = (run_log, ltp_results)

        return ltp_logs

    def get_mfhost(self):
        fhosts = list()
        for data in self.failures.values():
            fhosts += data[0]

        return max(set(fhosts), key=fhosts.count)

    def jobresult(self, jobid):
        ret = 0
        result = None

        if jobid is not None:
            root = self.getresultstree(jobid)
            result = root.attrib.get("result")

            if result != "Pass":
                ret = 1
            logging.info("job result: %s [%d]", result, ret)

        return (ret, result)

    def getresults(self, jobid=None):
        ret = 0
        fhosts = set()
        tfailures = 0

        if jobid is not None:
            (ret, _) = self.jobresult(jobid)

        if jobid is None or ret != 0:
            for (recipe, data) in self.failures.iteritems():
                # Treat single failure as a fluke during normal run
                if data[2] >= 3 and len(data[0]) < 2:
                    continue
                tfailures += len(data[0])
                logging.info("%s failed %d/%d (%s)%s", recipe, len(data[0]),
                             data[2], ', '.join(data[1]),
                             ""
                             if len(set(data[0])) > 1
                             else ": %s" % data[0][0])

                fhosts = fhosts.union(set(data[0]))
                ret = 1

        if ret != 0 and len(fhosts) > 0:
            msg = "unknown"
            if len(fhosts) > 1:
                msg = "multiple hosts"
            elif len(fhosts) == 1:
                msg = "a single host: %s" % fhosts.pop()

            logging.warning("FAILED %s/%s on %s", tfailures,
                            len(self.recipes), msg)

        return ret

    def recipe_to_job(self, recipe, samehost=False):
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
                root = self.getresultstree(cid)

                if root.attrib.get("status") in ["Completed", "Aborted",
                                                 "Cancelled"]:
                    logging.info("%s status changed to '%s', "
                                 "removing from watchlist",
                                 cid, root.attrib.get("status"))
                    self.watchlist.remove((cid, reschedule, origin))

                    if root.attrib.get("status") == "Cancelled":
                        continue

                    if root.attrib.get("result") != "Pass":
                        tinst = root.find(
                            ".//task[@name='/distribution/install']"
                        )
                        if tinst is not None and \
                                tinst.attrib.get("result") != "Pass":
                            logging.warning("%s failed before kernelinstall, "
                                            "resubmitting", cid)
                            self._forget_cid(cid)
                            newjob = self.recipe_to_job(root, False)
                            newjobid = self.jobsubmit(etree.tostring(newjob))
                            self.add_to_watchlist(newjobid, reschedule, None)
                        else:
                            if origin is None:
                                origin = cid

                            if origin not in self.failures:
                                self.failures[origin] = [[], set(), 1]

                            self.failures[origin][0].append(root.attrib.get(
                                "system"
                            ))
                            self.failures[origin][1].add(root.attrib.get(
                                "result"
                            ))

                            if reschedule:
                                logging.info("%s -> '%s', resubmitting",
                                             cid, root.attrib.get("result"))

                                newjob = self.recipe_to_job(root, False)
                                newjobid = self.jobsubmit(etree.tostring(
                                    newjob
                                ))
                                self.add_to_watchlist(newjobid, False, origin)

                                newjob = self.recipe_to_job(root, True)
                                newjobid = self.jobsubmit(etree.tostring(
                                    newjob
                                ))
                                self.add_to_watchlist(newjobid, False, origin)

            iteration += 1

    def add_to_watchlist(self, jobid, reschedule=True, origin=None):
        root = self.getresultstree(jobid)

        if self.whiteboard is None:
            self.whiteboard = root.find("whiteboard").text

        self.j2r[jobid] = set()
        for el in root.findall("recipeSet/recipe"):
            cid = "R:%s" % el.attrib.get("id")
            self.j2r[jobid].add(cid)
            self.watchlist.add((cid, reschedule, origin))
            self.recipes.add(cid)
            if origin is not None:
                self.failures[origin][2] += 1
            logging.info("added %s to watchlist", cid)

    def wait(self, jobid=None, reschedule=True):
        if jobid is None:
            jobid = self.lastsubmitted
        self.add_to_watchlist(jobid, reschedule)
        self.watchloop()

    def gethost(self, jobid=None):
        if jobid is None:
            jobid = self.lastsubmitted

        logging.info("gethost for %s", jobid)
        root = self.getresultstree(jobid)
        recipe = root.find("recipeSet/recipe")
        logging.info("%s: %s", jobid, recipe.attrib.get("system"))

        return recipe.attrib.get("system")

    def jobsubmit(self, xml):
        jobid = None
        args = ["bkr", "job-submit"]

        if self.jobowner is not None:
            args += ["--job-owner=%s" % self.jobowner]

        args += ["-"]

        bkr = subprocess.Popen(args, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)

        (stdout, _) = bkr.communicate(xml)

        for line in stdout.split("\n"):
            m = re.match(r"^Submitted: \['([^']+)'\]$", line)
            if m:
                jobid = m.group(1)
                break

        logging.info("submitted jobid: %s", jobid)
        self.jobs.add(jobid)
        self.lastsubmitted = jobid

        return jobid

    def run(self, url, release, wait=False, host=None, uid="",
            arch=platform.machine(), reschedule=True):
        ret = 0
        self.failures = {}
        self.recipes = set()
        self.watchlist = set()

        # FIXME Pass or retrieve this explicitly
        uid += " %s" % url.split('/')[-1]

        if host is None:
            hostname = ""
            hostnametag = ""
        else:
            hostname = "(%s) " % host
            hostnametag = '<hostname op="=" value="%s"/>' % host

        jobid = self.jobsubmit(self.getxml({'KVER': release,
                                            'KPKG_URL': url,
                                            'UID': uid,
                                            'ARCH': arch,
                                            'HOSTNAME': hostname,
                                            'HOSTNAMETAG': hostnametag}))

        if wait:
            self.wait(jobid, reschedule)
            ret = self.getresults()

        return ret


def getrunner(rtype, rarg):
    """
    Create an instance of a "runner" subclass with specified arguments.

    Args:
        rtype:  The value of the class "TYPE" member to match.
        rarg:   A dictionary with the instance creation arguments.

    Returns:
        The created class instance.

    Raises:
        ValueError if the rtype match wasn't found.
    """
    for cls in Runner.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown runner type: %s" % rtype)

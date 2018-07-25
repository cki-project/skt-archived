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

from skt.misc import SKT_SUCCESS, SKT_FAIL, SKT_ERROR


MAX_ABORTED = 3


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
        self.j2r = dict()
        self.aborted_count = 0

        logging.info("runner type: %s", self.TYPE)
        logging.info("beaker template: %s", self.template)

    def __getxml(self, replacements):
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
        if bkr.returncode != 0:
            raise Exception(
                'Unable to get Beaker job results for job %s' % jobid
            )

    def __forget_cid(self, cid):
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
                    if not rset:
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
            result[jobid]['status'] = root.attrib.get('status')

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

                test_list = self.get_recipe_test_list(recipe)

                rdata = (recipe.attrib.get("result"),
                         recipe.attrib.get("system"),
                         clogurl,
                         slshwurl,
                         llshwurl,
                         test_list)

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

            ltp_results = ltp_node.find(
                "results/result[@path='RHELKT1LITE.FILTERED']/logs/"
                "log[@name='resultoutputfile.log']"
            )
            if ltp_results is not None:
                ltp_results = ltp_results.attrib.get('href')

            run_log = ltp_node.find(
                "logs/log[@name='RHELKT1LITE.FILTERED.run.log']"
            )
            if run_log is not None:
                run_log = run_log.attrib.get('href')

            ltp_logs[recipe_id] = (ltp_results, run_log)

        return ltp_logs

    def __jobresult(self, jobid):
        """
        Get results for a specific job.

        Args:
            jobid: A Beaker job ID, in 'J:<id>' format.

        Returns:
            A tuple (ret, result), where ret is SKT_SUCCESS if the job ended
            with Pass, SKT_FAIL if a test failure occurred and SKT_ERROR if it
            aborted (infrastructure failure); and result is the job result
            itself.
        """
        result = None

        root = self.getresultstree(jobid)
        result = root.attrib.get("result")
        status = root.attrib.get('status')

        if result == "Pass":
            ret = SKT_SUCCESS
        elif result == 'Warn' and status == 'Aborted':
            ret = SKT_ERROR
        else:
            ret = SKT_FAIL

        logging.info("job result: %s [%d]", result, ret)

        return (ret, result)

    def __getresults(self, jobid=None):
        """
        Get return code based on the job results.

        Args:
            jobid: Beaker job ID in format 'J:<id>'. If not passed, all
                   submitted jobs are checked.

        Returns:
            SKT_SUCCESS if all jobs passed,
            SKT_FAIL in case of failures, and
            SKT_ERROR in case of infrastructure failures.
        """
        ret = SKT_SUCCESS
        fhosts = set()
        tfailures = 0

        if jobid:
            (ret, _) = self.__jobresult(jobid)

        if not jobid or ret != SKT_SUCCESS:
            if self.failures:
                all_aborted = all([data[1] == ('Warn', 'Aborted')
                                   for data in self.failures.values()])
                if all_aborted:
                    logging.warning('All jobs aborted, possible infrastructure'
                                    ' failure.')
                    return SKT_ERROR

                job_cancelled = any(status == 'Cancelled'
                                    for data in self.failures.values()
                                    for _, status in data[1])
                if job_cancelled:
                    return SKT_ERROR
            if self.aborted_count == MAX_ABORTED:
                logging.error('Max count of aborted jobs achieved, please '
                              'check your infrastructure!')

            for (recipe, data) in self.failures.iteritems():
                # Treat single failure as a fluke during normal run
                if data[2] >= 3 and len(data[0]) < 2:
                    continue
                tfailures += len(data[0])
                logging.info("%s failed %d/%d (%s)%s", recipe, len(data[0]),
                             data[2], ', '.join([result for (result, _)  # noqa
                                                 in data[1]]),
                             ""
                             if len(set(data[0])) > 1
                             else ": %s" % data[0][0])

                fhosts = fhosts.union(set(data[0]))
                ret = SKT_FAIL

        if ret and fhosts:
            msg = "unknown"
            if len(fhosts) > 1:
                msg = "multiple hosts"
            elif len(fhosts) == 1:
                msg = "a single host: %s" % fhosts.pop()

            logging.warning("FAILED %s/%s on %s", tfailures,
                            len(self.recipes), msg)

        return ret

    def __recipe_to_job(self, recipe, samehost=False):
        tmp = recipe.copy()

        hreq = tmp.find("hostRequires")
        hostname = hreq.find('hostname')
        if hostname is not None:
            hreq.remove(hostname)
        if samehost:
            hostname = etree.Element("hostname")
            hostname.set("op", "=")
            hostname.set("value", tmp.attrib.get("system"))
            hreq.append(hostname)

        newrs = etree.Element("recipeSet")
        newrs.append(tmp)

        newwb = etree.Element("whiteboard")
        newwb.text = "%s [R:%s]" % (self.whiteboard, tmp.attrib.get("id"))

        if samehost:
            newwb.text += " (%s)" % tmp.attrib.get("system")

        newroot = etree.Element("job")
        newroot.append(newwb)
        newroot.append(newrs)

        return newroot

    def __watchloop(self):
        iteration = 0
        while self.watchlist:
            if iteration:
                time.sleep(self.watchdelay)

            for (cid, reschedule, origin) in self.watchlist.copy():
                root = self.getresultstree(cid)

                result = root.attrib.get('result')
                status = root.attrib.get('status')
                if status in ['Completed', 'Aborted', 'Cancelled']:
                    logging.info("%s status changed to '%s', "
                                 "removing from watchlist",
                                 cid, root.attrib.get("status"))
                    self.watchlist.remove((cid, reschedule, origin))

                    if status == 'Cancelled':
                        logging.error('Cancelled job detected! Cancelling the'
                                      ' rest of running jobs and aborting!')
                        self.failures[cid] = [[root.attrib.get('system')],
                                              set([(result, status)]),
                                              1]
                        jobs_to_cancel = [job for (job, _, _)
                                          in self.watchlist.copy()]
                        if jobs_to_cancel:
                            ret = subprocess.call(['bkr', 'job-cancel']
                                                  + jobs_to_cancel)
                            if ret:
                                logging.info('Failed to cancel the remaining '
                                             'jobs')
                        return

                    if result != 'Pass':
                        tinst = root.find(
                            ".//task[@name='/distribution/kpkginstall']"
                        )
                        if tinst is not None and \
                                tinst.attrib.get("result") != "Pass":
                            logging.warning("%s failed before kernelinstall, "
                                            "resubmitting", cid)
                            self.__forget_cid(cid)
                            self.aborted_count += 1

                            if self.aborted_count < MAX_ABORTED:
                                newjob = self.__recipe_to_job(root, False)
                                newjobid = self.__jobsubmit(
                                    etree.tostring(newjob)
                                )
                                self.__add_to_watchlist(newjobid,
                                                        reschedule,
                                                        None)
                        else:
                            if origin is None:
                                origin = cid

                            if origin not in self.failures:
                                self.failures[origin] = [[], set(), 1]

                            self.failures[origin][0].append(root.attrib.get(
                                "system"
                            ))
                            self.failures[origin][1].add((result, status))

                            if reschedule:
                                logging.info("%s -> '%s', resubmitting",
                                             cid, result)

                                newjob = self.__recipe_to_job(root, False)
                                newjobid = self.__jobsubmit(etree.tostring(
                                    newjob
                                ))
                                self.__add_to_watchlist(newjobid,
                                                        False,
                                                        origin)

                                newjob = self.__recipe_to_job(root, True)
                                newjobid = self.__jobsubmit(etree.tostring(
                                    newjob
                                ))
                                self.__add_to_watchlist(newjobid,
                                                        False,
                                                        origin)

            iteration += 1

    def __add_to_watchlist(self, jobid, reschedule=True, origin=None):
        root = self.getresultstree(jobid)

        if self.whiteboard is None:
            self.whiteboard = root.find("whiteboard").text

        self.j2r[jobid] = set()
        for recipe in root.findall("recipeSet/recipe"):
            cid = "R:%s" % recipe.attrib.get("id")
            self.j2r[jobid].add(cid)
            self.watchlist.add((cid, reschedule, origin))
            self.recipes.add(cid)
            if origin is not None:
                self.failures[origin][2] += 1
            logging.info("added %s to watchlist", cid)

    def wait(self, jobid, reschedule=True):
        self.__add_to_watchlist(jobid, reschedule)
        self.__watchloop()

    def get_recipe_test_list(self, recipe_node):
        """
        Retrieve the list of tests which ran for a particular recipe. All tasks
        after kpkginstall are interpreted as ran tests.

        Args:
            recipe_node: ElementTree node representing the recipe, extracted
                         from Beaker XML or result XML.

        Returns:
            List of test names that ran.
        """
        test_list = []
        after_kpkg = False

        for test_task in recipe_node.findall('task'):
            if after_kpkg:
                test_list.append(test_task.attrib.get('name'))

            if 'kpkginstall' in test_task.attrib.get('name', ''):
                after_kpkg = True

        return test_list

    def __jobsubmit(self, xml):
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

        return jobid

    def run(self, url, release, wait=False, host=None, uid="",
            arch=platform.machine(), reschedule=True):
        """
        Run tests in Beaker.

        Args:
            url:        URL pointing to kernel tarball.
            release:    NVR of the tested kernel.
            wait:       False if skt should exit after submitting the jobs,
                        True if it should wait for them to finish.
            host:       Force testing on a machine with provided hostname.
            uid:        Username jobs should be submitted under. Can be
                        retrieved by running `bkr whoami`.
            arch:       Architecture of the machine the tests should run on, in
                        a format accepted by Beaker. Defaults to architecture
                        of the current machine skt is running on if not
                        specified.
            reschedule: True to try to rule out infrastructure / host-specific
                        failures by resubmitting the job (on both the same and
                        different host), False otherwise.

        Returns:
            SKT_SUCCESS if everything passed
            SKT_FAIL if testing failed
            SKT_ERROR in case of infrastructure error (exceptions are logged)
        """
        ret = SKT_SUCCESS
        self.failures = {}
        self.recipes = set()
        self.watchlist = set()
        self.aborted_count = 0

        # FIXME Pass or retrieve this explicitly
        uid += " %s" % url.split('/')[-1]

        if host is None:
            hostname = ""
            hostnametag = ""
        else:
            hostname = "(%s) " % host
            hostnametag = '<hostname op="=" value="%s"/>' % host

        try:
            jobid = self.__jobsubmit(self.__getxml(
                {'KVER': release,
                 'KPKG_URL': url,
                 'UID': uid,
                 'ARCH': arch,
                 'HOSTNAME': hostname,
                 'HOSTNAMETAG': hostnametag}
            ))

            if wait:
                self.wait(jobid, reschedule)
                ret = self.__getresults()
        except Exception as exc:
            logging.error(exc)
            ret = SKT_ERROR

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

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
"""Class for managing Runner."""
import logging
import os
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as etree

from abc import ABCMeta, abstractmethod
from defusedxml.ElementTree import fromstring

from skt.misc import SKT_SUCCESS, SKT_FAIL, SKT_ERROR
from skt.misc import WaivingWrap


class Runner(object):
    """An abstract test runner"""
    # pylint: disable=too-many-arguments,too-few-public-methods
    __metaclass__ = ABCMeta

    TYPE = 'default'

    @abstractmethod
    def run(self, url, max_aborted, release, wait=False,
            arch=platform.machine(), waiving=True):
        """
        Abstract method, override this to run tests in <implement. specific>

        Args:
            url:         URL pointing to kernel tarball.
            max_aborted: Maximum number of allowed aborted jobs. Abort the
                         whole stage if the number is reached.
            release:     NVR of the tested kernel.
            wait:        False if skt should exit after submitting the jobs,
                         True if it should wait for them to finish.
            arch:        Architecture of the machine the tests should run on,
                         in a format accepted by Beaker. Defaults to
                         architecture of the current machine skt is running on
                         if not specified.

        Returns:
            ret where ret can be
                   SKT_SUCCESS if everything passed
                   SKT_FAIL if testing failed
                   SKT_ERROR in case of infrastructure error (exceptions are
                                                              logged)
        """
        pass   # pragma: no cover


class BeakerRunner(Runner):
    """Beaker test runner"""
    # pylint: disable=too-many-instance-attributes
    TYPE = 'beaker'

    def __init__(self, jobtemplate, jobowner=None, blacklist=None):
        """
        Initialize a runner executing tests on Beaker.

        Args:
            jobtemplate:    Path to a Beaker job template. Can contain a tilde
                            expression ('~' or '~user') to be expanded into
                            the current user's home directory.
            jobowner:       Name of a Beaker user on whose behalf the job
                            should be submitted, or None, if the owner should
                            be the current user.
            blacklist:      Path to file containing hostnames to blacklist from
                            running on, one hostname per line.
        """
        # Beaker job template file path
        # FIXME Move expansion up the call stack, as this limits the class
        # usefulness, because tilde is a valid path character.
        self.template = os.path.expanduser(jobtemplate)
        # Name of a Beaker user on whose behalf the job should be submitted,
        # or None, if the owner should be the current user.
        self.jobowner = jobowner
        self.blacklisted = self.__load_blacklist(blacklist)
        # Delay between checks of Beaker job statuses, seconds
        self.watchdelay = 60
        # Set of recipe sets that didn't complete yet
        self.watchlist = set()
        self.whiteboard = ''
        self.job_to_recipe_set_map = {}
        self.recipe_set_results = {}
        # Keep a set of completed recipes per set so we don't check them again
        self.completed_recipes = {}
        self.aborted_count = 0
        # Set up the default, allowing for overrides with each run
        self.max_aborted = 3

        # determines if termination cleanup was done and all jobs terminated
        self.cleanup_done = False

        # if True, keep waived tests hidden for this run
        self.waiving = None
        # waiving-wrap interface
        self.waiving_wrap = None

        logging.info("runner type: %s", self.TYPE)
        logging.info("beaker template: %s", self.template)

    @classmethod
    def __load_blacklist(cls, filepath):
        hostnames = []

        try:
            with open(filepath, 'r') as fileh:
                for line in fileh:
                    line = line.strip()
                    if line:
                        hostnames.append(line)
        except (IOError, OSError) as exc:
            logging.error('Can\'t access %s!', filepath)
            raise exc
        except TypeError:
            logging.info('No hostname blacklist file passed')

        logging.info('Blacklisted hostnames: %s', hostnames)
        return hostnames

    def __getxml(self, replacements):
        """
        Generate job XML with template replacements applied. Search the
        template for words surrounded by "##" strings and replace them with
        strings from the supplied dictionary.

        Args:
            replacements:   A dictionary of placeholder strings with "##"
                            around them, and their replacements.

        Raises:
            ValueError if the placeholder would be replaced by a non-string
                       object.

        Returns:
            The job XML text with template replacements applied.
        """
        xml = ''
        with open(self.template, 'r') as fileh:
            for line in fileh:
                for match in re.finditer(r"##(\w+)##", line):
                    to_replace = match.group(1)
                    if to_replace in replacements:
                        if not isinstance(replacements[to_replace], str):
                            raise ValueError('XML replace: string expected but'
                                             ' {} is {}'.format(
                                                 to_replace,
                                                 replacements[to_replace]))
                        line = line.replace(match.group(0),
                                            replacements[to_replace])

                xml += line

        return xml

    @classmethod
    def getresultstree(cls, taskspec):
        """
        Retrieve Beaker results for taskspec in Beaker's native XML format.

        Args:
            taskspec:   ID of the job, recipe or recipe set.

        Returns:
            etree node representing the results.
        """
        args = ["bkr", "job-results", "--prettyxml", taskspec]

        bkr = subprocess.Popen(args, stdout=subprocess.PIPE)
        (stdout, _) = bkr.communicate()

        # Write the Beaker results locally so they could be stored as an
        # artifact.
        results_filename = 'beaker-results-{}.xml'.format(taskspec)
        with open(results_filename, 'wb') as fileh:
            fileh.write(stdout)

        return fromstring(stdout)

    def __forget_taskspec(self, taskspec):
        """
        Remove a job or recipe set from self.job_to_recipe_set_map, and recipe
        set from self.watchlist if applicable.

        Args:
            taskspec: The job (J:xxxxx) or recipe set (RS:xxxxx) ID.
        """
        if taskspec.startswith("J:"):
            del self.job_to_recipe_set_map[taskspec]
        elif taskspec.startswith("RS:"):
            self.watchlist.discard(taskspec)
            deljids = set()
            for (jid, rset) in self.job_to_recipe_set_map.iteritems():
                if taskspec in rset:
                    rset.remove(taskspec)
                    if not rset:
                        deljids.add(jid)
            for jid in deljids:
                del self.job_to_recipe_set_map[jid]
        else:
            raise ValueError("Unknown taskspec type: %s" % taskspec)

    def decide_run_result_by_task(self, recipe_result):
        """ Decide run result by tasks. If we have test waiving enabled and the
            test is waived in XML, ignore 'Warn' / 'Panic' / 'Fail' results.

            Args:
                recipe_result: a defused xml

            * When any task aborts and task isn't waived    -> SKT_ERROR

            * When any task warns  and task isn't waived    -> SKT_FAIL
            * When any task fails  and task isn't waived    -> SKT_FAIL
            * When any task panics and task isn't waived    -> SKT_FAIL

            * else: skip over 'Pass' / 'Skip' so eventually -> SKT_SUCCESS

        """
        for task in recipe_result.findall('task'):
            result = task.attrib.get('result')
            status = task.attrib.get('status')

            if result in ['Fail', 'Warn', 'Panic']:
                if self.waiving and self.waiving_wrap.is_task_waived(task):
                    continue
                else:
                    if status == 'Aborted':
                        return SKT_ERROR

                    return SKT_FAIL
            if result in ['Pass', 'Skip']:
                continue

        return SKT_SUCCESS

    def __getresults(self):
        """
        Get return code based on the job results.

        Returns:
            SKT_SUCCESS if all jobs passed,
            SKT_FAIL in case of failures, and
            SKT_ERROR in case of infrastructure failures.
        """
        if not self.job_to_recipe_set_map:
            # We forgot every job / recipe set
            logging.error('All test sets aborted or were cancelled!')
            return SKT_ERROR

        for _, recipe_sets in self.job_to_recipe_set_map.items():
            for recipe_set_id in recipe_sets:
                results = self.recipe_set_results[recipe_set_id]
                for recipe_result in results.findall('.//recipe'):
                    if recipe_result.attrib.get('result') != 'Pass':
                        ret = self.decide_run_result_by_task(recipe_result)

                        if ret != SKT_SUCCESS:
                            logging.info('Failure in a recipe detected!')
                            return ret

        logging.info('Testing passed!')
        return SKT_SUCCESS

    def __blacklist_hreq(self, host_requires):
        """
        Make sure recipe excludes blacklisted hosts.

        Args:
            host_requires: etree node representing "hostRequires" node from the
                           recipe.

        Returns:
            Modified "hostRequires" etree node.
        """
        and_node = host_requires.find('and')
        if and_node is None:
            and_node = etree.Element('and')
            host_requires.append(and_node)

        for disabled in self.blacklisted:
            hostname = etree.Element('hostname')
            hostname.set('op', '!=')
            hostname.set('value', disabled)
            and_node.append(hostname)

        return host_requires

    def __recipe_set_to_job(self, recipe_set, samehost=False):
        tmp = recipe_set.copy()

        for recipe in tmp.findall('recipe'):
            hreq = recipe.find("hostRequires")
            hostname = hreq.find('hostname')
            if hostname is not None:
                hreq.remove(hostname)
            if samehost:
                hostname = etree.Element("hostname")
                hostname.set("op", "=")
                hostname.set("value", recipe.attrib.get("system"))
                hreq.append(hostname)
            else:
                new_hreq = self.__blacklist_hreq(hreq)
                recipe.remove(hreq)
                recipe.append(new_hreq)

        newwb = etree.Element("whiteboard")
        newwb.text = "%s [RS:%s]" % (self.whiteboard, tmp.attrib.get("id"))

        newroot = etree.Element("job")
        newroot.append(newwb)
        newroot.append(tmp)

        return newroot

    def cleanup_handler(self):
        """
        Call cancel_pending_jobs() to cancel all pending jobs

        Returns:
             None
        """
        # don't run cleanup handler twice by accident
        if self.cleanup_done:
            return

        # skt is being terminated, cancel its jobs
        self.cancel_pending_jobs()

        self.cleanup_done = True

    def signal_handler(self, signal, frame):
        # pylint: disable=unused-argument
        """
        Handle SIGTERM|SIGINT: call cleanup_handler() and exit.
        """
        self.cleanup_handler()

        sys.exit(SKT_ERROR)

    def cancel_pending_jobs(self):
        """
        Cancel all recipe sets from self.watchlist and remove their IDs from
        self.job_to_recipe_set_map.
        Cancelling a part of a job leads to cancelling the entire job.
        So we cancel a job if any of its recipesets is in the watchlist.
        """
        logging.info('Cancelling pending jobs!')

        for job_id in set(self.job_to_recipe_set_map):
            ret = subprocess.call(['bkr', 'job-cancel', job_id])
            if ret:
                logging.info('Failed to cancel the remaining recipe sets!')

            self.__forget_taskspec(job_id)

    def __handle_test_abort(self, recipe, recipe_id, recipe_set_id, root):
        if self.decide_run_result_by_task(recipe) == SKT_SUCCESS:
            # A task that is waived aborted or panicked. Waived tasks are
            # appended to the end of the recipe, so we should be able to
            # safely ignore this.
            return

        logging.warning('%s from %s aborted!',
                        recipe_id,
                        recipe_set_id)
        self.__forget_taskspec(recipe_set_id)
        self.aborted_count += 1

        if self.aborted_count < self.max_aborted:
            logging.warning('Resubmitting aborted %s',
                            recipe_set_id)
            newjob = self.__recipe_set_to_job(root)
            newjobid = self.__jobsubmit(etree.tostring(newjob))
            self.__add_to_watchlist(newjobid)

    def __handle_test_fail(self, recipe):
        # Something in the recipe set really reported failure
        test_failure = False
        # set to True when test failed, but is waived
        waiving_skip = False

        if self.get_kpkginstall_task(recipe) is None:
            # we don't waiving kernel-install task :-)
            # Assume the kernel was installed by default and
            # everything is a test
            test_failure = True

        elif self.decide_run_result_by_task(recipe) == SKT_SUCCESS:
            # A task that is waived failed. Waived tasks are
            # appended to the end of the recipe, so we should be able to
            # safely ignore this.
            waiving_skip = True
            # set this just fyi - we will continue anyway
            test_failure = True
        else:
            test_list = self.get_recipe_test_list(recipe)

            for task in recipe.findall('task'):
                result = task.attrib.get('result')

                if result != 'Pass' and result != 'Skip':
                    if task.attrib.get('name') in test_list:
                        test_failure = True

                    break

        return test_failure, waiving_skip

    def __watchloop(self):
        while self.watchlist:
            time.sleep(self.watchdelay)

            if self.max_aborted == self.aborted_count:
                # Remove / cancel all the remaining recipe set IDs and abort
                self.cancel_pending_jobs()

            for recipe_set_id in self.watchlist.copy():
                root = self.getresultstree(recipe_set_id)
                recipes = root.findall('.//recipe')

                for recipe in recipes:
                    result = recipe.attrib.get('result')
                    status = recipe.attrib.get('status')
                    recipe_id = 'R:' + recipe.attrib.get('id')
                    if status not in ['Completed', 'Aborted', 'Cancelled'] or \
                            recipe_id in self.completed_recipes[recipe_set_id]:
                        # continue watching unfinished recipes
                        continue

                    logging.info("%s status changed to %s", recipe_id, status)
                    self.completed_recipes[recipe_set_id].add(recipe_id)
                    if len(self.completed_recipes[recipe_set_id]) == \
                            len(recipes):
                        self.watchlist.remove(recipe_set_id)
                        self.recipe_set_results[recipe_set_id] = root

                    if result == 'Pass':
                        # some recipe passed, nothing to do here
                        continue

                    if status == 'Cancelled':
                        # job got cancelled for some reason, there's probably
                        # an external reason
                        logging.error('Cancelled run detected! Cancelling the '
                                      'rest of runs and aborting!')
                        self.cancel_pending_jobs()
                        return

                    if result == 'Warn' and status == 'Aborted':
                        self.__handle_test_abort(recipe, recipe_id,
                                                 recipe_set_id, root)
                        continue

                    # check for test failure
                    test_failure, waive_skip = self.__handle_test_fail(recipe)
                    if waive_skip:
                        logging.info("recipe %s waived task(s) failed",
                                     recipe_id)
                        continue

                    if not test_failure:
                        # Recipe failed before the tested kernel was installed
                        self.__forget_taskspec(recipe_set_id)
                        self.aborted_count += 1

                        if self.aborted_count < self.max_aborted:
                            logging.warning('Infrastructure-related problem '
                                            'found, resubmitting %s',
                                            recipe_set_id)
                            newjob = self.__recipe_set_to_job(root)
                            newjobid = self.__jobsubmit(etree.tostring(newjob))
                            self.__add_to_watchlist(newjobid)

    def __add_to_watchlist(self, jobid):
        root = self.getresultstree(jobid)

        if not self.whiteboard:
            self.whiteboard = root.find("whiteboard").text

        self.job_to_recipe_set_map[jobid] = set()
        for recipe_set in root.findall("recipeSet"):
            set_id = "RS:%s" % recipe_set.attrib.get("id")
            self.job_to_recipe_set_map[jobid].add(set_id)
            self.watchlist.add(set_id)
            self.completed_recipes[set_id] = set()
            logging.info("added %s to watchlist", set_id)

    def wait(self, jobid):
        """
        Add jobid to watchlist, enter watchloop and wait for jobid to finish.

        Args:
            jobid: id of a Beaker job like 1234

        """
        self.__add_to_watchlist(jobid)
        self.__watchloop()

    def get_recipe_test_list(self, recipe_node):
        """
        Retrieve the list of tests which ran for a particular recipe. All tasks
        after kpkginstall (including the kpkginstall task itself), which were
        not skipped, are interpreted as ran tests. If the kpkginstall task
        doesn't exist, assume every task is a test and the kernel was installed
        by default.

        Args:
            recipe_node: ElementTree node representing the recipe, extracted
                         from Beaker XML or result XML.

        Returns:
            List of test names that ran.
        """
        test_list = []
        after_kpkg = True if self.get_kpkginstall_task(recipe_node) is None \
            else False

        for test_task in recipe_node.findall('task'):
            fetch = test_task.find('fetch')
            if fetch is not None and \
                    'kpkginstall' in fetch.attrib.get('url', ''):
                after_kpkg = True

            if after_kpkg and test_task.attrib.get('result') != 'Skip':
                test_list.append(test_task.attrib.get('name'))

        return test_list

    @classmethod
    def get_kpkginstall_task(cls, recipe_node):
        """
        Return a kpkginstall task node for a given recipe.

        Returns:
            Etree node representing kpkginstall task, None if there is no such
            task.
        """
        for task in recipe_node.findall('task'):
            fetch = task.find('fetch')
            if fetch is not None and \
                    'kpkginstall' in fetch.attrib.get('url', ''):
                return task

        return None

    def __jobsubmit(self, xml):
        # pylint: disable=no-self-use
        jobid = None
        args = ["bkr", "job-submit"]

        if self.jobowner is not None:
            args += ["--job-owner=%s" % self.jobowner]

        args += ["-"]

        bkr = subprocess.Popen(args, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE)

        (stdout, _) = bkr.communicate(xml)

        for line in stdout.split("\n"):
            match = re.match(r"^Submitted: \['([^']+)'\]$", line)
            if match:
                jobid = match.group(1)
                break

        if not jobid:
            raise Exception('Unable to submit the job!')

        logging.info("submitted jobid: %s", jobid)

        return jobid

    def run(self, url, max_aborted, release, wait=False,
            arch=platform.machine(), waiving=True):
        """
        Run tests in Beaker.

        Args:
            url:         URL pointing to kernel tarball.
            max_aborted: Maximum number of allowed aborted jobs. Abort the
                         whole stage if the number is reached.
            release:     NVR of the tested kernel.
            wait:        False if skt should exit after submitting the jobs,
                         True if it should wait for them to finish.
            arch:        Architecture of the machine the tests should run on,
                         in a format accepted by Beaker. Defaults to
                         architecture of the current machine skt is running on
                         if not specified.
            waiving:        Hide tests that are waived

        Returns:
            ret where ret can be
                   SKT_SUCCESS if everything passed
                   SKT_FAIL if testing failed
                   SKT_ERROR in case of infrastructure error (exceptions are
                                                              logged)
        """
        # pylint: disable=too-many-arguments
        ret = SKT_SUCCESS
        self.watchlist = set()
        self.job_to_recipe_set_map = {}
        self.recipe_set_results = {}
        self.completed_recipes = {}
        self.aborted_count = 0
        self.max_aborted = max_aborted
        self.waiving = waiving
        self.waiving_wrap = WaivingWrap(self.waiving)

        try:
            job_xml_tree = fromstring(self.__getxml(
                {'KVER': release,
                 'KPKG_URL': url,
                 'ARCH': arch}
            ))
            for recipe in job_xml_tree.findall('recipeSet/recipe'):
                hreq = recipe.find('hostRequires')
                new_hreq = self.__blacklist_hreq(hreq)
                recipe.remove(hreq)
                recipe.append(new_hreq)

            jobid = self.__jobsubmit(etree.tostring(job_xml_tree))

            if wait:
                self.wait(jobid)
                ret = self.__getresults()
                logging.debug(
                    "Got return code when gathering results: %s", ret
                )
        except (Exception, BaseException) as exc:
            logging.error(exc)
            if isinstance(exc, SystemExit):
                # call cleanup handler to kill submitted jobs
                self.cleanup_handler()
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

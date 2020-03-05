# Copyright (c) 2017-2020 Red Hat, Inc. All rights reserved. This copyrighted
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
import copy
import logging
import os
import platform
import re
import subprocess
import time

from defusedxml.ElementTree import fromstring
from defusedxml.ElementTree import tostring
from defusedxml.ElementTree import ParseError

from skt.misc import SKT_SUCCESS, SKT_FAIL, SKT_ERROR, SKT_BOOT
from skt.misc import is_task_waived
from cki_lib.misc import safe_popen, retry_safe_popen


class ConditionCheck:
    def __init__(self, retval, **kwargs):
        self.retval = retval
        self.kwargs = kwargs

    def __str__(self):
        values = ' '.join([f'{arg}={self.kwargs[arg]}' for arg in self.kwargs])

        return f'retval={self.retval} {values}'

    def __call__(self, task, is_task_waived_func, prev_task):
        """ Evaluates the condition and return retval if matched, else None.

            Args:
                task: defusedxml of the task node
                is_task_waived_func: function used to test whether the task
                                     is waived
                prev_task: task that was run before this one, or None if this
                           task is the first task in the recipe
        """
        task_results = {
            'result': task.attrib.get('result'),
            'status': task.attrib.get('status'),
            'waived': is_task_waived_func(task),
            'prev_task_panicked_and_waived':
                True if prev_task is not None and (
                    is_task_waived_func(prev_task) and
                    prev_task.attrib.get('result') == 'Panic'
                ) else False
        }

        if not self.kwargs:
            # don't match empty conditions as satisfied
            return None

        for arg in self.kwargs:
            if task_results[arg] != self.kwargs[arg]:
                # the status entry doesn't match all the conditions
                return None

        # the status entry matches all the conditions
        return self.retval


result_condition_checks = [
    # This contains objects that will return <retval> (first parameter), when
    # all the specified conditions are met. Empty conditions with no keywords
    # are never met.

    # Previous task was waived and panicked, which causes the next
    # task to abort. The task is waived for a reason, return
    # SKT_SUCCESS.
    ConditionCheck(SKT_SUCCESS, result='Warn', waived=False, status='Aborted',
                   prev_task_panicked_and_waived=True),

    # A non-waived task panicked, return SKT_FAIL and don't confuse
    # this with infra-errors.
    ConditionCheck(SKT_FAIL, result='Panic', waived=False),

    # A non-waived tasked aborted, return SKT_ERROR, possible
    # infra issue.
    ConditionCheck(SKT_ERROR, result='Warn',  waived=False, status='Aborted'),

    # The rest of the fall-through conditions.
    ConditionCheck(SKT_FAIL, result='Warn', waived=False),
    ConditionCheck(SKT_FAIL, result='Fail', waived=False),
]


class BeakerRunner:
    """Beaker test runner"""
    # pylint: disable=too-many-instance-attributes

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

        # the actual retcode to return is stored here
        self.retcode = SKT_ERROR

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

    def get_recipset_group(self, taskspec):
        for (jid, rset) in self.job_to_recipe_set_map.items():
            if taskspec in rset:
                return self.getresultstree(jid).attrib['group']

        return None

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
                                             ' {} is {}'.format(to_replace,
                                                                replacements
                                                                [to_replace]))
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

        err_strings = ["ProtocolError", "503 Service Unavailable"]
        stdout, stderr, returncode = retry_safe_popen(err_strings, args,
                                                      stderr=subprocess.PIPE,
                                                      stdout=subprocess.PIPE)

        if returncode:
            logging.warning(stdout)
            logging.warning(stderr)
            raise RuntimeError('failed getting Beaker job-results')

        # Write the Beaker results locally so they could be stored as an
        # artifact.
        results_filename = 'beaker-results-{}.xml'.format(taskspec)
        with open(results_filename, 'w') as fileh:
            fileh.write(stdout)

        return fromstring(stdout)

    def __forget_taskspec(self, recipe_set_id):
        """
        Remove recipe set from self.job_to_recipe_set_map and self.watchlist
        (if applicable).

        Args:
            recipe_set_id: recipe set (RS:xxxxx) ID.
        """
        self.watchlist.discard(recipe_set_id)
        deljids = set()
        for (jid, rset) in self.job_to_recipe_set_map.items():
            if recipe_set_id in rset:
                rset.remove(recipe_set_id)
                if not rset:
                    deljids.add(jid)
        for jid in deljids:
            del self.job_to_recipe_set_map[jid]

    def _not_booting(self, recipe):
        """
        Check if the kernel we should test failed to boot. In these cases, the
        Boot test throws EWD. We need to check that EWD wasn't hit sooner (e.g.
        the distro failed to install).

        Returns:
            True if the issue is caused by a kernel not booting,
            False otherwise.
        """
        is_boot_test = False

        for task in recipe.findall('task'):
            if task.attrib['name'] == "Boot test":
                is_boot_test = True

            for res in task.findall('.//results/'):
                if res.text and 'External Watchdog Expired' in res.text:
                    if is_boot_test:
                        return True
                    else:
                        return False

            if is_boot_test:
                # If we got here it means that we got past the boot without
                # hitting EWD. Since we want to only check the boot failure and
                # not test troubles, return here.
                return False

    def decide_run_result_by_task(self, recipe_result, recipe_id):
        """ Return result of a single recipe decided by tasks. The conditions
            to test are read from result_condition_checks in their natural
            specified order.

            Args:
                recipe_result: a defused xml
                recipe_id: id of the recipe from the XML, prefixed with R:
            Returns:
                retval, msg where retval is a return code like SKT_SUCCESS,
                            SKT_BOOT, ... and msg is an explanation of why

        """
        # If the recipe passed, then there's little to do.
        if recipe_result.attrib.get('result') == 'Pass':
            return SKT_SUCCESS, f'recipeid {recipe_id} passed all tests'

        if self._not_booting(recipe_result):
            return SKT_BOOT, f'recipeid {recipe_id} hit EWD in boottest!'

        prev_task = None
        for task in recipe_result.findall('task'):
            for cond_check in result_condition_checks:
                retval = cond_check(task, is_task_waived, prev_task)
                if retval is not None:
                    return retval, f'recipeid {recipe_id} -> {str(cond_check)}'

            # remember the previous task
            prev_task = task

        # It's possible that failing tests were just waived...
        return SKT_SUCCESS, f'recipeid {recipe_id} passed with waived tests'

    def __getresults(self):
        """
        Get return code based on the job results. This processes all recipes.
        The priority is (from highest to lowest):
        # 0) Infra issue - all tests aborted or cancelled
        # 1) Unwaived infra issue in any recipe
        # 2) Boot failure
        # 3) Unwaived test failure
        # 4) All tests OK or alle OK with test waived

        Returns:
            SKT_SUCCESS if all jobs passed,
            SKT_FAIL in case of failures, and
            SKT_ERROR in case of infrastructure failures.
            SKT_BOOT in case of boot failure.
        """
        if not self.job_to_recipe_set_map:
            # We forgot every job / recipe set
            logging.error('All test sets aborted or were cancelled!')
            return SKT_ERROR

        rcpid_and_results = []
        for _, recipe_sets in self.job_to_recipe_set_map.items():
            for recipe_set_id in recipe_sets:
                results = self.recipe_set_results[recipe_set_id]
                for recipe_result in results.findall('.//recipe'):
                    rcpid = 'R:' + recipe_result.attrib.get('id')
                    ret, msg = self.decide_run_result_by_task(recipe_result,
                                                              rcpid)

                    # log output of decide_run_result_by_task for final rc only
                    logging.info(msg)

                    rcpid_and_results.append((rcpid, ret))

        for ret in [SKT_ERROR, SKT_BOOT, SKT_FAIL]:
            for rcpid, result in rcpid_and_results:
                if ret == result:
                    logging.info(f'Failure ({ret}) in recipeid {rcpid}'
                                 f' detected!')
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

        if host_requires.get('force'):
            # don't add blacklist if the host is forced
            return host_requires

        and_node = host_requires.find('and')
        if and_node is None:
            and_node = fromstring('<and />')
            host_requires.append(and_node)

        invalid_entries_reported = False
        for disabled in self.blacklisted:
            try:
                hostname = fromstring(f'<hostname op="!=" value="{disabled}" '
                                      f'/>')
                and_node.append(hostname)
            except ParseError:
                # do not accept or try to quote any html/xml values; only
                # plaintext values like "host1" are accepted
                if not invalid_entries_reported:
                    logging.info('The blacklist or a part of it is invalid!')
                    invalid_entries_reported = True

        return host_requires

    def __recipe_set_to_job(self, recipe_set, samehost=False):
        tmp = copy.deepcopy(recipe_set)

        try:
            group = self.get_recipset_group('RS:{}'.format(recipe_set.
                                                           attrib['id']))
        except KeyError:
            # don't set group later on
            group = None

        for recipe in tmp.findall('recipe'):
            hreq = recipe.find("hostRequires")
            hostname = hreq.find('hostname')
            if hostname is not None:
                hreq.remove(hostname)
            if samehost:
                value = recipe.attrib.get("system")
                hostname = fromstring(f'<hostname op="=" value="{value}"/>')
                hreq.append(hostname)
            else:
                new_hreq = self.__blacklist_hreq(hreq)
                recipe.remove(hreq)
                recipe.append(new_hreq)

        newwb = fromstring("<whiteboard/>")
        newwb.text = "%s [RS:%s]" % (self.whiteboard, tmp.attrib.get("id"))

        newroot = fromstring("<job/>")
        if group:
            newroot.attrib['group'] = group

        newroot.append(newwb)
        newroot.append(tmp)

        return newroot

    def cancel_pending_jobs(self):
        """
        Cancel all recipe sets from self.watchlist.
        Cancelling a part of a job leads to cancelling the entire job.
        So we cancel a job if any of its recipesets is in the watchlist.
        """
        logging.info('Cancelling pending jobs!')

        for job_id in set(self.job_to_recipe_set_map):
            _, _, ret = safe_popen(['bkr', 'job-cancel', job_id])
            if ret:
                logging.info('Failed to cancel the remaining recipe sets!')

    def __handle_test_abort(self, recipe, recipe_id, recipe_set_id, root):
        if self._not_booting(recipe):
            return

        retval, _ = self.decide_run_result_by_task(recipe, recipe_id)
        if retval == SKT_SUCCESS:
            # A task that is waived aborted or panicked. Waived tasks are
            # appended to the end of the recipe, so we should be able to
            # safely ignore this.
            return

        logging.warning('%s from %s aborted!',
                        recipe_id,
                        recipe_set_id)
        self.aborted_count += 1

        if self.aborted_count < self.max_aborted:
            logging.warning('Resubmitting aborted %s',
                            recipe_set_id)
            newjob = self.__recipe_set_to_job(root)
            newjobid = self.__jobsubmit(tostring(newjob))
            self.__add_to_watchlist(newjobid)

        self.watchlist.discard(recipe_set_id)

    def __handle_test_fail(self, recipe, recipe_id):
        # Something in the recipe set really reported failure
        test_failure = False
        # set to True when test failed, but is waived
        waiving_skip = False

        if self.get_kpkginstall_task(recipe) is None:
            # we don't waive the kernel-install task :-)
            # Assume the kernel was installed by default and
            # everything is a test
            test_failure = True

        elif self.decide_run_result_by_task(recipe, recipe_id)[0]\
                == SKT_SUCCESS:
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
                    test_failure, waive_skip = \
                        self.__handle_test_fail(recipe, recipe_id)
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
                            newjobid = self.__jobsubmit(tostring(newjob))
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
        err_strings = ["connection to beaker.engineering.redhat.com failed",
                       "Can't connect to MySQL server on"]
        stdout, stderr, retcode = retry_safe_popen(err_strings, args,
                                                   stdin_data=xml,
                                                   stdin=subprocess.PIPE,
                                                   stderr=subprocess.PIPE,
                                                   stdout=subprocess.PIPE)

        for line in stdout.split("\n"):
            match = re.match(r"^Submitted: \['([^']+)'\]$", line)
            if match:
                jobid = match.group(1)
                break

        if not jobid:
            logging.info(f'retcode={retcode}, stderr={stderr}')
            logging.info(stdout)
            raise Exception('Unable to submit the job!')

        logging.info("submitted jobid: %s", jobid)

        return jobid

    def add_blacklist2recipes(self, job_xml_tree):
        """ Make sure blacklist is added to all recipes.

            Args:
               job_xml_tree: ElementTree.Element with all recipeSets/recipes

        """
        for recipe in job_xml_tree.findall('recipeSet/recipe'):
            hreq = recipe.find('hostRequires')
            new_hreq = self.__blacklist_hreq(hreq)
            recipe.remove(hreq)
            recipe.append(new_hreq)

    def run(self, url, max_aborted, release, wait=False,
            arch=platform.machine()):
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

        Returns:
            ret where ret can be
                   SKT_SUCCESS if everything passed
                   SKT_FAIL if testing failed
                   SKT_ERROR in case of infrastructure error (exceptions are
                                                              logged)
                   SKT_BOOT if the boot test failed
        """
        # pylint: disable=too-many-arguments
        self.watchlist = set()
        self.job_to_recipe_set_map = {}
        self.recipe_set_results = {}
        self.completed_recipes = {}
        self.aborted_count = 0
        self.max_aborted = max_aborted

        try:
            job_xml_tree = fromstring(self.__getxml(
                {'KVER': release,
                 'KPKG_URL': url,
                 'ARCH': arch}
            ))
            # add blacklist to all recipes
            self.add_blacklist2recipes(job_xml_tree)

            # convert etree to xml and submit the job to Beaker
            jobid = self.__jobsubmit(tostring(job_xml_tree))

            if wait:
                # wait for completion, resubmit jobs as needed
                self.wait(jobid)
                # get return code and report it
                self.retcode = self.__getresults()
                logging.debug(
                    "Got return code when gathering results: %s", self.retcode
                )
            else:
                # not waiting -> change retcode to success
                self.retcode = SKT_SUCCESS

        except (Exception, BaseException) as exc:
            logging.error(exc)

        return self.retcode

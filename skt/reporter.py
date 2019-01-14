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
"""Class for managing Reporter."""
import ConfigParser
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import enum
import logging
import os
import re
import smtplib
import sys

from jinja2 import Environment, FileSystemLoader

from skt.console import gzipdata
from skt.misc import get_patch_name, get_patch_mbox
import skt.runner

# Determine the absolute path to this script and the directory which holds
# the jinja2 templates.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = "{}/templates".format(SCRIPT_DIR)

# Set up the jinja2 environment which can be reused throughout this script.
JINJA_ENV = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    trim_blocks=True,  # Remove first newline after a jinja2 block
    keep_trailing_newline=True,  # Preserve trailing newlines
    lstrip_blocks=True,  # Strip whitespace from the left side of tags
)


class MultiReportFailure(enum.IntEnum):
    """IntEnum to track multireport failure statuses."""

    PASS = 0
    MERGE = 1
    BUILD = 2
    TEST = 3


def load_state_cfg(statefile):
    """Load state information from a state file.

    It takes the current config and adjusts state information within the
    config based on the state file provided.

    Args:
        statefile:      Path to a skt state file to read

    Returns: A cfg dictionary.

    """
    cfg = {}
    state_to_report = ConfigParser.ConfigParser()
    with open(statefile, 'r') as fileh:
        state_to_report.readfp(fileh)

    # FIXME This can be simplified or removed after configuration and
    # state split
    for (name, value) in state_to_report.items('state'):
        if not cfg.get(name):
            if name.startswith('jobid_'):
                cfg.setdefault("jobs", set()).add(value)
            if name.startswith('recipesetid_'):
                cfg.setdefault("recipe_sets", set()).add(value)
            elif name.startswith('mergerepo_'):
                cfg.setdefault("mergerepos", list()).append(value)
            elif name.startswith('mergehead_'):
                cfg.setdefault("mergeheads", list()).append(value)
            elif name.startswith('localpatch_'):
                cfg.setdefault("localpatches", list()).append(value)
            elif name.startswith('patchwork_'):
                cfg.setdefault("patchworks", list()).append(value)
            cfg[name] = value

    # Get runner info
    if state_to_report.has_section('runner'):
        runner_config = {}
        for (key, val) in state_to_report.items('runner'):
            if key != 'type':
                runner_config[key] = val
            cfg['runner'] = [
                state_to_report.get('runner', 'type'),
                runner_config
            ]
    else:
        logging.debug('No runner info found in state file, test runs will'
                      ' not be reported')

    return cfg


class Reporter(object):
    """Abstract test result reporter"""
    # pylint: disable=too-few-public-methods
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
        # mergedata: a dict containing following keys:
        # 'baserepo'     - repo URL
        # 'basehead'     - base commit SHA
        # 'basesubject'  - subject for 'basehead' commit
        # 'merge_git'    - (merge repo, head) tuples
        # 'localpatch'   - an array of paths to files like ['/tmp/patch.txt']
        # 'patchwork'    - (link to patchwork patch, patchname) tuples
        self.mergedata = None
        # Save list of state files because self.cfg will be overwritten. This
        # can be changed to access a specific parameter after the FIXME with
        # passing only explicit parameters is implemented. Only test run and
        # runner info is used during reporting so we are good to go.
        self.statefiles = cfg.get('result', [])
        # Notion of failure for subject creation with multireporting. The
        # earliest problem in the pipeline is reported.
        self.multireport_failed = MultiReportFailure.PASS
        # We need to save the job IDs when iterating over state files when
        # multireporting
        self.multi_job_ids = []

    def __stateconfigdata(self, mergedata):
        # Store the repo URL, base commit SHA, and subject for that commit.
        mergedata['baserepo'] = self.cfg.get("baserepo")
        mergedata['basehead'] = self.cfg.get("basehead")
        mergedata['basesubject'] = self.cfg.get('basesubject')

        if self.cfg.get("mergerepos"):
            mrl = self.cfg.get("mergerepos")
            mhl = self.cfg.get("mergeheads")
            for idx, mrl_item in enumerate(mrl):
                mergedata['merge_git'].append((mrl_item, mhl[idx]))

        if self.cfg.get("localpatches"):
            mergedata['localpatch'] = [
                os.path.basename(patch_path) for patch_path
                in self.cfg.get("localpatches")
            ]

        if self.cfg.get("patchworks"):
            for purl in self.cfg.get("patchworks"):
                patch_mbox = get_patch_mbox(purl)
                patchname = get_patch_name(patch_mbox)
                mergedata['patchwork'].append((purl, patchname))

        return mergedata

    def _update_mergedata(self):
        mergedata = {
            'base': None,
            'merge_git': [],
            'localpatch': [],
            'patchwork': [],
        }

        mergedata = self.__stateconfigdata(mergedata)
        self.mergedata = mergedata

    def __getmergelog(self):
        """
        Read the merge log and remove unneeded lines.
        Returns: A string containing a filtered merge log if the merge log
                 exists. Otherwise None is returned.
        """
        # Did the merge fail?
        if not self.cfg.get('mergelog'):
            return None

        result = ""
        with open(self.cfg.get("mergelog"), 'r') as fileh:
            for line in fileh:
                # Skip the useless part of the 'git am' output
                if ("The copy of the patch" in line) \
                        or ('see the failed patch' in line):
                    break
                result += line

        return result

    def __getbuildlog(self, suffix=None):
        """
        Read a build log from the disk and add it to the list of attachments.
        Args:
            suffix: The extra text to add to the build log file name. This is
                    helpful for distinguishing between different architectures
                    that were built. Examples: 'aarch64', 'x86_64'.
        Returns: The name of the attachment that was added if the build log
                 exists from a failed build. Otherwise None is returned.
        """
        # Did the build fail?
        if not self.cfg.get('buildlog'):
            return None

        if suffix:
            attachment_name = "build_{}.log.gz".format(suffix)
        else:
            attachment_name = "build.log.gz"

        with open(self.cfg.get("buildlog"), 'r') as fileh:
            self.attach.append((attachment_name, gzipdata(fileh.read())))

        return attachment_name

    @classmethod
    def __get_failed_task_log(cls, task_node):
        """
        Get logs from a failed task and its subtasks.

        Returns: A list of log file URLs.
        """
        useless_logs = ['harness.log', 'setup.log']

        # Get the logs from the main task.
        task_logs = [
            log.attrib.get('href') for log in task_node.findall('logs/log')
            if log.attrib.get('name') not in useless_logs
        ]

        # If this task has subtasks, get those as well.
        subtask_logs = [subtask.findall('logs/log') for subtask in
                        task_node.findall('results/result')
                        if subtask.attrib.get('result') != 'Pass']

        for subtask_log in subtask_logs:
            log_urls = [log.attrib.get('href') for log in subtask_log]
            task_logs += log_urls

        return task_logs

    @classmethod
    def __get_task(cls, recipe, task_name):
        """
        Get the task XML node for a task.
        Returns: An XML element for a particular task.
        """
        xml_task_element = "task[@name='{}']".format(task_name)
        task_node = recipe.find(xml_task_element)
        return task_node

    def __getjobresults(self):
        """
        Retrieve job results which should be appended to the report.
        Every test run has a list of receipe sets that were run. Each set
        can contain one or more recipes. Each recipe has one or more tasks
        that run individual tests.

        Returns:
            A list of lines representing results of test runs.
        """
        result = []

        runner = skt.runner.getrunner(*self.cfg.get("runner"))

        # Get the list of recipes sets that were run.
        recipe_set_list = self.cfg.get('recipe_sets', [])

        # Get the XML result tree for each recipe set.
        recipe_set_results = [runner.getresultstree(recipe_set_id)
                              for recipe_set_id in recipe_set_list]

        # Loop through each recipe set to examine each recipe (and its tasks).
        for recipe_set_result in recipe_set_results:
            for recipe in recipe_set_result.findall('recipe'):

                passed_tasks = []
                failed_tasks = []

                # Get basic information about this recipe.
                recipe_result = recipe.attrib.get('result')
                recipe_data = {
                    'id': recipe.attrib.get('id'),
                    'arch': recipe.find(
                        'hostRequires/and/arch').attrib.get('value'),
                    'result': recipe_result,
                }

                # Get a list of the tests that were run for this recipe.
                tests_run = runner.get_recipe_test_list(recipe)
                for test_name in tests_run:

                    # Get the XML node of the task and basic data
                    task_node = self.__get_task(recipe, test_name)
                    task_name = task_node.attrib.get('name')
                    task_result = task_node.attrib['result']
                    task_status = task_node.attrib['status']
                    task_url = ''

                    # Find git source, if any
                    fetch = task_node.find('fetch')
                    if fetch is not None:
                        task_url = fetch.attrib.get('url')

                    if task_result == 'Pass':
                        passed_tasks.append({'name': task_name,
                                             'url': task_url})
                    elif (task_result == 'Warn' and task_status == 'Aborted'):
                        # Don't add tasks that aborted to the lists
                        continue
                    else:
                        # Retrieve all needed data about the failed task
                        logs = self.__get_failed_task_log(task_node)
                        # If the task caused a kernel panic, add a link to the
                        # console log since that's the one containing the
                        # actual trace.
                        if task_result == 'Panic':
                            console = recipe.find(
                                "logs/log[@name='console.log']")
                            if console is not None:
                                logs.append(console.attrib.get('href'))

                        failed_tasks.append({'name': task_name,
                                             'logs': logs,
                                             'url': task_url})

                recipe_data['passed_tasks'] = passed_tasks
                recipe_data['failed_tasks'] = failed_tasks

                # Add all the details about this recipe to the main result.
                result.append(recipe_data)

        return result

    def _get_multireport(self):
        """
        Generate a report based on an skt rc file and various state files.

        Returns: A long string of test results suitable for sending via email
                 or displaying directly in a terminal.
        """
        template_name = self.cfg['template']

        # Ensure the template name is valid.
        assert re.match(r'^[A-Za-z0-9_-]+$', template_name), \
            "Invalid template name"

        # Set the template filename and load the template.
        template_file = "report_{}.j2".format(template_name)
        template = JINJA_ENV.get_template(template_file)

        # If we don't have any state files, this is likely a run with a single
        # test. Make a single entry in self.statefiles so we can re-use the
        # loop below.
        self.statefiles = self.statefiles or [None]

        # Set up a list to hold our data for each job.
        report_jobs = []

        # Loop through each of the statefiles provided.
        for statefile in self.statefiles:
            # If the statefile is none, this is a single run report and the
            # state information has already been loaded into self.cfg.
            if statefile:
                self.cfg = load_state_cfg(statefile)

            # Update the data about the patches merged.
            self._update_mergedata()

            # Add the list of jobs foud in this statefile to the list.
            if self.cfg.get("jobs"):
                jobs = [x for x in sorted(self.cfg.get('jobs'))]
                self.multi_job_ids += jobs

            # Did the merge fail? If so, stop right here and send the report.
            # We didn't build any kernels or test anything after that failure.
            if self.cfg.get('mergelog'):
                self.multireport_failed = MultiReportFailure.MERGE
                result = template.render(
                    mergedata=self.mergedata,
                    cfg=self.cfg,
                    mergelog=self.__getmergelog(),
                    multireport_failed=self.multireport_failed,
                )
                return result

            # Store the data about this job for the report.
            job_data = self.cfg

            # If our make options contain '-C <path>', we should remove that.
            if 'make_opts' in job_data:
                pattern = r' -C [\w\-/\d]+'
                job_data['make_opts'] = re.sub(
                    pattern, '', job_data['make_opts']
                )

            # Did the compile fail for this job?
            # If yes, store the build log and skip to the next job since we
            # didn't test anything in this job.
            if self.cfg.get('buildlog'):
                self.multireport_failed = MultiReportFailure.BUILD
                kernel_arch = self.cfg.get('kernel_arch')
                job_data['buildlog'] = self.__getbuildlog(kernel_arch)
                report_jobs.append(job_data)
                continue

            # Did the tests run for this job?
            if self.cfg.get('runner'):
                # If the tests failed, mark the result as a test failure.
                if self.cfg.get('retcode') != '0':
                    self.multireport_failed = MultiReportFailure.TEST

                # Collect the tests results and append them to our list.
                job_data['test_results'] = self.__getjobresults()
                report_jobs.append(job_data)

        # Render the report.
        result = template.render(
            mergedata=self.mergedata,
            cfg=self.cfg,
            report_jobs=report_jobs,
            multireport_failed=self.multireport_failed,
        )
        return result

    @classmethod
    def _get_repo_name(cls, baserepo):
        """
        Generate a short repository name based on the contents of 'baserepo'.

        Args:
            baserepo: A URL to a git repository.

        Returns: A string containing the repository name.
        """
        repo_name = os.path.basename(baserepo)
        return os.path.splitext(repo_name)[0]

    def _get_multisubject(self):
        """
        Generate a subject line for the report based on test results.

        Returns: A string.
        """
        status = 'PASS'
        detail = 'Test report'
        krelease = ''

        if self.multireport_failed != MultiReportFailure.PASS:
            status = 'FAIL'

        if self.multireport_failed == MultiReportFailure.MERGE:
            detail = "Patch application failed"
        elif self.multireport_failed == MultiReportFailure.BUILD:
            detail = "Build failed"

        # Kernel release should be same for all kernels built
        if self.cfg.get("krelease"):
            repo_name = self._get_repo_name(self.cfg.get('baserepo'))
            krelease = " for kernel {} ({})".format(
                self.cfg.get("krelease"),
                repo_name
            )

        return "{}: {}{}".format(status, detail, krelease)


class StdioReporter(Reporter):
    """Generate test result output and print directly to the terminal."""
    # pylint: disable=too-few-public-methods
    TYPE = 'stdio'

    def report(self, printer=sys.stdout):
        """
        Print the email subject and text directly to a configurable output.

        Args:
            printer: What should be used to print the output to console
                    (default: stdout)
        """
        # We need to run the reporting function first to get the aggregated
        # data to build subject from
        report = self._get_multireport()
        printer.write("Subject: {}\n".format(self._get_multisubject()))
        printer.write(report)

        for (name, att) in self.attach:
            if name.endswith(('.log', '.txt')):
                printer.write("\n---------------\n{}\n".format(name))
                printer.write(att)


class MailReporter(Reporter):
    """Generate and send an email message with the results of the test."""
    # pylint: disable=too-few-public-methods
    TYPE = 'mail'

    def __init__(self, cfg):
        """Initialize an e-mail reporter."""
        # Get all of the required fields to send an email
        self.mailfrom = cfg['reporter']['mail_from']
        self.mailto = [to.strip() for to in cfg['reporter']['mail_to']]
        self.mailcc = [cc.strip() for cc in cfg['reporter']['mail_cc'] or []]
        self.mailbcc = [bcc.strip()
                        for bcc in cfg['reporter']['mail_bcc'] or []]
        self.headers = [headers.strip() for headers in
                        cfg['reporter']['mail_header']]
        self.subject_pfx = cfg['reporter']['mail_subject_pfx']
        self.subject = cfg['reporter']['mail_subject']
        self.smtp_url = cfg.get('smtp_url') or 'localhost'

        # Enable debugging for the SMTP server connection if skt was run with
        # verbose logging enabled.
        self.debug = False
        if cfg.get('verbose', 0) > 0:
            self.debug = True

        super(MailReporter, self).__init__(cfg)

    def report(self):
        """Generate and send the email report."""
        msg = MIMEMultipart()

        # Add the most basic parts of the email message
        msg['To'] = ', '.join(self.mailto)
        msg['Cc'] = ', '.join(self.mailcc)
        msg['From'] = self.mailfrom

        # Add any extra headers
        for header_line in self.headers:
            header, value = header_line.split(":", 1)
            msg[header] = value

        # We need to run the reporting function first to get aggregates to
        # build subject from
        msg.attach(MIMEText(self._get_multireport()))

        # Assign subject
        if self.subject:
            subject = self.subject
        else:
            subject = self._get_multisubject()
        if self.subject_pfx:
            subject = self.subject_pfx + subject
        msg['Subject'] = subject

        # Add the SKT job IDs so we can correlate emails to jobs
        msg['X-SKT-JIDS'] = ' '.join(self.multi_job_ids)

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

        mailserver = smtplib.SMTP(self.smtp_url)

        # Enable SMTP debugging if skt is running in verbose mode.
        mailserver.set_debuglevel(self.debug)

        mailserver.sendmail(self.mailfrom,
                            self.mailto + self.mailcc + self.mailbcc,
                            msg.as_string())
        mailserver.quit()

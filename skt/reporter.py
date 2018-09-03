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
try:
    import ConfigParser
except ImportError:
    import configparser as ConfigParser
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import smtplib
import sys

import requests

from skt.console import ConsoleLog, gzipdata
from skt.misc import join_with_slash, get_patch_name, get_patch_mbox
import skt.runner


MULTI_PASS = 0
MULTI_MERGE = 1
MULTI_BUILD = 2
MULTI_TEST = 3


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

    def __stateconfigdata(self, mergedata):
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
            'config': None
        }

        mergedata = self.__stateconfigdata(mergedata)

        if not self.cfg.get('mergelog'):
            if self.cfg.get("cfgurl"):
                response = requests.get(self.cfg.get("cfgurl"))
                if response:
                    mergedata['config'] = response.text
            else:
                with open(join_with_slash(self.cfg.get("workdir"),
                                          ".config"), "r") as fileh:
                    mergedata['config'] = fileh.read()

        self.mergedata = mergedata

    def __getmergeinfo(self):
        """
        Retrieve information about applied patches and base repository as a
        list of strings which should be then appended to the report.

        Returns: A list of strings representing data about applied patches and
                 base repository.
        """
        result = ['We cloned the git tree and checked out %s from the '
                  'repository at' % self.mergedata['base'][1][:12],
                  '  %s' % self.mergedata['base'][0]]

        if self.mergedata['merge_git']:
            result += ['\nWe merged the following references into the tree:']
            for repo, head in self.mergedata['merge_git']:
                result += ['  - %s' % repo,
                           '    into commit %s' % head[:12]]
        elif self.mergedata['localpatch'] or self.mergedata['patchwork']:
            result = ['We applied the following patch']
            if len(self.mergedata['localpatch']
                   + self.mergedata['patchwork']) > 1:
                result[0] += 'es:\n'
            else:
                result[0] += ':\n'

            for patchpath in self.mergedata['localpatch']:
                result += ['  - %s' % patchpath]

            for (purl, pname) in self.mergedata['patchwork']:
                result += ['  - %s,' % pname,
                           '    grabbed from %s\n' % purl]

            result += ['on top of commit %s from the repository at' %
                       self.mergedata['base'][1][:12],
                       '  %s' % self.mergedata['base'][0]]

        return result

    def __get_kernel_config(self, suffix=None):
        """
        Add the configuration which was used to build the kernel to reporter's
        list of attachments. Add optional suffix to attachment's name to
        distinguish it from other configs.

        Args:
            suffix: Optional suffix to add to attachment's name.

        Returns:
            A list of strings representing build configuration data.
        """
        cfgname = "config.gz" if not suffix else "config_{}.gz".format(suffix)

        self.attach.append((cfgname, gzipdata(self.mergedata["config"])))
        return ['\nThe kernel was built with the attached configuration '
                '(%s).' % cfgname]

    def __getmergefailure(self):
        result = ['\nHowever, the application of the last patch above '
                  'failed with the',
                  'following output:\n']

        with open(self.cfg.get("mergelog"), 'r') as fileh:
            for line in fileh:
                # Skip the useless part of the 'git am' output
                if ("The copy of the patch" in line) \
                        or ('see the failed patch' in line):
                    break
                result.append('    ' + line.strip())

        result += ['\nPlease note that if there are subsequent patches in the '
                   'series, they weren\'t',
                   'applied because of the error message stated above.\n']

        return result

    def __getbuildfailure(self, suffix=None):
        attname = "build.log.gz" if not suffix else "build_%s.log.gz" % suffix
        result = ['However, the build failed. We are attaching the build '
                  'output for',
                  'more information (%s).' % attname]

        with open(self.cfg.get("buildlog"), 'r') as fileh:
            self.attach.append((attname, gzipdata(fileh.read())))

        return result

    def __getjobresults(self):
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
        result = []

        runner = skt.runner.getrunner(*self.cfg.get("runner"))
        recipe_set_list = self.cfg.get('recipe_sets', [])

        for recipe_set_id in recipe_set_list:
            recipe_set_result = runner.getresultstree(recipe_set_id)
            for recipe in recipe_set_result.findall('recipe'):
                failed_tasks = []
                recipe_result = recipe.attrib.get('result')

                result += ['\n\n{}{} ({} arch): {}\n'.format(
                    'Test results for recipe R:',
                    recipe.attrib.get('id'),
                    recipe.find('hostRequires/and/arch').attrib.get('value'),
                    recipe_result.upper()
                )]

                kpkginstall_task = recipe.find(
                    "task[@name='/distribution/kpkginstall']"
                )
                if kpkginstall_task.attrib.get('result') != 'Pass':
                    result += ['Kernel failed to boot!\n']
                    failed_tasks.append('/distribution/kpkginstall')
                else:
                    recipe_tests = runner.get_recipe_test_list(recipe)
                    result += ['We ran the following tests:']
                    for test_name in recipe_tests:
                        test_result = recipe.find(
                            "task[@name='{}']".format(test_name)
                        ).attrib.get('result')
                        result += ['  - {}: {}'.format(test_name,
                                                       test_result.upper())]
                        if test_result != 'Pass':
                            failed_tasks.append(test_name)

                if failed_tasks:
                    result += [
                        '\nFor more information about the failures, here are '
                        'links for the logs of',
                        'failed tests and their subtasks:'
                    ]

                    console_node = recipe.find("logs/log[@name='console.log']")
                    if console_node is not None:
                        console_log = ConsoleLog(
                            self.cfg.get("krelease"),
                            console_node.attrib.get('href')
                        )
                        if console_log.data:
                            console_name = '{}_console.log.gz'.format(
                                recipe.attrib.get('id')
                            )
                            self.attach.append(
                                (console_name, console_log.getfulllog())
                            )
                            result += ['- console log ({}) is attached'.format(
                                console_name
                            )]
                for failed_task in failed_tasks:
                    task_node = recipe.find(
                        "task[@name='{}']".format(failed_task)
                    )
                    result += ['- {}'.format(failed_task)]
                    for log in task_node.findall('logs/log'):
                        result += ['  {}'.format(log.attrib.get('href'))]
                    for subtask_log in task_node.findall(
                            'results/result/logs/log'
                    ):
                        result += [
                            '  {}'.format(subtask_log.attrib.get('href'))
                        ]

                machinedesc_url = recipe.find(
                    "task[@name='/test/misc/machineinfo']/logs/"
                    "log[@name='machinedesc.log']"
                ).attrib.get('href')
                machinedesc = requests.get(machinedesc_url).text
                result += [
                    '',
                    'Testing was performed on a machine with following '
                    'parameters:',
                    '',
                    machinedesc,
                    ''
                ]

        if self.multireport and self.cfg.get('retcode') != '0' and \
                self.multireport_failed == MULTI_PASS:
            self.multireport_failed = MULTI_TEST

        return result

    def _getreport(self):
        msg = ['Hello,\n',
               'We appreciate your contributions to the Linux kernel and '
               'would like to help',
               'test them. Below are the results of automatic tests we ran']
        if self.mergedata['localpatch'] or self.mergedata['patchwork']:
            msg[-1] += ' on a patchset'
            msg += ['you\'re involved with, with hope it will help you find '
                    'possible issues sooner.']
        else:
            # There is no patchset the person was involved with
            msg[-1] += ', with hope it'
            msg += ['will help you find possible issues sooner.']

        msg += ['\n'] + self.__getmergeinfo()

        if self.cfg.get("mergelog"):
            msg += self.__getmergefailure()
        else:
            self.__get_kernel_config()
            if self.cfg.get("buildlog"):
                msg += self.__getbuildfailure()
            elif self.cfg.get('runner'):
                msg += self.__getjobresults()

        msg += ['\nPlease reply to this email if you find an issue with our '
                'testing process,',
                'or wish to not receive these reports anymore.',
                '\nSincerely,',
                '  Kernel CI Team']

        # Move configuration attachments to the end because some mail clients
        # (eg. mutt) inline them and they are huge
        # It's not safe to iterate over changing list so let's use a helper
        config_attachments = [attachment for attachment in self.attach
                              if 'config' in attachment[0]]
        self.attach = [
            attachment for attachment in self.attach
            if attachment not in config_attachments
        ] + config_attachments

        return '\n'.join(msg)

    def _get_multireport(self):
        intro = ['Hello,\n',
                 'We appreciate your contributions to the Linux kernel and '
                 'would like to help',
                 'test them. Below are the results of automatic tests we ran']
        results = []

        for idx, statefile in enumerate(self.statefiles):
            self.cfg = load_state_cfg(statefile)
            self._update_mergedata()

            if self.cfg.get("jobs"):
                for jobid in sorted(self.cfg.get("jobs")):
                    self.multi_job_ids.append(jobid)

            # The patches applied should be same for all runs but we need to
            # include the information only once
            if not idx:
                if self.mergedata['localpatch'] or self.mergedata['patchwork']:
                    intro[-1] += ' on a patchset'
                    intro += ['you\'re involved with, with hope it will help '
                              'you find possible issues sooner.']
                else:
                    # There is no patchset the person was involved with
                    intro[-1] += ', with hope it'
                    intro += ['will help you find possible issues sooner.']

                results += ['\n'] + self.__getmergeinfo() + ['']

                # We use the same tree for all runs so any merge failures are
                # same as well.
                if self.cfg.get('mergelog'):
                    self.multireport_failed = MULTI_MERGE
                    results += self.__getmergefailure()

            # Skip config/build/run retrieval if the merge failed.
            if not self.cfg.get('mergelog'):
                marker = self.cfg.get('kernel_arch', str(idx + 1))
                results += ['\n##### These are the results for %s' %
                            (marker + ' architecture'
                             if self.cfg.get('kernel_arch')
                             else 'test set %s' % marker)]

                results += self.__get_kernel_config(marker)

                if self.cfg.get('buildlog'):
                    if not self.multireport_failed:
                        self.multireport_failed = MULTI_BUILD
                    results += self.__getbuildfailure(marker)
                elif self.cfg.get('runner'):
                    results += self.__getjobresults()

            results.append('\n')

        results += ['Please reply to this email if you find an issue with our '
                    'testing process,',
                    'or wish to not receive these reports anymore.',
                    '\nSincerely,',
                    '  Kernel CI Team']

        # Move configuration attachments to the end because some mail clients
        # (eg. mutt) inline them and they are huge
        # It's not safe to iterate over changing list so let's use a helper
        config_attachments = [attachment for attachment in self.attach
                              if 'config' in attachment[0]]
        self.attach = [
            attachment for attachment in self.attach
            if attachment not in config_attachments
        ] + config_attachments

        return '\n'.join(intro + self.__get_multireport_summary() + results)

    def _getsubject(self):
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

    def _get_multisubject(self):
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

    def __get_multireport_summary(self):
        """
        Get a summary (pass / fail) of the multireport.

        Returns: A list of lines (strings) representing the summary.
        """
        summary = ['\nTEST SUMMARY:']

        if self.multireport_failed == MULTI_PASS:
            summary += ['  All builds and tests PASSED.']
        elif self.multireport_failed == MULTI_MERGE:
            summary += ['  Patch application FAILED!']
        elif self.multireport_failed == MULTI_BUILD:
            summary += ['  One or more builds FAILED!']
        elif self.multireport_failed == MULTI_TEST:
            summary += ['  Testing FAILED!']

        summary += ['\nMore detailed data follows.', '------------']
        return summary

    # TODO Define abstract "report" method.


class StdioReporter(Reporter):
    """A reporter sending results to stdout"""
    TYPE = 'stdio'

    def report(self, printer=sys.stdout):
        if self.multireport:
            # We need to run the reporting function first to get the aggregated
            # data to build subject from
            report = self._get_multireport()
            printer.write("{}\n".format(self._get_multisubject()))
            printer.write(report)
        else:
            self._update_mergedata()
            printer.write("Subject: {}\n".format(self._getsubject()))
            printer.write(self._getreport())

        for (name, att) in self.attach:
            if name.endswith(('.log', '.txt')):
                printer.write("\n---------------\n{}\n".format(name))
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
            msg.attach(MIMEText(self._get_multireport()))
            if not msg['Subject']:
                msg['Subject'] = self._get_multisubject()
            # Add the SKT job IDs so we can correlate emails to jobs
            msg['X-SKT-JIDS'] = ' '.join(self.multi_job_ids)
        else:
            self._update_mergedata()
            if not msg['Subject']:
                msg['Subject'] = self._getsubject()
            msg.attach(MIMEText(self._getreport()))
            # Add the SKT job IDs so we can correlate emails to jobs
            msg['X-SKT-JIDS'] = ' '.join(self._getjobids())

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
        mailserver.sendmail(self.mailfrom, self.mailto, msg.as_string())
        mailserver.quit()

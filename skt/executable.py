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

from __future__ import print_function
import ConfigParser
import argparse
import ast
import atexit
import datetime
import importlib
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile

import skt
import skt.console
import skt.publisher
import skt.reporter
import skt.runner
from skt.kernelbuilder import KernelBuilder, CommandTimeoutError, ParsingError
from skt.kerneltree import KernelTree, PatchApplicationError
from skt.misc import join_with_slash, SKT_SUCCESS, SKT_FAIL
from skt.state_file import get_state, update_state

DEFAULTRC = "~/.sktrc"
LOGGER = logging.getLogger()
retcode = SKT_SUCCESS


class AppendMergeArgument(argparse.Action):
    """Special type of argument/action that puts multiple parsed argument
    values into Namespace.'merge_queue', with the argument name saved."""
    # pylint: disable=too-few-public-methods
    def __call__(self, parser, namespace, values, option_string=None):
        if 'merge_queue' not in namespace:
            setattr(namespace, 'merge_queue', [])
        previous = namespace.merge_queue
        previous.append((self.dest, values))
        setattr(namespace, 'merge_queue', previous)


def full_path(path):
    """Get an absolute path to a file."""
    return os.path.abspath(os.path.expanduser(path))


def report_results(result_path, result_string, report_path, report_string):
    """
    Write report and results to the machine and human-readable forms.

    Args:
        result_path:    A full path of result log.
        result_string:  The content of string result.
        report_path:    A full path of report log.
        report_string:  The content of string report.
    """
    with os.fdopen(os.open(result_path, os.O_CREAT | os.O_WRONLY),
                   'w') as result_file:
        result_file.write(result_string)
    with os.fdopen(os.open(report_path, os.O_CREAT | os.O_WRONLY),
                   'w') as report_file:
        report_file.write(report_string)


def remove_oldresult(output_dir, prefix_filename):
    """
    Remove existing results from previous runs.

    Args:
        output_dir:      Source directory stores existing result.
        prefix_filename: Prefix of the existing result filename.
    """
    try:
        for filename in os.listdir(output_dir):
            if filename.startswith(prefix_filename):
                os.unlink(join_with_slash(output_dir, filename))
    except OSError:
        pass


def save_state(cfg, state):
    """
    Merge state to cfg, and then save cfg.

    Args:
        cfg:    A dictionary of skt configuration.
        state:  A dictionary of skt current state.
    """

    for (key, val) in state.iteritems():
        cfg[key] = val

    if not cfg.get('state'):
        return

    config = cfg.get('_parser')
    if not config.has_section("state"):
        config.add_section("state")

    for (key, val) in state.iteritems():
        if val is not None:
            logging.debug("state: %s -> %s", key, val)
            config.set('state', key, val)

    with open(cfg.get('rc'), 'w') as fileh:
        config.write(fileh)


def cmd_merge(args):
    """
    Fetch a kernel repository, checkout particular references, and optionally
    apply patches from patchwork instances.

    Args:
        args:    Command line arguments
    """
    global retcode
    # Counter merge patch following:
    # idx[0]: counter of merge-ref option.
    # idx[1]: counter of patch option.
    # idx[2]: counter of pw option.
    idx = [0, 0, 0]

    # Clone the kernel tree and check out the proper ref.
    ktree = KernelTree(
        args.get('baserepo'),
        ref=args.get('ref'),
        wdir=full_path(args.get('workdir')),
        fetch_depth=args.get('fetch_depth')
    )
    bhead = ktree.checkout()

    # Gather the subject and date of the commit that is currently checked out.
    bsubject = ktree.get_commit_subject(bhead)
    commitdate = ktree.get_commit_date(bhead)

    # Update the state file with what we know so far.
    state = {
        'baserepo': args.get('baserepo'),
        'basehead': bhead,
        'basesubject': bsubject,
        'commitdate': commitdate,
        'workdir': full_path(args.get('workdir')),
    }
    update_state(args['rc'], state)

    # Loop over what we have been asked to merge (if applicable).
    for thing_to_merge in args.get('merge_queue', []):
        try:
            if thing_to_merge[0] == 'merge_ref':
                mbranch_ref = thing_to_merge[1].split()

                # Update the state file with our merge_ref data.
                state = {
                    'mergerepo_%02d' % idx[0]: mbranch_ref[0],
                    'mergehead_%02d' % idx[0]: bhead
                }
                update_state(args['rc'], state)

                # Merge the git ref.
                (retcode, bhead) = ktree.merge_git_ref(*mbranch_ref)

                if retcode:
                    return

                # Increment the counter.
                idx[0] += 1

            else:
                # Attempt to merge a local patch.
                if thing_to_merge[0] == 'patch':
                    # Get the full path to the patch to merge.
                    patch = os.path.abspath(thing_to_merge[1])

                    # Update the state file with our local patch data.
                    state = {'localpatch_%02d' % idx[1]: patch}
                    update_state(args['rc'], state)

                    # Merge the patch.
                    ktree.merge_patch_file(patch)

                    # Increment the counter.
                    idx[1] += 1

                # Attempt to merge a patch from patchwork.
                elif thing_to_merge[0] == 'pw':
                    patch = thing_to_merge[1]

                    # Update the state file with our patchwork patch data.
                    state = {'patchwork_%02d' % idx[2]: patch}
                    update_state(args['rc'], state)

                    # Merge the patch. Retrieve the Patchwork session cookie
                    # first.
                    session_id = get_state(args['rc'],
                                           'patchwork_session_cookie')
                    ktree.merge_patchwork_patch(patch, session_id)

                    # Increment the counter.
                    idx[2] += 1

        # If the patch application failed, we should set the return code,
        # log an error, and update our state file.
        except PatchApplicationError as patch_exc:
            retcode = SKT_FAIL
            logging.error(patch_exc)

            # Update the state.
            state = {'mergelog': ktree.mergelog}
            update_state(args['rc'], state)

            return

        # If something else unexpected happened, re-raise the exception.
        except Exception:
            (exc, exc_type, trace) = sys.exc_info()
            raise exc, exc_type, trace

    # Get the SHA and subject of the repo after applying patches.
    buildhead = ktree.get_commit_hash()
    buildsubject = ktree.get_commit_subject()

    # Update the state file with the data about the current repo commit.
    state = {
        'buildhead': buildhead,
        'buildsubject': buildsubject
    }
    update_state(args['rc'], state)


def cmd_build(args):
    """
    Build the kernel with specified configuration and put it into a tarball.

    Args:
        args:    Command line arguments
    """
    global retcode
    tstamp = datetime.datetime.strftime(datetime.datetime.now(),
                                        "%Y%m%d%H%M%S")

    tgz = None
    builder = KernelBuilder(
        source_dir=args.get('workdir'),
        basecfg=args.get('baseconfig'),
        cfgtype=args.get('cfgtype'),
        extra_make_args=args.get('makeopts'),
        enable_debuginfo=args.get('enable_debuginfo'),
        rh_configs_glob=args.get('rh_configs_glob'),
        make_target=args.get('make_target'),
        localversion=args.get('localversion')
    )

    # Clean the kernel source with 'make mrproper' if requested.
    if args.get('wipe'):
        builder.clean_kernel_source()

    # Gather additional details about the build and save them to the state
    # file.
    kernel_arch = builder.build_arch
    make_opts = builder.assemble_make_options()
    state = {
        'kernel_arch': kernel_arch,
        'make_opts': ' '.join(make_opts)
    }
    update_state(args['rc'], state)

    # Write the cross compiler prefix to the state file only if the
    # environment variable is set:
    cross_compiler_prefix = builder.cross_compiler_prefix
    if cross_compiler_prefix:
        state = {'cross_compiler_prefix': cross_compiler_prefix}
        update_state(args['rc'], state)

    # Attempt to compile the kernel.
    try:
        package_path = builder.compile_kernel()
    # Handle a failure if the build times out, fails, or if the build
    # artifacts can't be found.
    except (CommandTimeoutError, subprocess.CalledProcessError, ParsingError,
            IOError) as exc:
        logging.error(exc)

        # Update the state file with the path to the build log.
        state = {'buildlog': builder.buildlog}
        update_state(args['rc'], state)

        # Set the return code.
        retcode = SKT_FAIL
    # Re-raise any unexpected exceptions.
    except Exception:
        (exc, exc_type, trace) = sys.exc_info()
        raise exc, exc_type, trace

    # Get the SHA of the commit from the repo that we just compiled.
    buildhead = get_state(args['rc'], 'buildhead')

    # Handle any built tarballs.
    if package_path and package_path.endswith('tar.gz'):
        if buildhead:
            # Replace the filename with the SHA of the last commit in the repo.
            ttgz = "{}.tar.gz".format(buildhead)
        else:
            # Add a timestamp to the path if we have no commit to reference.
            ttgz = addtstamp(tgz, tstamp)

        # Rename the kernel tarball.
        shutil.move(package_path, ttgz)
        logging.info("tarball path: %s", ttgz)

        # Save our tarball path to the state file.
        state = {'tarpkg': ttgz}
        update_state(args['rc'], state)

    # Handle any RPM repositories.
    if package_path and 'rpm_repo' in package_path:
        state = {'rpm_repo': package_path}
        update_state(args['rc'], state)

    # Set a filename for the kernel config file based on the SHA of the last
    # commit in the repo.
    tconfig = '{}.config'.format(buildhead)

    try:
        # Rename the config file and save its location to our state file.
        shutil.copyfile(builder.get_cfgpath(), tconfig)
        state = {'buildconf': tconfig}
        update_state(args['rc'], state)

        # Get the kernel version string.
        krelease = builder.getrelease()
        state = {'krelease': krelease}
        update_state(args['rc'], state)

    except IOError:  # Kernel config failed to build
        tconfig = ''
        logging.error('No config file to copy found!')


def cmd_publish(cfg):
    """
    Publish (copy) the kernel tarball and configuration to the specified
    location, generating their resulting URLs, using the specified "publisher".
    Only "cp", "scp" and "sftp" publishers are supported at the moment.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    publisher = skt.publisher.getpublisher(*cfg.get('publisher'))

    if cfg.get('buildconf'):
        cfgurl = publisher.publish(cfg.get('buildconf'))
        save_state(cfg, {'cfgurl': cfgurl})

    if cfg.get('tarpkg'):
        url = publisher.publish(cfg.get('tarpkg'))
        logging.info("published tarpkg url: %s", url)
        save_state(cfg, {'buildurl': url})
    else:
        logging.debug('No kernel tarball to publish found!')


def cmd_run(cfg):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    global retcode

    runner = skt.runner.getrunner(*cfg.get('runner'))

    atexit.register(runner.cleanup_handler)
    signal.signal(signal.SIGINT, runner.signal_handler)
    signal.signal(signal.SIGTERM, runner.signal_handler)
    retcode = runner.run(cfg.get('buildurl'),
                         cfg.get('max_aborted_count'),
                         cfg.get('krelease'),
                         cfg.get('wait'),
                         arch=cfg.get("kernel_arch"),
                         waiving=cfg.get('waiving'))

    recipe_set_index = 0
    for index, job in enumerate(runner.job_to_recipe_set_map.keys()):
        save_state(cfg, {'jobid_%s' % (index): job})
        for recipe_set in runner.job_to_recipe_set_map[job]:
            save_state(cfg,
                       {'recipesetid_%s' % (recipe_set_index): recipe_set})
            recipe_set_index += 1

    cfg['jobs'] = runner.job_to_recipe_set_map.keys()

    save_state(cfg, {'retcode': retcode})


def cmd_report(cfg):
    """
    Report build and/or test results using the specified "reporter". Currently
    results can be reported by e-mail or printed to stdout.

    Args:
        cfg:    A dictionary of skt configuration.
    """
    if not cfg.get("reporter"):
        return

    # Attempt to import the reporter provided by the user
    try:
        module = importlib.import_module('skt.reporter')
        class_name = "{}Reporter".format(cfg['reporter']['type'].title())
        reporter_class = getattr(module, class_name)
    except AttributeError:
        sys.exit(
            "Unable to find specified reporter type: {}".format(class_name)
        )

    # FIXME We are passing the entire cfg object to the reporter class but
    # we should be passing the specific options that are needed.
    # Create a report
    reporter = reporter_class(cfg)
    reporter.report()


def cmd_console_check(cfg):
    """
    Check the console logs for any traces.

    Args:
        cfg: A dictionary of skt configuration.
    """
    remove_oldresult(cfg.get('output_dir'), 'console_check.')
    console_result_path = join_with_slash(cfg.get('output_dir'),
                                          'console_check.result')
    console_report_path = join_with_slash(cfg.get('output_dir'),
                                          'console_check.report')

    if not cfg.get('krelease') or not cfg.get('console'):
        raise Exception('<krelease> or <console-url> parameter missing!')

    trace_list_list = []

    for console_path_or_url in cfg.get('console'):
        console_log = skt.console.ConsoleLog(cfg.get('krelease'),
                                             console_path_or_url)

        trace_list_list.append(console_log.gettraces())

    if any(trace_list_list):
        report_string = ''

        for trace_list in trace_list_list:
            if trace_list:
                report_string += '{}\n{}:\n\n{}\n\n'.format(
                    'This is the first trace we found in ',
                    # Get the path/URL that belongs to the found trace. The
                    # order of passed console logs and traces is the same, so
                    # we get the index of the trace and retrieve the log on the
                    # same position.
                    cfg.get('console')[trace_list_list.index(trace_list)],
                    trace_list[0]
                )

        report_results(console_result_path, 'false',
                       console_report_path, report_string)
    else:
        report_results(console_result_path, 'true',
                       console_report_path, 'No call traces were detected.')


def cmd_all(cfg):
    """
    Run the following commands in order: merge, build, publish, run, and
    report (if --wait option was specified).

    Args:
        cfg:    A dictionary of skt configuration.
    """
    cmd_merge(cfg)
    cmd_build(cfg)
    cmd_publish(cfg)
    cmd_run(cfg)
    if cfg.get('wait'):
        cmd_report(cfg)


def addtstamp(path, tstamp):
    """
    Add time stamp to a file path.

    Args:
        path:   file path.
        tstamp: time stamp.

    Returns:
        New path with time stamp.
    """
    return join_with_slash(os.path.dirname(path),
                           "%s-%s" % (tstamp, os.path.basename(path)))


def setup_logging(verbose):
    """
    Setup the root logger.

    Args:
        verbose:    Verbosity level to setup log message filtering.
    """
    logging.basicConfig(format="%(asctime)s %(levelname)8s   %(message)s")
    LOGGER.setLevel(logging.WARNING - (verbose * 10))
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


def setup_parser():
    """
    Create an skt command line parser.

    Returns:
        The created parser.
    """
    parser = argparse.ArgumentParser()

    # These arguments apply to all commands within skt
    parser.add_argument(
        "-d",
        "--workdir",
        type=str,
        help="Path to work dir"
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=str,
        help="Path to output directory"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        help="Increase verbosity level",
        action="count",
        default=0
    )
    parser.add_argument(
        "--rc",
        help="Path to rc file",
        default=DEFAULTRC
    )
    # FIXME Storing state in config file can break the whole system in case
    #       state saving aborts. It's better to save state separately.
    #       It also breaks separation of concerns, as in principle skt doesn't
    #       need to modify its own configuration otherwise.
    parser.add_argument(
        "--state",
        help=(
            "Save/read state from 'state' section of rc file"
        ),
        action="store_true",
        default=False
    )
    parser.add_argument(
        "--waiving",
        help=(
            "Hide waived tests."
        ),
        type=lambda x: (str(x).lower() == 'true'),
        default=True
    )

    subparsers = parser.add_subparsers()

    # These arguments apply to the 'merge' skt subcommand
    parser_merge = subparsers.add_parser("merge", add_help=False)
    parser_merge.add_argument(
        "-b",
        "--baserepo",
        type=str,
        help="Base repo URL"
    )
    parser_merge.add_argument(
        "--ref",
        type=str,
        help="Base repo ref to which patches are applied (default: master)"
    )
    parser_merge.add_argument(
        "--patch",
        type=str,
        action=AppendMergeArgument,
        help="Path to a local patch to apply "
        + "(use multiple times for multiple patches)"
    )
    parser_merge.add_argument(
        "--pw",
        type=str,
        action=AppendMergeArgument,
        help="URL to Patchwork patch to apply "
        + "(use multiple times for multiple patches)"
    )
    parser_merge.add_argument(
        "-m",
        "--merge-ref",
        action=AppendMergeArgument,
        help="Merge ref format: 'url [ref]' "
        + "(use multiple times for multiple merge refs)"
    )
    parser_merge.add_argument(
        "--fetch-depth",
        type=str,
        help=(
            "Create a shallow clone with a history truncated to the "
            "specified number of commits."
        ),
        default=None
    )

    # These arguments apply to the 'build' skt command
    parser_build = subparsers.add_parser("build", add_help=False)
    parser_build.add_argument(
        "-c",
        "--baseconfig",
        type=str,
        help="Path to kernel config to use"
    )
    parser_build.add_argument(
        "--cfgtype",
        type=str,
        help="How to process default config (default: olddefconfig)"
    )
    parser_build.add_argument(
        "-w",
        "--wipe",
        action="store_true",
        default=False,
        help="Clean build (make mrproper before building)"
    )
    parser_build.add_argument(
        "--enable-debuginfo",
        type=bool,
        default=False,
        help="Build kernel with debuginfo (default: disabled)"
    )
    parser_build.add_argument(
        "--makeopts",
        type=str,
        help="Additional options to pass to make"
    )
    parser_build.add_argument(
        "--make-target",
        dest="make_target",
        type=str,
        default='targz-pkg',
        choices=('targz-pkg', 'binrpm-pkg'),
        help="Make target from kernel Makefile"
    )
    parser_build.add_argument(
        "--rh-configs-glob",
        type=str,
        help=(
            "Glob pattern to use when choosing the correct kernel config "
            "(required if '--cfgtype rh-configs' is used)"
        )
    )
    parser_build.add_argument(
        "--localversion",
        type=str,
        default="skt",
        help=("String to append to kernel version number (LOCALVERSION)")
    )

    # These arguments apply to the 'publish' skt command
    parser_publish = subparsers.add_parser("publish", add_help=False)
    parser_publish.add_argument(
        "-p",
        "--publisher",
        type=str,
        nargs=3,
        help="Publisher config string in 'type destination baseurl' format"
    )
    parser_publish.add_argument(
        "--tarpkg",
        type=str,
        help="Path to tar pkg to publish"
    )

    # These arguments apply to the 'run' skt command
    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument(
        "-r",
        "--runner",
        nargs=2,
        type=str,
        help="Runner config in 'type \"{'key' : 'val', ...}\"' format"
    )
    parser_run.add_argument(
        "--buildurl",
        type=str,
        help="Build tarpkg url"
    )
    parser_run.add_argument(
        "--krelease",
        type=str,
        help="Kernel release version of the build"
    )
    parser_run.add_argument(
        '--max-aborted-count',
        type=int,
        help='Ignore <count> aborted jobs to work around temporary '
        + 'infrastructure issues. Defaults to 3.'
    )
    parser_run.add_argument(
        "--wait",
        action="store_true",
        default=False,
        help="Do not exit until tests are finished"
    )

    # These arguments apply to the 'report' skt subcommand
    parser_report = subparsers.add_parser("report", add_help=False)
    parser_report.add_argument(
        "--reporter",
        dest="type",
        type=str,
        choices=('stdio', 'mail'),
        help="Reporter to use"
    )
    parser_report.add_argument(
        "--mail-to",
        action='append',
        type=str,
        help="Report recipient's email address"
    )
    parser_report.add_argument(
        "--mail-cc",
        action='append',
        type=str,
        help="Report copy recipient's email address"
    )
    parser_report.add_argument(
        "--mail-bcc",
        action='append',
        type=str,
        help="Report hidden copy recipient's email address"
    )
    parser_report.add_argument(
        "--mail-from",
        type=str,
        help="Report sender's email address"
    )
    parser_report.add_argument(
        "--mail-subject-pfx",
        type=str,
        help="Prefix to add to the subject of the report email"
    )
    parser_report.add_argument(
        "--mail-subject",
        type=str,
        help="Subject of the report email"
    )
    parser_report.add_argument(
        "--mail-header",
        action='append',
        default=[],
        type=str,
        help=(
            "Extra headers for the report email - example: "
            "\"In-Reply-To: <messageid@example.com>\""
        )
    )
    parser_report.add_argument(
        '--result',
        action='append',
        type=str,
        help='Path to a state file to include in the report'
    )
    parser_report.add_argument(
        "--smtp-url",
        type=str,
        help='Use smtp url instead of localhost to send mail',
    )
    parser_report.add_argument(
        "--template",
        dest="template",
        type=str,
        default='full',
        choices=('full', 'limited'),
        help="Template to use for reports"
    )
    parser_report.set_defaults(func=cmd_report)
    parser_report.set_defaults(_name="report")

    parser_console = subparsers.add_parser('console-check', add_help=False)
    parser_console.add_argument(
        '--krelease',
        type=str,
        help='Release version string of the kernel to search for'
    )
    parser_console.add_argument(
        '--console',
        type=str,
        action='append',
        default=[],
        help='URL or path to console log to parse, local file may be gzipped. '
        + 'Can be specified multiple times to parse more logs with the '
        + 'same krelease.'
    )

    parser_all = subparsers.add_parser(
        "all",
        parents=[
            parser_merge,
            parser_build,
            parser_publish,
            parser_run,
            parser_report
        ]
    )

    parser_merge.add_argument(
        "-h",
        "--help",
        help="Merge sub-command help",
        action="help"
    )
    parser_build.add_argument(
        "-h",
        "--help",
        help="Build sub-command help",
        action="help"
    )
    parser_publish.add_argument(
        "-h",
        "--help",
        action="help",
        help="Publish sub-command help"
    )
    parser_run.add_argument(
        "-h",
        "--help",
        help="Run sub-command help",
        action="help"
    )
    parser_report.add_argument(
        "-h",
        "--help",
        help="Report sub-command help",
        action="help"
    )
    parser_console.add_argument(
        '-h',
        '--help',
        help='Console sub-command help',
        action='help'
    )

    parser_merge.set_defaults(func=cmd_merge)
    parser_merge.set_defaults(_name="merge")
    parser_build.set_defaults(func=cmd_build)
    parser_build.set_defaults(_name="build")
    parser_publish.set_defaults(func=cmd_publish)
    parser_publish.set_defaults(_name="publish")
    parser_run.set_defaults(func=cmd_run)
    parser_run.set_defaults(_name="run")
    parser_console.set_defaults(func=cmd_console_check)
    parser_console.set_defaults(_name='console_check')
    parser_all.set_defaults(func=cmd_all)
    parser_all.set_defaults(_name="all")

    return parser


def load_config(args):
    """
    Load skt configuration from the command line and the configuration file.

    Args:
        args:   Parsed command-line configuration, including the path to the
                configuration file.

    Returns:
        Loaded configuration dictionary.
    """
    # NOTE(mhayden): The shell should do any tilde expansions on the path
    # before the rc path is provided to Python.
    config_parser = ConfigParser.ConfigParser()
    config_parser.read(os.path.abspath(args.rc))

    cfg = vars(args)
    cfg['_parser'] = config_parser
    cfg['_testcases'] = []

    # Read 'state' section first so that it is not overwritten by 'config'
    # section values.
    if cfg.get('state') and config_parser.has_section('state'):
        for (name, value) in config_parser.items('state'):
            if not cfg.get(name):
                if name.startswith("jobid_"):
                    cfg.setdefault("jobs", set()).add(value)
                if name.startswith('recipesetid_'):
                    cfg.setdefault("recipe_sets", set()).add(value)
                elif name.startswith("mergerepo_"):
                    cfg.setdefault("mergerepos", list()).append(value)
                elif name.startswith("mergehead_"):
                    cfg.setdefault("mergeheads", list()).append(value)
                elif name.startswith("localpatch_"):
                    cfg.setdefault("localpatches", list()).append(value)
                elif name.startswith("patchwork_"):
                    cfg.setdefault("patchworks", list()).append(value)
                cfg[name] = value

    if config_parser.has_section('config'):
        for (name, value) in config_parser.items('config'):
            if not cfg.get(name):
                cfg[name] = value

    if config_parser.has_section('publisher') and not cfg.get('publisher'):
        cfg['publisher'] = [config_parser.get('publisher', 'type'),
                            config_parser.get('publisher', 'destination'),
                            config_parser.get('publisher', 'baseurl')]

    if config_parser.has_section('runner') and not cfg.get('runner'):
        rcfg = {}
        for (key, val) in config_parser.items('runner'):
            if key != 'type':
                rcfg[key] = val
        cfg['runner'] = [config_parser.get('runner', 'type'), rcfg]
    elif cfg.get('runner'):
        cfg['runner'] = [cfg.get('runner')[0],
                         ast.literal_eval(cfg.get('runner')[1])]

    # Check if the reporter type is set on the command line
    if cfg.get('_name') == 'report' and cfg.get('type'):
        # Use the reporter configuration from the command line
        cfg['reporter'] = {
            'type': cfg.get('type'),
            'mail_to': cfg.get('mail_to'),
            'mail_cc': cfg.get('mail_cc'),
            'mail_bcc': cfg.get('mail_bcc'),
            'mail_from': cfg.get('mail_from'),
            'mail_subject_pfx': cfg.get('mail_subject_pfx'),
            'mail_subject': cfg.get('mail_subject'),
            'mail_header': cfg.get('mail_header')
        }
    elif config_parser.has_section('reporter'):
        # Use the reporter configuration from the configuration file
        cfg['reporter'] = config_parser.items('reporter')

    # Get an absolute path for the work directory
    if cfg.get('workdir'):
        cfg['workdir'] = full_path(cfg.get('workdir'))
    else:
        cfg['workdir'] = tempfile.mkdtemp()

    # Get an absolute path for the kernel configuration file
    if cfg.get('basecfg'):
        cfg['basecfg'] = full_path(cfg.get('basecfg'))

    # Get an absolute path for the configuration file
    cfg['rc'] = full_path(cfg.get('rc'))

    # Get an absolute path for the buildconf
    if cfg.get('buildconf'):
        cfg['buildconf'] = full_path(cfg.get('buildconf'))

    # Get an absolute path for the tarpkg
    if cfg.get('tarpkg'):
        cfg['tarpkg'] = full_path(cfg.get('tarpkg'))

    # Get absolute paths to state files for multireport
    # Handle "result" being None if none are specified
    for idx, statefile_path in enumerate(cfg.get('result') or []):
        cfg['result'][idx] = full_path(statefile_path)

    # Assign default max aborted count if it's not defined in config file
    if not cfg.get('max_aborted_count'):
        cfg['max_aborted_count'] = 3

    # Get absolute path to blacklist file
    if cfg.get('runner') and cfg['runner'][0] == 'beaker' and \
            'blacklist' in cfg['runner'][1]:
        cfg['runner'][1]['blacklist'] = full_path(
            cfg['runner'][1]['blacklist']
        )

    # Create and get an absolute path for the output directory
    if cfg.get('output_dir'):
        cfg['output_dir'] = full_path(cfg.get('output_dir'))
        try:
            os.mkdir(cfg.get('output_dir'))
        except OSError:
            pass
    elif os.access(cfg.get('workdir'), os.W_OK | os.X_OK):
        cfg['output_dir'] = cfg.get('workdir')
    else:
        cfg['output_dir'] = os.getcwd()

    return cfg


def check_args(parser, args):
    # pylint: disable=protected-access
    """Check the arguments provided to ensure all requirements are met.

    Args:
      parser - the parser object
      args   - the parsed arguments
    """
    # Users must specify a glob to match generated kernel config files when
    # building configs with `make rh-configs`
    if (args._name == 'build' and args.cfgtype == 'rh-configs'
            and not args.rh_configs_glob):
        parser.error("--cfgtype rh-configs requires --rh-configs-glob to set")

    # Check required arguments for 'report'
    if args._name == 'report':

        # MailReporter requires recipient and sender email addresses
        if (args.type == 'mail' and (not args.mail_to or not args.mail_from)):
            parser.error(
                "--reporter mail requires --mail-to and --mail-from to be set"
            )

        # No mail-related options should be provided for StdioReporter
        if (args.type == 'stdio'
                and any([getattr(args, x)
                         for x in dir(args) if x.startswith('mail')])):
            parser.error(
                'the stdio reporter was selected but arguments for the mail '
                'reporter were provided'
            )


def main():
    """This is the main entry point used by setup.cfg."""
    # pylint: disable=protected-access
    try:
        global retcode

        parser = setup_parser()
        args = parser.parse_args()
        check_args(parser, args)

        setup_logging(args.verbose)

        # We are gradually migrating away from messing with cfg and passing
        # it everywhere.
        var_args = vars(args)
        if var_args['_name'] in ['merge', 'build']:
            args.func(var_args)
        else:
            cfg = load_config(args)
            args.func(cfg)

        sys.exit(retcode)
    except KeyboardInterrupt:
        print("\nExited at user request.")
        sys.exit(130)

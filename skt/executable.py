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
"""SKT entry point and argument parsing."""
import argparse
import atexit
import itertools
import logging
import os
import signal
import sys
import tempfile

from datadefinition.rc_data import SKTData

from skt.misc import SKT_ERROR
from skt.runner import BeakerRunner

LOGGER = logging.getLogger()


def full_path(path):
    """Get an absolute path to a file."""
    return os.path.abspath(os.path.expanduser(path))


def cmd_run(skt_data):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        skt_data: SKTData, parsed rc config file overriden with cmd-line args
    """
    jobtemplate = skt_data.runner.jobtemplate
    jobowner = skt_data.runner.jobowner
    blacklist = skt_data.runner.blacklist
    runner = BeakerRunner(jobtemplate, jobowner, blacklist)
    try:
        cmd_run.cleanup_done
    except AttributeError:
        cmd_run.cleanup_done = False

    def cleanup_handler():
        """ Save SKT job state (jobs, recipesets, retcode) to rc-file and mark
            in runner that cleanup_handler() ran. Jobs are not cancelled, see
            ticket #1140.

            Returns:
                 None
        """
        # Don't run cleanup handler twice by accident.
        # NOTE: Also, the code to cancel any jobs was removed on purpose.
        if cmd_run.cleanup_done:
            return

        skt_data.state.jobs = ' '.join(runner.job_to_recipe_set_map.keys())
        skt_data.state.recipesets = ' '.join(
            list(itertools.chain.from_iterable(
                runner.job_to_recipe_set_map.values()
            ))
        )

        skt_data.state.retcode = runner.retcode
        with open(skt_data.state.rc, 'w') as fhandle:
            fhandle.write(skt_data.serialize())

        # NOTE: Don't cancel jobs. Per ticket #1140, Beaker jobs must continue
        # to run when a timeout is reached and skt is killed in the GitLab
        # pipeline.
        cmd_run.cleanup_done = True

    def signal_handler(sig, frame):
        # pylint: disable=unused-argument
        """
        Handle SIGTERM|SIGINT: call cleanup_handler() and exit.
        """
        cleanup_handler()

        sys.exit(SKT_ERROR)

    atexit.register(cleanup_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    return runner.run(skt_data.state.kernel_package_url,
                      skt_data.state.max_aborted_count,
                      skt_data.state.kernel_version,
                      skt_data.state.wait,
                      arch=skt_data.state.kernel_arch,
                      waiving=skt_data.state.waiving)


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
    PLEASE SET DEFAULTS IN post_fixture() only.

    Returns:
        The created parser.
    """
    parser = argparse.ArgumentParser()

    # These arguments apply to all commands within skt
    parser.add_argument("-d", "--workdir", type=str, help="Path to work dir")
    parser.add_argument("-v", "--verbose", help="Increase verbosity level",
                        action="count", default=0)
    parser.add_argument("--rc", help="Path to rc file", required=True)
    parser.add_argument("--waiving", help=("Hide waived tests."),
                        type=lambda x: (str(x).lower() == 'true'),
                        default=True)

    parser.add_argument(
        "--state",
        help=(
            "Save/read state from 'state' section of rc file"
        ),
        action="store_true",
        default=False
    )

    subparsers = parser.add_subparsers()

    # These arguments apply to the 'run' skt command
    parser_run = subparsers.add_parser("run", add_help=False)
    parser_run.add_argument('--max-aborted-count', type=int,
                            help='Ignore <count> aborted jobs to work around '
                                 'temporary infrastructure issues. Defaults '
                                 'to 3.')
    parser_run.add_argument("--wait", action="store_true",
                            help="Do not exit until tests are finished")

    parser_run.add_argument("-h", "--help", help="Run sub-command help",
                            action="help")

    return parser


def post_fixture(skt_data):
    """ Modifies skt configuration to set defaults or modify params."""
    # Get an absolute path for the work directory
    if skt_data.state.workdir:
        skt_data.state.workdir = full_path(skt_data.state.workdir)
    else:
        skt_data.state.workdir = tempfile.mkdtemp()

    # Assign default --wait value if not specified
    if not skt_data.state.wait:
        skt_data.state.wait = True

    # Assign default max aborted count if it's not defined in config file
    if not skt_data.state.max_aborted_count:
        skt_data.state.max_aborted_count = 3

    # Get absolute path to blacklist file
    if skt_data.runner.blacklist:
        skt_data.runner.blacklist = full_path(skt_data.runner.blacklist)

    return skt_data


def override_config_with_cmdline(args, skt_data):
    """Override skt config data with cmd-line args.
    Args:
        args: argparse.Namespace, cmd-line args
        skt_data: SKTData, parsed config file setiings
    Returns:
        updated skt_data with overriden values
    """

    for key, value in vars(args).items():
        setattr(skt_data.state, key, value)

    return skt_data


def load_skt_config_data(args):
    """Load settings from config file and override with cmd-line args.
    Args:
        args: argparse.Namespace, parsed cmd-line args
    Returns:
        skt_data: SKTData, parsed rc config file
    """
    # make sure path to rc-file is absolute
    args.rc = full_path(args.rc)

    with open(args.rc) as fhandle:
        skt_data = SKTData.deserialize(fhandle.read())  # type: SKTData

    # override config file values with cmd-line args
    skt_data = override_config_with_cmdline(args, skt_data)

    return skt_data


def main():
    """This is the main entry point used by setup.cfg."""
    # pylint: disable=protected-access
    try:
        parser = setup_parser()
        args = parser.parse_args()
        skt_data = load_skt_config_data(args)

        setup_logging(skt_data.state.verbose)

        skt_data = post_fixture(skt_data)

        retcode = cmd_run(skt_data)

        sys.exit(retcode)
    except KeyboardInterrupt:
        print("\nExited at user request.")
        sys.exit(130)

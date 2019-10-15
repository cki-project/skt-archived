# Copyright (c) 2017-2019 Red Hat, Inc. All rights reserved. This copyrighted
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

import configparser
import atexit
import logging
import os
import signal
import sys
import tempfile

import argparse
from skt.runner import BeakerRunner
from skt.misc import SKT_ERROR

LOGGER = logging.getLogger()


def full_path(path):
    """Get an absolute path to a file."""
    return os.path.abspath(os.path.expanduser(path))


def save_state(config_set, state):
    """
    Merge state to config_set, and then save config_set.

    Args:
        config_set: A dictionary of skt configuration.
        state:      A dictionary of skt current state.
    """

    def set_field(key, val, dst):
        if key in ['type', 'jobtemplate', 'jobowner', 'blacklist']:
            dst['runner'][key] = val
        else:
            dst['state'][key] = val

    config_dict = {'state': {}, 'runner': {}}

    for key, val in config_set.items():
        set_field(key, val, config_dict)

    for key, val in state.items():
        # print info about change of existing keys or addition of new
        if key not in config_set.keys() or config_set[key] != val:
            logging.debug("state: %s -> %s", key, val)

        set_field(key, val, config_dict)
        config_set[key] = val

    # create parser to safely read dict and output config file
    temp_parser = configparser.RawConfigParser()
    temp_parser.read_dict(config_dict)

    # write parser content to the rc-file
    with open(config_set.get('rc'), 'w') as fileh:
        temp_parser.write(fileh)


def cmd_run(config_set):
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        config_set:    A dictionary of skt configuration.
    """
    jobtemplate = config_set.get('jobtemplate')
    jobowner = config_set.get('jobowner')
    blacklist = config_set.get('blacklist')
    runner = BeakerRunner(jobtemplate, jobowner, blacklist)
    try:
        cmd_run.cleanup_done
    except AttributeError:
        cmd_run.cleanup_done = False

    def cleanup_handler():
        """ Save SKT job state (recipesetid_?, max_recipe_set_index, jobs,
            retcode) to rc-file and mark in runner that cleanup_handler() ran.
            Jobs are not cancelled, see ticket #1140.

            Returns:
                 None
        """
        # Don't run cleanup handler twice by accident.
        # NOTE: Also, the code to cancel any jobs was removed on purpose.
        if cmd_run.cleanup_done:
            return

        recipe_set_index = 0
        recipe_set_list = []
        for index, job in enumerate(runner.job_to_recipe_set_map.keys()):
            for recipe_set in runner.job_to_recipe_set_map[job]:
                recipe_set_list.append(recipe_set)
                save_state(config_set,
                           {'recipesetid_%s' % (recipe_set_index): recipe_set})
                recipe_set_index += 1

        config_set['jobs'] = ' '.join(runner.job_to_recipe_set_map.keys())
        config_set['recipesets'] = ' '.join(recipe_set_list)
        # save maximum indexes we've used to simplify statefile merging
        config_set['max_recipe_set_index'] = recipe_set_index

        save_state(config_set, {'retcode': runner.retcode})

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
    return runner.run(config_set.get('kernel_package_url'),
                      config_set.get('max_aborted_count'),
                      config_set.get('kernel_version'),
                      config_set.get('wait'),
                      arch=config_set.get("kernel_arch"),
                      waiving=config_set.get('waiving'))


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

    parser_run.set_defaults(func=cmd_run)
    parser_run.set_defaults(_name="run")

    return parser


def post_fixture(config_set):
    """ Modifies skt configuration to set defaults or modify params."""
    # Get an absolute path for the work directory
    if config_set.get('workdir'):
        config_set['workdir'] = full_path(config_set.get('workdir'))
    else:
        config_set['workdir'] = tempfile.mkdtemp()

    # Assign default --wait value if not specified
    if not config_set.get('wait'):
        config_set['wait'] = True

    # Assign default max aborted count if it's not defined in config file
    if not config_set.get('max_aborted_count'):
        config_set['max_aborted_count'] = 3

    # Get absolute path to blacklist file
    if config_set.get('blacklist'):
        config_set['blacklist'] = full_path(config_set['blacklist'])

    return config_set


def load_config(args):
    """
    Load skt configuration from the command line and the configuration file.

    Args:
        args:   Parsed command-line configuration, including the path to the
                configuration file.

    Returns:
        Loaded configuration dictionary.
    """
    # NOTE: The shell should do any tilde expansions on the path
    # before the rc path is provided to Python.

    # create output object; config file settings overriden by cmd-line params
    config_set = vars(args)

    # read input file (rc-file) using config file parser
    config_parser = configparser.RawConfigParser()
    config_parser.read(args.rc)

    # if there are unset values in command-line args, use config values
    seen_config_keys = set()
    for section in config_parser.sections():
        for key, value in config_parser.items(section):
            if key in seen_config_keys:
                # we don't allow non-globally-unique keys
                raise RuntimeError('input config file is not flat!')

            if config_set.get(key):
                # cmd-line args override config, not vice-versa
                continue

            # write a new value
            config_set[key] = value

            # add seen key
            seen_config_keys.add(key)

    return config_set


def main():
    """This is the main entry point used by setup.cfg."""
    # pylint: disable=protected-access
    try:
        parser = setup_parser()
        args = parser.parse_args()
        # make sure path to rc-file is absolute
        args.rc = full_path(args.rc)

        setup_logging(args.verbose)

        config_set = load_config(args)
        config_set = post_fixture(config_set)

        retcode = args.func(config_set)

        sys.exit(retcode)
    except KeyboardInterrupt:
        print("\nExited at user request.")
        sys.exit(130)

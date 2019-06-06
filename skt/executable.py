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

import argparse
import atexit
import logging
import os
import signal
import sys
import tempfile

from skt.config import ConfigFile
from skt.config import ConfigSet
from skt.runner import BeakerRunner

LOGGER = logging.getLogger()


class ConfigFileFixture(ConfigFile):
    """Merges cmd-line arguments and config file contents, runs fixture."""

    def fixture(self):
        """Modifies skt cmd-line configuration post-parse, post config-load.

        Attributes:
            config_set - parsed cmd-line options
        """
        # Get an absolute path for the work directory
        cfg = self.config_set

        if cfg.workdir:
            cfg.workdir = full_path(cfg.workdir)
        else:
            cfg.workdir = tempfile.mkdtemp()

        # Get an absolute path for the configuration file
        cfg.rc = full_path(cfg.rc)

        # Get absolute path to Beaker blacklist file
        if cfg.blacklist and cfg.type == 'beaker':
            cfg.blacklist = full_path(cfg.blacklist)

        # Create and get an absolute path for the output directory
        if cfg.output_dir:
            cfg.output_dir = full_path(cfg.output_dir)
            try:
                os.mkdir(cfg.output_dir)
            except OSError:
                pass
        elif os.access(cfg.workdir, os.W_OK | os.X_OK):
            cfg.output_dir = cfg.workdir
        else:
            cfg.output_dir = os.getcwd()


def full_path(path):
    """Get an absolute path to a file."""
    return os.path.abspath(os.path.expanduser(path))


def cmd_run(config_set):
    # type: (ConfigSet) -> int
    """
    Run tests on a built kernel using the specified "runner". Only "Beaker"
    runner is currently supported.

    Args:
        config_set:    A dictionary of skt configuration.
    """
    runner = BeakerRunner(config_set.jobtemplate, config_set.jobowner,
                          config_set.blacklist)

    atexit.register(runner.cleanup_handler)
    signal.signal(signal.SIGINT, runner.signal_handler)
    signal.signal(signal.SIGTERM, runner.signal_handler)
    retcode = runner.run(config_set.kernel_package_url,
                         config_set.max_aborted_count,
                         config_set.kernel_version,
                         config_set.wait,
                         arch=config_set.kernel_arch,
                         waiving=config_set.waiving)

    recipe_set_index = 0
    for index, job in enumerate(runner.job_to_recipe_set_map.keys()):
        config_set.save_state({f'jobid_{index}': job})
        for recipe_set in runner.job_to_recipe_set_map[job]:
            config_set.save_state(
                {f'recipesetid_{recipe_set_index}': recipe_set})
            recipe_set_index += 1

    config_set.jobs = runner.job_to_recipe_set_map.keys()

    config_set.save_state({'retcode': retcode})

    return retcode


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


def parse_cmdline():
    """
    Parse skt command line parser.

    Returns:
        ConfigSet object with parsed args, function to run
    """
    parser = argparse.ArgumentParser()
    # config_set will be returned with parsed options in sections for config
    config_set = ConfigSet()

    # These arguments apply to all commands within skt, [state] section of
    # skt config file.
    section = 'state'
    config_set.add_argument(parser, section, "-d", "--workdir", type=str,
                            help="Path to work dir")
    config_set.add_argument(parser, section, "-o", "--output-dir",
                            type=str, help="Path to output directory")
    config_set.add_argument(parser, section, "-v", "--verbose",
                            help="Increase verbosity level", action="count",
                            default=0)
    config_set.add_argument(parser, section, "--rc", help="Path to rc file",
                            required=True)
    config_set.add_argument(parser, section, "--waiving",
                            help=("Hide waived tests."),
                            type=lambda x: (str(x).lower() == 'true'),
                            default=True)

    subparsers = parser.add_subparsers()

    # These arguments apply to the 'run' skt command, [runner] section of
    # skt config file.
    section = 'runner'
    parser_run = subparsers.add_parser("run", add_help=False)
    config_set.add_argument(parser_run, section, '--max-aborted-count',
                            type=int, default=3,
                            help='Ignore <count> aborted jobs to work around'
                                 ' temporary infrastructure issues. Defaults'
                                 ' to 3.')
    config_set.add_argument(parser_run, section, "--wait", action="store_true",
                            default=False,
                            help="Do not exit until tests are finished")
    # TODO: unused - remove this
    parser.add_argument("--state", help='unused', action='store_true')
    # add --help manually
    parser_run.add_argument("-h", "--help", help="Run sub-command help",
                            action="help")

    parser_run.set_defaults(func=cmd_run)
    parser_run.set_defaults(_name="run")

    # parse arguments
    args = parser.parse_args()

    # save parsed arguments to sections
    config_set.load_args(args)

    return config_set, args.func


def main():
    """This is the main entry point used by setup.cfg."""
    # pylint: disable=protected-access
    try:
        config_set, func = parse_cmdline()

        setup_logging(config_set.verbose)

        # create config file wrapper and run its fixture
        cfgfile = ConfigFileFixture(config_set, config_set.rc)

        # pass fixture-d config_set
        retcode = func(cfgfile.config_set)

        sys.exit(retcode)
    except KeyboardInterrupt:
        print("\nExited at user request.")
        sys.exit(130)


if __name__ == '__main__':
    main()

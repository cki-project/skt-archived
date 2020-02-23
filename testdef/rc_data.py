#!/usr/bin/env python3
# Copyright (c) 2018 - 2020 Red Hat, Inc. All rights reserved. This copyrighted
# material is made available to anyone wishing to use, modify, copy, or
# redistribute it subject to the terms and conditions of the GNU General Public
# License v.2 or later.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
"""SKT and pipeline rc_data serialization."""

from cki_lib.misc import init_logger
from cki_lib.misc import parse_config_data
from testdef import const

_NO_DEFAULT = object()

ERR_LOGGER = init_logger(__name__, dst_file='rc_data.log')


class DefinitionBase:
    """Base class to simplify dataclass serialization (config, yaml, dict)."""

    def dicts_to_classes(self):
        # pylint: disable=E1101
        """Convert all dict items that are in annotations into an instance of
        the specified class if the current value is of type dict."""
        for key, value in self.dict_data.items():
            if key in self.__annotations__:
                actual_type = self.__annotations__[key]
                if 'List' in str(actual_type):
                    cltype = actual_type.__args__[0]
                    nested_values = [cltype(item) for item in value]
                    setattr(self, key, nested_values)
                    continue

                # handle NoneTypes like x: int = None
                if value is None:
                    setattr(self, key, value)
                    continue

                try:
                    setattr(self, key, actual_type(value))
                except TypeError:
                    ERR_LOGGER.info(f'conversion failed for {key} {value}'
                                    f' {actual_type}\n')
                    raise

    def __init__(self, dict_data):
        self.dict_data = dict_data
        self.safe_init(self.dict_data)
        self.dicts_to_classes()
        self.check_for_missing_args()

    def safe_init(self, data_dict):
        # pylint: disable=E1101
        """Create an object including dynamic attributes.

        The dynamic attributes should be removed in the future.
        """
        dynamic_kwargs = {}
        for key, value in data_dict.items():
            if key not in self.__annotations__:
                dynamic_kwargs[key] = value

            setattr(self, key, value)

        if dynamic_kwargs:
            alert_keys = [key for key in dynamic_kwargs if 'patchwork_' not
                          in key]
            if alert_keys:
                missing_keys = ", ".join(alert_keys)
                msg = f'code-issue: following keys are not part of the' \
                      f' datastructure definition: {missing_keys}'
                ERR_LOGGER.debug(msg)
                # create annotations for undefined keys, type str
                for key in dynamic_kwargs:
                    self.__annotations__[key] = str

    def check_for_missing_args(self):
        # pylint: disable=E1101
        """Raise exception if required attribute isn't set.

        We have to use this way of making attributes required. Otherwise
        positional attributes would prevent us from using inheritance with
        dataclass classes in any meaningful way.

        Args:
            obj: an instance of a class decorated with dataclass,
                 to check for attributes
        Raises: TypeError when required attribute is missing
        """
        missing_args = []
        for key in self.__annotations__:
            if getattr(self, key, None) is _NO_DEFAULT:
                missing_args.append(key)

        if missing_args:
            missing = ', '.join(missing_args)
            raise TypeError(f"__init__ missing {len(missing_args)} required "
                            f"arguments: {missing}")

    def serialize2config(self):
        """Serialize object into a string with [section]."""
        # This can be read with ConfigParser.
        data = ''
        for section in self.dict_data.keys():
            data += f'[{section}]\n'
            for key, value in self.dict_data[section].items():
                if value is not None:
                    data += f'{key} = {value}\n'
            data += '\n'

        return data


class RunnerData(DefinitionBase):
    """SKT [runner] data only."""
    jobtemplate: str
    type: str = None  # to be removed: legacy pipelines only
    blacklist: str = None
    jobowner: str = None


class StateData(DefinitionBase):
    # pylint: disable=C0103, R0902
    """SKT rc [state] section extended with everything else in [state]."""
    func: str = None
    _name: str = None
    build_job_url: str = None
    commit_message_title: str = None
    config_file: str = None
    debug_kernel: str = None
    git_url: str = None
    kernel_config_url: str = None
    kernel_type: str = None
    make_opts: str = None
    make_target: str = None
    merge_branch: str = None
    merge_tree: str = None
    merge_tree_stage: str = None
    tag: str = None
    tarball_file: str = None
    test_hash: str = None

    lintcmd: str = None
    lintlog: str = None

    cross_compiler_prefix: str = None
    NO_REPORT: str = None
    buildlog: str = None
    mergelog: str = None
    modified_files: str = None
    reason: str = None
    repo_path: str = None
    stage_build: str = None
    stage_merge: str = None
    stage_setup: str = None
    stage_lint: str = None
    stage_publish: str = None
    stage_createrepo: str = None
    stage_skip: str = None
    stage_test: str = None
    targeted_tests: int = None
    targeted_tests_list: str = None
    verbose: int = None

    workdir: str = None

    kernel_package_url: str = None
    kernel_version: str = None
    kernel_arch: str = None
    jobs: str = None
    max_aborted_count: int = None
    rc: str = None
    retcode: int = None
    recipesets: str = None
    state: str = None
    wait: str = None
    waiving: str = None  # to be removed: legacy pipelines only


class SKTData(DefinitionBase):
    """Datastructure for all SKT data."""
    state: StateData
    runner: RunnerData = None

    @classmethod
    def deserialize(cls, str_data):
        """Deserialize string into this object."""
        return SKTData(parse_config_data(str_data))

    def serialize(self):
        """Serialize the object into string."""
        return self.serialize2config()


class RCData(DefinitionBase):
    # pylint: disable=R0902
    """rc-file data structure"""
    pipe_job: dict = _NO_DEFAULT
    state: StateData = _NO_DEFAULT
    runner: RunnerData = None

    job_stage: str = None
    job_id: int = None
    job_name: str = None
    name: str = None
    patchwork_patches: list = None
    localpatch_patches: list = None

    def __init__(self, data_dict):
        """Create the object."""
        super(RCData, self).__init__(data_dict)

        # This object is responsible for (de)serializing it rc file.
        # We run a fixture method on it, but eventually all of this should be
        # moved to where the data is created (like SKT).

        # Store job stage to help determine what data may be required
        self.job_stage = data_dict['pipe_job']['stage']
        # Store the name to help separate lint jobs
        self.job_name = data_dict['pipe_job']['name']
        self.job_id = data_dict['pipe_job']['id']

        if self.job_stage in [const.TEST_STAGE, const.SKIP_STAGE]:
            self.runner = RunnerData(data_dict['runner'])
        self.state = StateData(data_dict['state'])

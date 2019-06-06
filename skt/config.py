
import configparser
import logging
import os


class ConfigSet:
    """ A mockery of argparse.Namespace. Allows "flat" retrieval if there
       each key is unique to all the sections. Stores data in sections
       that are compatible with config file."""

    def __init__(self):
        # stores sectioned options/config file values
        self.data = {}

    def add_argument(self, parser, section, *args, **kwargs):
        """ Adds argument to argparse parser and saves [section]\nkey:value.

            Args:
                parser:  argparse parser to which we're adding arguments
                section: config file section like ['state'] (without brackets)
                args:    arguments passed to parser.add_argument
                kwargs:  keyword arguments passed to parser.add_argument
        """
        dashes = [arg for arg in args if isinstance(arg, str) and
                  arg.startswith('-')]
        if not dashes:
            raise RuntimeError('Count of -/-- variant options are not 1 or 2!')
        # find -short or --long option that indentifies it
        name = next(reversed(sorted(dashes, key=lambda x: x.startswith('--'))))
        name = name.lstrip('-').replace('-', '_')

        # add argument to argparse parser
        parser.add_argument(*args, **kwargs)

        # add names of arguments to config_set
        self.set_value(section, name, None)

    def load_args(self, args):
        """Loads cmd-line argparse.Namespace arguments"""
        # add their values to sectioned config_sets
        for section, dict_values in self.data.items():
            for key in dict_values.keys():
                value = args.__getattribute__(key)
                # save parsed value into config_set
                self.set_value(section, key, value)

    def __setattr__(self, attr, value):
        """Set attribute with value. Should be used only for existing keys."""
        # exclude .data, so we don't end-up looping
        if attr == 'data':
            super().__setattr__(attr, value)
        else:
            ret, section = self._getattr_section(attr)

            self.data[section][attr] = value

    def __getattr__(self, attr):
        """Flat retrieval of attribute."""
        ret, _ = self._getattr_section(attr)
        return ret

    def _getattr_section(self, attr):
        """Retrieves attr value and the section it is in."""
        sec = 'state'
        ret = None
        for section in self.data.keys():
            for key, value in self.data[section].items():
                if key == attr:
                    if ret is not None:
                        raise RuntimeError(
                            'each key can only be inside 1 section')

                    ret = value
                    sec = section

        return ret, sec

    def maybe_set_value(self, section, key, value):
        """Sets key:name to section, if it doesn't exist or is None."""
        if section not in self.data.keys():
            self.data[section] = {}

        try:
            self.data[section][key]
        except KeyError:
            # not set yet, we can safely override
            self.data[section][key] = value
        else:
            # current value is None, we can safely override
            if self.data[section][key] is None:
                self.data[section][key] = value

            # otherwise don't override anything!

    def set_value(self, section, key, value):
        """Sets value of key under section."""
        try:
            self.data[section][key] = value
        except KeyError:
            self.data[section] = {}
            self.data[section][key] = value

    def save_state(self, state):
        """Merge state to skt runtime configuration, and then save it.

        Args:
            state:  A dictionary of skt current state.
        """

        for key, val in state.items():
            current_val = getattr(self, key)
            if current_val:
                logging.debug("state: %s -> %s", key, val)

            setattr(self, key, val)

        c = configparser.RawConfigParser()
        c.read_dict(self.data)
        with open(f'{self.rc}', 'w') as fileh:
            c.write(fileh)


class ConfigFile:
    """Smart config file. Command-line arguments can override values in it."""
    def __init__(self, config_set, filepath):
        # NOTE: The shell should do any tilde expansions on the path
        # before the filepath path is provided to Python.
        rc = os.path.abspath(os.path.expanduser(filepath))

        # read input file (rc-file) using config file parser
        self.config_parser = configparser.RawConfigParser()
        self.config_parser.read(rc)

        # if there are unset values in command-line args, use config values
        for section in self.config_parser.sections():
            for key, value in self.config_parser.items(section):
                config_set.maybe_set_value(section, key, value)

        # save current config
        self.config_set = config_set

        # adjust options post all parsing to make object state
        # final and consistent
        self.fixture()

    def fixture(self):
        """Override this to modify config values post-parse."""

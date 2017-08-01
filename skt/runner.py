import logging
import os
import re
import subprocess

class runner(object):
    TYPE = 'default'

class beakerrunner(runner):
    TYPE = 'beaker'

    def __init__(self, jobtemplate):
        self.template = os.path.expanduser(jobtemplate)

        logging.info("runner type: %s", self.TYPE)
        logging.info("beaker template: %s", self.template)

    def getxml(self, replacements):
        xml = ''
        with open(self.template, 'r') as f:
            for line in f:
                for match in re.finditer("##(\w+)##", line):
                    if match.group(1) in replacements:
                        line = line.replace(match.group(0),
                                replacements[match.group(1)])

                xml += line

        return xml

    def run(self, release, url):
        bkr = subprocess.Popen(["bkr", "job-submit", "-"],
                stdin=subprocess.PIPE)
        bkr.communicate(self.getxml({'KVER' : release, 'KPKG_URL' : url}))

def getrunner(rtype, rarg):
    for cls in runner.__subclasses__():
        if cls.TYPE == rtype:
            return cls(**rarg)
    raise ValueError("Unknown runner type: %s" % rtype)

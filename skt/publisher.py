import logging
import os
import shutil
import subprocess

class publisher(object):
    TYPE = 'default'

    def __init__(self, dest, url):
        self.destination = dest
        self.baseurl = url

        logging.info("publisher type: %s", self.TYPE)
        logging.info("publisher destination: %s", self.destination)

    def geturl(self, source):
        return "%s/%s" % (self.baseurl, os.path.basename(source))

class cppublisher(publisher):
    TYPE = 'cp'

    def publish(self, source):
        shutil.copy(os.path.expanduser(source), self.destination)
        return self.geturl(source)

class scppublisher(publisher):
    TYPE = 'scp'

    def publish(self, source):
        subprocess.check_call(["scp", os.path.expanduser(source), self.destination])
        return self.geturl(source)

def getpublisher(ptype, parg, pburl):
    for cls in publisher.__subclasses__():
        if cls.TYPE == ptype:
            return cls(parg, pburl)
    raise ValueError("Unknown publisher type: %s" % ptype)

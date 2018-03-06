"""
Test cases for publisher module.
"""
import unittest
from skt import publisher


class TestPublisher(unittest.TestCase):
    """Test cases for publisher.publisher class"""
    def test_geturl(self):
        """Check if the source url is built correctly"""
        pub = publisher.publisher('dest', 'file:///tmp/test')
        self.assertEqual(pub.geturl('source'), 'file:///tmp/test/source')

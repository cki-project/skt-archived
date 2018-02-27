import unittest
from skt import publisher


class TestPublisher(unittest.TestCase):
    def test_geturl(self):
        pub = publisher.publisher('dest', 'file:///tmp/test')
        self.assertEqual(pub.geturl('source'), 'file:///tmp/test/source')

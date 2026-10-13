import tempfile
import unittest
from pathlib import Path
from workshop_validator.private_download import DownloadError, download_one


class PrivateDownloadTests(unittest.TestCase):
    def test_rejects_local_and_non_https_input_before_accessing_network(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'guide.pdf'
            for url in ('file:///tmp/guide.pdf','http://example.com/guide.pdf',
                        'https://unapproved.example/guide.pdf',
                        'https://user:password@approved.example/guide.pdf',
                        'https://approved.example:8080/guide.pdf'):
                with self.assertRaises(DownloadError):
                    download_one(url,p,allowed_host='approved.example')
                self.assertFalse(p.exists())

    def test_rejects_existing_destination_without_overwriting(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'guide.pdf'
            p.write_text('preserve')
            with self.assertRaises(DownloadError):
                download_one('https://approved.example/guide.pdf',p,allowed_host='approved.example')
            self.assertEqual(p.read_text(),'preserve')


if __name__=='__main__':
    unittest.main()

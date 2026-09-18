import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mirror


class Response(io.BytesIO):
    status = 200

    def __init__(self, body, url):
        super().__init__(body)
        self.url = url

    def geturl(self):
        return self.url


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = b'verified installer'
        self.asset = {
            'name': 'OpenICow-0.3.0-preview.2-test.zip',
            'url': 'https://openicow.com/downloads/0.3.0-preview.2/OpenICow-0.3.0-preview.2-test.zip',
            'bytes': len(self.body), 'sha256': hashlib.sha256(self.body).hexdigest(),
        }
        self.release = {'tag': 'openicow-v0.3.0-preview.2', 'version': '0.3.0-preview.2', 'assets': [self.asset]}
        self.record = {'draft': False, 'html_url': 'https://github.com/example/downloads/releases/tag/openicow-v0.3.0-preview.2',
                       'assets': [{'name': self.asset['name'], 'size': self.asset['bytes'], 'digest': 'sha256:' + self.asset['sha256']}]}

    def index(self, releases):
        path = self.root / 'index.json'
        path.write_text(json.dumps({'schemaVersion': 1, 'releases': releases}))
        return path

    def test_empty_and_real_index_are_valid(self):
        self.assertEqual(mirror.load_index(self.index([])), [])
        self.assertEqual(mirror.load_index(self.index([self.release])), [self.release])

    def test_url_path_names_sizes_and_duplicate_tags_are_rejected(self):
        bad = [
            {'url': self.asset['url'].replace('openicow.com', 'example.com')},
            {'url': self.asset['url'] + '?token=x'},
            {'name': '../OpenICow-0.3.0-preview.2-test.zip'},
            {'bytes': True}, {'sha256': '0' * 63},
        ]
        for changes in bad:
            value = copy.deepcopy(self.release)
            value['assets'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                mirror.load_index(self.index([value]))
        with self.assertRaisesRegex(ValueError, 'duplicate tag'):
            mirror.load_index(self.index([self.release, self.release]))

    def test_download_writes_exact_bytes_and_rejects_tampering_or_oversize(self):
        for i, body in enumerate([self.body, b'tampered installer', self.body + b'extra']):
            with self.subTest(body=body), patch.object(mirror, 'build_opener') as opener:
                opener.return_value.open.return_value = Response(body, self.asset['url'])
                target = self.root / str(i)
                if i:
                    with self.assertRaises(ValueError):
                        mirror.download(self.asset, target)
                else:
                    mirror.download(self.asset, target)
                    self.assertEqual(target.read_bytes(), self.body)

    def test_redirect_is_rejected_instead_of_contacting_another_host(self):
        request = Request(self.asset['url'])
        with self.assertRaises(HTTPError):
            mirror.NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://example.com/file')

    def test_matching_published_release_is_unchanged_and_mismatch_never_writes(self):
        with patch.object(mirror, 'existing_release', return_value=self.record), patch.object(mirror, 'download') as download, patch.object(mirror, 'gh') as gh:
            result = mirror.mirror(self.release, 'example/downloads')
        self.assertEqual(result['status'], 'unchanged')
        download.assert_not_called()
        gh.assert_not_called()
        changed = copy.deepcopy(self.record)
        changed['assets'][0]['digest'] = 'sha256:' + '0' * 64
        with patch.object(mirror, 'existing_release', return_value=changed), patch.object(mirror, 'gh') as gh:
            with self.assertRaisesRegex(ValueError, 'published release differs'):
                mirror.mirror(self.release, 'example/downloads')
        gh.assert_not_called()

    def test_interrupted_draft_can_resume_but_publication_requires_verified_github_digests(self):
        draft = {**self.record, 'draft': True, 'assets': []}
        uploaded = {**self.record, 'draft': True}
        with patch.object(mirror, 'existing_release', side_effect=[draft, draft, uploaded]), patch.object(mirror, 'download') as download, patch.object(mirror, 'gh') as gh:
            result = mirror.mirror(self.release, 'example/downloads')
        self.assertEqual(result['status'], 'published')
        download.assert_called_once()
        self.assertEqual([call.args[:2] for call in gh.call_args_list], [('release', 'upload'), ('release', 'edit')])
        self.assertIn('--clobber', gh.call_args_list[0].args)
        with patch.object(mirror, 'existing_release', side_effect=[draft, draft, draft]), patch.object(mirror, 'download'), patch.object(mirror, 'gh') as gh:
            with self.assertRaisesRegex(ValueError, 'digests do not match'):
                mirror.mirror(self.release, 'example/downloads')
        self.assertEqual([call.args[:2] for call in gh.call_args_list], [('release', 'upload')])

    def test_only_confirmed_404_means_release_missing(self):
        for stderr, missing in [('gh: Not Found (HTTP 404)', True), ('connection timed out', False), ('gh: Forbidden (HTTP 403)', False)]:
            process = subprocess.CompletedProcess(['gh'], 1, stdout='', stderr=stderr)
            with self.subTest(stderr=stderr), patch.object(mirror.subprocess, 'run', return_value=process):
                if missing:
                    self.assertIsNone(mirror.existing_release('example/downloads', self.release['tag']))
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        mirror.existing_release('example/downloads', self.release['tag'])


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Validate a public download index and mirror verified website packages to GitHub."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = r'(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})(?:-[a-z][a-z0-9-]*\.(?:0|[1-9][0-9]{0,4}))?'


def load_index(path):
    path = Path(path)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('index exceeds 1 MiB')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {'schemaVersion', 'releases'} or value['schemaVersion'] != 1:
        raise ValueError('invalid index schema')
    releases = value['releases']
    if not isinstance(releases, list) or len(releases) > 64:
        raise ValueError('invalid releases list')
    tags = set()
    for release in releases:
        if not isinstance(release, dict) or set(release) != {'tag', 'version', 'assets'}:
            raise ValueError('invalid release')
        version = release['version']
        if not isinstance(version, str) or not re.fullmatch(VERSION, version):
            raise ValueError('invalid version')
        if release['tag'] != 'openicow-v' + version or release['tag'] in tags:
            raise ValueError('invalid or duplicate tag')
        tags.add(release['tag'])
        assets = release['assets']
        if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
            raise ValueError('release requires 1 to 16 assets')
        names = set()
        for asset in assets:
            if not isinstance(asset, dict) or set(asset) != {'name', 'url', 'bytes', 'sha256'}:
                raise ValueError('invalid asset')
            name = asset['name']
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,199}', name):
                raise ValueError('invalid asset name')
            if name in names or Path(name).suffix.lower() not in {'.dmg', '.zip', '.exe', '.apk', '.txt'}:
                raise ValueError('invalid or duplicate asset name')
            names.add(name)
            if asset['url'] != f'https://openicow.com/downloads/{version}/{name}':
                raise ValueError('asset URL must be the canonical openicow.com download')
            if type(asset['bytes']) is not int or not 0 < asset['bytes'] <= 2 * 1024**3:
                raise ValueError('invalid asset size')
            if not isinstance(asset['sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', asset['sha256']):
                raise ValueError('invalid asset digest')
    return releases


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        raise HTTPError(request.full_url, code, 'download redirects are not allowed', headers, fp)


def download(asset, destination):
    request = Request(asset['url'], headers={'User-Agent': 'OpenICow-Release/1.0'})
    digest = hashlib.sha256()
    total = 0
    with build_opener(NoRedirect()).open(request, timeout=60) as response, Path(destination).open('xb') as output:
        if response.status != 200 or response.geturl() != asset['url']:
            raise ValueError('unexpected download response')
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > asset['bytes']:
                raise ValueError('download exceeds expected size')
            output.write(chunk)
            digest.update(chunk)
    if total != asset['bytes'] or digest.hexdigest() != asset['sha256']:
        raise ValueError('download size or SHA-256 mismatch')


def gh(*arguments):
    return subprocess.run(['gh', *arguments], check=True, capture_output=True, text=True).stdout.strip()


def existing_release(repo, tag):
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', repo, '--json', 'apiUrl'], capture_output=True, text=True)
    if result.returncode:
        if result.stderr.strip() == 'release not found':
            return None
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    api_url = json.loads(result.stdout).get('apiUrl')
    if not isinstance(api_url, str) or not re.fullmatch(r'https://api\.github\.com/repos/' + re.escape(repo) + r'/releases/[0-9]+', api_url):
        raise ValueError('unexpected GitHub release API URL')
    return json.loads(gh('api', api_url))


def matches(existing, assets):
    actual = {a['name']: (a['size'], a.get('digest')) for a in existing['assets']}
    expected = {a['name']: (a['bytes'], 'sha256:' + a['sha256']) for a in assets}
    return len(existing['assets']) == len(actual) and actual == expected


def mirror(release, repo):
    tag, assets = release['tag'], release['assets']
    existing = existing_release(repo, tag)
    if existing and not existing['draft']:
        if not matches(existing, assets):
            raise ValueError('published release differs; use a new version instead of overwriting it')
        return {'tag': tag, 'status': 'unchanged', 'url': existing['html_url']}
    if existing and {a['name'] for a in existing['assets']} - {a['name'] for a in assets}:
        raise ValueError('draft contains assets outside the index')
    with tempfile.TemporaryDirectory(prefix='openicow-mirror-', dir=os.environ.get('RUNNER_TEMP')) as directory:
        paths = []
        for asset in assets:
            path = Path(directory) / asset['name']
            download(asset, path)
            paths.append(str(path))
        if not existing:
            gh('release', 'create', tag, '--repo', repo, '--draft', '--title', f"OpenICow {release['version']}",
               '--notes', 'Verified download mirror. Website: https://openicow.com/',
               *(['--prerelease'] if '-' in release['version'] else []))
        current = existing_release(repo, tag)
        if not current or not current['draft']:
            raise ValueError('release must remain a draft while uploading')
        gh('release', 'upload', tag, '--repo', repo, '--clobber', *paths)
        current = existing_release(repo, tag)
        if not current or not current['draft'] or not matches(current, assets):
            raise ValueError('uploaded GitHub asset digests do not match the index')
        gh('release', 'edit', tag, '--repo', repo, '--draft=false')
        return {'tag': tag, 'status': 'published', 'url': current['html_url']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', default='release-index.json')
    parser.add_argument('--mode', choices=['check', 'publish'], default='check')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'))
    args = parser.parse_args()
    releases = load_index(args.index)
    if args.mode == 'check':
        result = {'mode': 'check', 'releases': len(releases), 'assets': sum(len(r['assets']) for r in releases)}
    else:
        if not isinstance(args.repo, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', args.repo):
            raise ValueError('invalid GitHub repository')
        result = {'mode': 'publish', 'releases': [mirror(release, args.repo) for release in releases]}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

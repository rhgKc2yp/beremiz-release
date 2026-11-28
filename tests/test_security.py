"""Offline regression tests for the high/critical scanning findings."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import build_paths
import resolve


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wx = load('wx-deps')
bacnet = load('verify-bacnet')


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.override = patch.object(build_paths, 'REPO', self.repo)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.bundle = self.repo / 'build/beremiz'
        self.bundle.mkdir(parents=True)
        self.outside = self.root / 'outside'
        self.outside.mkdir()

    def test_fixed_build_and_archive(self):
        archive = self.repo / 'build/archive-test/beremiz'
        archive.mkdir(parents=True)
        self.assertEqual(build_paths.bundle_path(), self.bundle)
        self.assertEqual(build_paths.bundle_path(True), archive)

    def test_bundle_and_build_symlinks_rejected(self):
        self.bundle.rmdir()
        self.bundle.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            build_paths.bundle_path()
        self.bundle.unlink()
        self.bundle.parent.rmdir()
        self.bundle.parent.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            build_paths.bundle_path()

    def test_output_path_traversal_and_symlinks(self):
        for path in ('../outside', '/tmp', 'sub/../../outside'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                build_paths.output_path(self.bundle, path)
        (self.bundle / 'lib').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            build_paths.output_path(self.bundle, 'lib/file')
        self.assertFalse((self.outside / 'file').exists())

    def test_special_file_rejected(self):
        fifo = self.bundle / 'pipe'
        os.mkfifo(fifo)
        with self.assertRaises(ValueError):
            build_paths.output_path(self.bundle, 'pipe')
        with self.assertRaises(ValueError):
            build_paths.validate_tree(self.bundle)

    def test_tree_allows_internal_links_rejects_escape(self):
        (self.bundle / 'file').write_text('safe')
        (self.bundle / 'link').symlink_to('file')
        build_paths.validate_tree(self.bundle)
        with self.assertRaises(ValueError):
            build_paths.reject_destination_links(self.bundle)
        (self.bundle / 'bad').symlink_to('../../outside')
        with self.assertRaises(ValueError):
            build_paths.validate_tree(self.bundle)

    def test_wx_architecture_and_filename(self):
        self.assertEqual(wx.cache_path('x86_64'), self.repo / 'build/ubuntu-wx/x86_64')
        self.assertEqual(wx.cache_path('aarch64'), self.repo / 'build/ubuntu-wx/aarch64')
        with self.assertRaises(ValueError):
            wx.cache_path('../../outside')
        cache = wx.cache_path('x86_64')
        cache.mkdir(parents=True)
        self.assertEqual(wx.package_path(cache, 'libwx_4.2.4+dfsg-1_amd64.deb'),
                         cache / 'libwx_4.2.4+dfsg-1_amd64.deb')
        for name in ('../outside.deb', '/tmp/a.deb', 'x/y.deb', 'x%2fother.deb', 'lock.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                wx.package_path(cache, name)
        (cache / 'evil.deb').symlink_to(self.outside / 'target')
        with self.assertRaises(ValueError):
            wx.package_path(cache, 'evil.deb')

    def test_bacnet_child_workdir(self):
        base = build_paths.bacnet_work_base()
        with tempfile.TemporaryDirectory(prefix='run-', dir=base) as directory:
            work = Path(directory)
            (work / 'project').mkdir()
            with patch.object(Path, 'cwd', return_value=work):
                self.assertEqual(build_paths.bacnet_child_project(), work / 'project')
                (work / 'project').rmdir()
                (work / 'project').symlink_to(self.outside, target_is_directory=True)
                with self.assertRaises(ValueError):
                    build_paths.bacnet_child_project()
        with patch.object(Path, 'cwd', return_value=self.outside), self.assertRaises(ValueError):
            build_paths.bacnet_child_project()

    def test_bacnet_private_mode(self):
        base = build_paths.bacnet_work_base()
        base.chmod(0o755)
        with self.assertRaises(ValueError):
            build_paths.bacnet_work_base()

    def test_bacnet_uses_fixed_executables_and_no_path_arguments(self):
        example = self.bundle / 'beremiz/tests/projects/BACnet/bacnet_0@bacnet'
        example.mkdir(parents=True)
        (example / 'confnode.xml').write_text('<node/>')
        (self.bundle / 'bin').mkdir()
        work = self.repo / 'build/work'
        work.mkdir()
        with patch.object(bacnet.subprocess, 'run', side_effect=RuntimeError('stop before execution')) as run:
            with self.assertRaisesRegex(RuntimeError, 'stop before execution'):
                bacnet.verify(self.bundle, work, archive=True)
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(self.bundle / 'bin/beremiz-python'),
                                  str(SCRIPTS / 'verify-bacnet.py'), '--project-data', '--archive'])
        self.assertEqual(kwargs['cwd'], work)


class NetworkTests(unittest.TestCase):
    def test_allowed_urls(self):
        for url in ('https://api.github.com/repos/beremiz/beremiz/releases/latest',
                    'https://api.github.com/repos/beremiz/beremiz/releases/tags/v1.6.0',
                    'https://api.github.com/repos/beremiz/beremiz/commits/v1.6.0',
                    f'https://raw.githubusercontent.com/beremiz/beremiz_public_dist/{resolve.RECIPE}/.github/workflows/prep_sources.yml',
                    'https://codeload.github.com/bacnet-stack/bacnet-stack/tar.gz/' + 'a' * 40):
            with self.subTest(url=url):
                self.assertTrue(resolve.validate_url(url))

    def test_invalid_urls_never_open(self):
        prefix = 'https://api.github.com/repos/beremiz/beremiz/releases/tags/'
        for url in (prefix + '../latest', prefix + 'v1.6.0?foo=bar', prefix + 'v1.6.0#x',
                    prefix + '%2e%2e', prefix + 'v1.6.0\n', 'https://api.github.com@localhost/',
                    'https://api.github.com:443/repos/beremiz/beremiz/releases/latest',
                    'http://api.github.com/repos/beremiz/beremiz/releases/latest',
                    'https://codeload.github.com/other/repo/tar.gz/' + 'a' * 40):
            with self.subTest(url=url), patch.object(resolve.urllib.request, 'build_opener') as opener:
                with self.assertRaises(ValueError):
                    resolve.get(url)
                opener.assert_not_called()

    def test_invalid_tag_no_network(self):
        with patch.object(sys, 'argv', ['resolve.py', '--tag', '../latest']), patch.object(resolve, 'get') as get:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                resolve.main()
            get.assert_not_called()

    def test_redirect_is_denied(self):
        request = urllib.request.Request('https://api.github.com/repos/beremiz/beremiz/releases/latest')
        with self.assertRaises(urllib.error.HTTPError):
            resolve.NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://example.org/')

    def test_token_only_on_api(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'test-token'}), patch.object(resolve.urllib.request, 'build_opener') as make:
            response = make.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'ok'
            self.assertEqual(resolve.get('https://api.github.com/repos/beremiz/beremiz/releases/latest'), b'ok')
            request = make.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
            resolve.get('https://codeload.github.com/beremiz/beremiz/tar.gz/' + 'a'*40)
            request = make.return_value.open.call_args.args[0]
            self.assertIsNone(request.get_header('Authorization'))
            self.assertIsInstance(make.call_args.args[0], resolve.NoRedirect)

    def test_ubuntu_pool_path_and_digest(self):
        self.assertEqual(wx.pool_filename('pool/universe/w/wxpython4.0/libwx_4.2.4+dfsg-1_amd64.deb'),
                         'libwx_4.2.4+dfsg-1_amd64.deb')
        for path in ('pool/main/../../bad.deb', 'https://example.org/bad.deb', 'pool/main/a/%2e%2e.deb',
                     'pool/main/a/bad.deb?x=1'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                wx.pool_filename(path)
        data = b'package'
        record = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'package': 'test'}
        wx.check_package(data, record)
        with self.assertRaises(SystemExit):
            wx.check_package(b'invalid', record)


class EntryPointTests(unittest.TestCase):
    def test_old_arbitrary_path_cli_rejected(self):
        for script, args in [('check-layout.py', []), ('source-index.py', []), ('toolchain.py', []),
                             ('verify-bacnet.py', []), ('verify-bacnet-gui.py', []), ('wx-deps.py', ['install'])]:
            with self.subTest(script=script):
                result = subprocess.run([sys.executable, str(SCRIPTS / script), *args, '/tmp'],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('unrecognized arguments', result.stderr)

    def test_layout_and_source_index_with_fixed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            scripts = repo / 'scripts'
            scripts.mkdir()
            for name in ('build_paths.py', 'check-layout.py', 'source-index.py'):
                shutil.copy2(SCRIPTS / name, scripts / name)
            bundle = repo / 'build/beremiz'
            for name in ('bin/beremiz', 'bin/beremiz-cli', 'bin/beremiz-python',
                         'matiec/iec2c', 'matiec/lib/ieclib.txt', 'toolchain/bin/gcc',
                         'beremiz/Beremiz.py', 'BACnet/include/bacdef.h',
                         'BACnet/ports/linux/bip-init.c', 'BACnet/lib/libbacnet.a',
                         'PATCHES/0001-bacnet-python3.patch', 'PATCHES/0002-bacnet-wx-editor.patch'):
                path = bundle / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            for name, value in {'SOURCES.lock.json': {'release_url': 'https://example.org/',
                                                     'recipe_commit': 'a'*40, 'sources': {}},
                                'UBUNTU-PACKAGES.json': [], 'RUNTIME-COMMITS.json': {},
                                'SDK-MANIFEST.json': {'modules': []}}.items():
                (bundle / name).write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(scripts / 'source-index.py')],
                                    cwd='/', capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Beremiz community source build', result.stdout)
            archive = repo / 'build/archive-test/beremiz'
            shutil.copytree(bundle, archive)
            result = subprocess.run([sys.executable, str(scripts / 'check-layout.py'), '--archive', '--links-only'],
                                    cwd='/', capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            (archive / 'external').symlink_to('/tmp')
            result = subprocess.run([sys.executable, str(scripts / 'check-layout.py'), '--archive', '--links-only'],
                                    cwd='/', capture_output=True, text=True, timeout=5)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('External symlink', result.stderr)

    def test_shell_invalid_mode_rejected(self):
        result = subprocess.run(['bash', str(SCRIPTS / 'verify.sh'), '/tmp'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()

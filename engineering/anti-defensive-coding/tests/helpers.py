from contextlib import chdir, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest


sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "scan_python.py"
SKILLS_ROOT = SCRIPT.parents[3]
SCRIPT_DIR = str(SCRIPT.parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import scan_python  # noqa: E402


class ExtractorTestCase:
    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    def _class_workspace(cls, tmp_path_factory):
        cls._class_temp = tmp_path_factory.mktemp(cls.__name__)
        cls.git_template = cls._class_temp / "template"
        cls.git_template.mkdir()
        cls.run_git(cls.git_template, "init", "-q", "-b", "main")
        cls._fixtures = {}

    @pytest.fixture(autouse=True)
    def _workspace(self, tmp_path):
        self.root = tmp_path

    @classmethod
    def run_git(cls, cwd, *args):
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Extractor Test",
                "-c",
                "user.email=test@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=cwd,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    @classmethod
    def head_sha(cls, root):
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = root / ".git" / head[5:]
            if ref.is_file():
                return ref.read_text(encoding="utf-8").strip()
        return cls.run_git(root, "rev-parse", "HEAD")

    @classmethod
    def write_source(cls, root, path, source):
        target = root / path
        if source is None:
            target.unlink(missing_ok=True)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, bytes):
            target.write_bytes(source)
        else:
            target.write_text(
                textwrap.dedent(source).lstrip("\n"),
                encoding="utf-8",
            )
        return target

    @classmethod
    def git_fixture(cls, name, *snapshots, working_files=None):
        if name not in cls._fixtures:
            repo = cls._class_temp / "fixtures" / name
            shutil.copytree(cls.git_template / ".git", repo / ".git")
            commits = []
            for files in snapshots:
                for path, source in files.items():
                    cls.write_source(repo, path, source)
                cls.run_git(repo, "add", ".")
                cls.run_git(repo, "commit", "-qm", "fixture")
                commits.append(cls.head_sha(repo))
            if working_files is not None:
                for path, source in working_files.items():
                    cls.write_source(repo, path, source)
            cls._fixtures[name] = (repo, tuple(commits))
        return cls._fixtures[name]

    def put(self, path, source):
        return self.write_source(self.root, path, source)

    def check_cli_result(self, returncode, stdout, stderr, code):
        assert returncode == code, stderr
        if code == 0:
            assert stderr == ""
        else:
            assert stderr
        return stdout

    def cli(self, *args, code=0, cwd=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        target_cwd = self.root if cwd is None else cwd
        with chdir(target_cwd), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                returncode = scan_python.main([*map(str, args)])
            except SystemExit as exit_:
                returncode = exit_.code if isinstance(exit_.code, int) else 1
        return self.check_cli_result(
            returncode,
            stdout.getvalue(),
            stderr.getvalue(),
            code,
        )

    def cli_subprocess(self, *args, code=0, cwd=None):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)],
            cwd=self.root if cwd is None else cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return self.check_cli_result(
            result.returncode,
            result.stdout,
            result.stderr,
            code,
        )

    def scan(self, *args, **kwargs):
        return json.loads(self.cli(*args, "--format", "json", **kwargs))

    def git(self, *args, cwd=None):
        return self.run_git(self.root if cwd is None else cwd, *args)

    def init_repo(self):
        git_dir = self.root / ".git"
        if git_dir.exists():
            shutil.rmtree(git_dir)
        shutil.copytree(self.git_template / ".git", git_dir)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.head_sha(self.root)

    def use_git_fixture(self, name, *snapshots, working_files=None):
        repo, commits = self.git_fixture(name, *snapshots, working_files=working_files)
        self.root = repo
        return commits[0] if len(commits) == 1 else commits

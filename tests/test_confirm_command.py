"""
Unit tests for cf confirm command.
"""
import io
import json
import stat

import pytest
from click.testing import CliRunner
from chipfoundry_cli.main import (
    confirm_duplicate_stub_dirs,
    list_incoming_confirm_candidates,
    main,
    resolve_confirm_sftp_dirname,
)


class _Attr:
    def __init__(self, filename, is_dir):
        self.filename = filename
        self.st_mode = stat.S_IFDIR | 0o755 if is_dir else stat.S_IFREG | 0o644


class _FakeSFTP:
    def __init__(self, tree):
        self.tree = tree

    def listdir_attr(self, path):
        node = self.tree.get(path)
        if node is None:
            raise FileNotFoundError(path)
        if not isinstance(node, dict):
            raise NotADirectoryError(path)
        return [_Attr(name, isinstance(child, dict)) for name, child in node.items()]

    def open(self, path, mode="r"):
        node = self.tree.get(path)
        if not isinstance(node, str):
            raise FileNotFoundError(path)
        return io.StringIO(node)


class TestConfirmCommand:
    """Test suite for cf confirm command."""
    
    def test_confirm_help(self):
        """Test confirm command help output."""
        runner = CliRunner()
        result = runner.invoke(main, ['confirm', '--help'])
        
        assert result.exit_code == 0
        assert 'Confirm project submission' in result.output
        assert '--project-root' in result.output
        assert '--sftp-host' in result.output
        assert '--sftp-username' in result.output
        assert '--sftp-key' in result.output
        assert '--project-name' in result.output
    
    def test_confirm_with_all_options(self):
        """Test confirm command with all options."""
        runner = CliRunner()
        result = runner.invoke(main, [
            'confirm',
            '--project-root', '/tmp/test',
            '--sftp-host', 'test.example.com',
            '--sftp-username', 'testuser',
            '--project-name', 'test_project'
        ])
        
        # Should fail without proper setup, but options should be recognized
        assert result.exit_code != 0


PID = "af56147b-1994-49e6-a68e-79f9f3c3f081"


def test_confirm_updates_the_directory_that_has_the_wrapper():
    candidates = [
        {
            "name": "AF-R2",
            "platform_project_id": PID,
            "has_wrapper": False,
            "files": (".cf/project.json",),
        },
        {
            "name": "af-r2",
            "platform_project_id": PID,
            "has_wrapper": True,
            "files": (".cf/project.json", "gds/openframe_project_wrapper.gds"),
        },
    ]

    assert resolve_confirm_sftp_dirname("AF-R2", candidates, PID) == "af-r2"


def test_confirm_uses_the_project_name_when_nothing_is_uploaded_yet():
    assert resolve_confirm_sftp_dirname("AF-R2", [], PID) == "AF-R2"


def test_confirm_keeps_an_exact_directory_that_already_has_the_wrapper():
    candidates = [
        {
            "name": "AF-R2",
            "platform_project_id": PID,
            "has_wrapper": True,
            "files": (".cf/project.json", "gds/openframe_project_wrapper.gds"),
        }
    ]

    assert resolve_confirm_sftp_dirname("AF-R2", candidates, PID) == "AF-R2"


def test_confirm_matches_a_case_variant_by_directory_name():
    candidates = [
        {
            "name": "af-a2",
            "platform_project_id": None,
            "has_wrapper": True,
            "files": ("gds/openframe_project_wrapper.gds",),
        }
    ]

    assert resolve_confirm_sftp_dirname("AF-A2", candidates, PID) == "af-a2"


def test_confirm_does_not_reuse_a_different_project_with_the_same_spelling():
    candidates = [
        {
            "name": "af-r2",
            "platform_project_id": "someone-else",
            "has_wrapper": True,
            "files": ("gds/openframe_project_wrapper.gds",),
        }
    ]

    assert resolve_confirm_sftp_dirname("AF-R2", candidates, PID) == "AF-R2"


def test_confirm_stub_dirs_are_json_only_duplicates():
    candidates = [
        {
            "name": "AF-R2",
            "platform_project_id": PID,
            "has_wrapper": False,
            "files": (".cf/project.json",),
        },
        {
            "name": "af-r2",
            "platform_project_id": PID,
            "has_wrapper": True,
            "files": (".cf/project.json", "gds/openframe_project_wrapper.gds"),
        },
        {
            "name": "notes",
            "platform_project_id": PID,
            "has_wrapper": False,
            "files": (".cf/project.json", "README.md"),
        },
    ]

    assert confirm_duplicate_stub_dirs("af-r2", "AF-R2", candidates, PID) == ["AF-R2"]


def test_list_incoming_confirm_candidates_reads_project_json_and_wrapper():
    project_json = json.dumps({"project": {"platform_project_id": PID, "name": "AF-R2"}})
    sftp = _FakeSFTP({
        "incoming/projects": {
            "af-r2": {},
            "AF-R2": {},
        },
        "incoming/projects/af-r2": {
            ".cf": {},
            "gds": {},
        },
        "incoming/projects/af-r2/.cf": {"project.json": project_json},
        "incoming/projects/af-r2/.cf/project.json": project_json,
        "incoming/projects/af-r2/gds": {"openframe_project_wrapper.gds": "gds-bytes"},
        "incoming/projects/af-r2/gds/openframe_project_wrapper.gds": "gds-bytes",
        "incoming/projects/AF-R2": {".cf": {}},
        "incoming/projects/AF-R2/.cf": {"project.json": project_json},
        "incoming/projects/AF-R2/.cf/project.json": project_json,
    })

    candidates = list_incoming_confirm_candidates(sftp)
    by_name = {candidate["name"]: candidate for candidate in candidates}

    assert by_name["af-r2"]["has_wrapper"] is True
    assert by_name["af-r2"]["platform_project_id"] == PID
    assert by_name["AF-R2"]["has_wrapper"] is False
    assert resolve_confirm_sftp_dirname("AF-R2", candidates, PID) == "af-r2"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

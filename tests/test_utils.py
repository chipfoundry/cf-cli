"""
Unit tests for utility functions that can be tested without external dependencies.
"""
import pytest
from pathlib import Path
import json
import tempfile
import shutil
import os
from chipfoundry_cli.utils import (
    collect_project_files,
    ensure_cf_directory,
    calculate_sha256,
    load_project_json,
    save_project_json,
    GDS_TYPE_MAP,
    REQUIRED_FILES
)


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory for testing."""
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture
def complete_project_dir(temp_project_dir):
    """Create a complete project directory with all required files."""
    project_root = Path(temp_project_dir)
    
    # Create required verilog file
    verilog_dir = project_root / 'verilog' / 'rtl'
    verilog_dir.mkdir(parents=True, exist_ok=True)
    (verilog_dir / 'user_defines.v').write_text('// test defines')
    
    # Create GDS file
    gds_dir = project_root / 'gds'
    gds_dir.mkdir(parents=True, exist_ok=True)
    (gds_dir / 'user_project_wrapper.gds').write_text('dummy gds content')
    
    return temp_project_dir


class TestCollectProjectFiles:
    """Test collect_project_files function."""
    
    def test_collect_complete_project(self, complete_project_dir):
        """Test collecting files from a complete project."""
        collected = collect_project_files(complete_project_dir)
        
        assert 'verilog/rtl/user_defines.v' in collected
        assert collected['verilog/rtl/user_defines.v'] is not None
        assert 'gds/user_project_wrapper.gds' in collected
        assert collected['gds/user_project_wrapper.gds'] is not None
    
    def test_collect_missing_required_file(self, temp_project_dir):
        """Test that missing required files raise FileNotFoundError."""
        # Create directory but no required files
        Path(temp_project_dir).mkdir(parents=True, exist_ok=True)
        
        with pytest.raises(FileNotFoundError):
            collect_project_files(temp_project_dir)
    
    def test_collect_digital_gds(self, temp_project_dir):
        """Test collecting digital GDS file."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        (verilog_dir / 'user_defines.v').write_text('// test')
        
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'user_project_wrapper.gds').write_text('gds')
        
        collected = collect_project_files(temp_project_dir)
        assert 'gds/user_project_wrapper.gds' in collected
    
    def test_collect_analog_gds(self, temp_project_dir):
        """Test collecting analog GDS file."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        (verilog_dir / 'user_defines.v').write_text('// test')
        
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'user_analog_project_wrapper.gds').write_text('gds')
        
        collected = collect_project_files(temp_project_dir)
        assert 'gds/user_analog_project_wrapper.gds' in collected
    
    def test_collect_compressed_gds(self, temp_project_dir):
        """Test collecting compressed GDS file."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        (verilog_dir / 'user_defines.v').write_text('// test')
        
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'user_project_wrapper.gds.gz').write_text('compressed gds')
        
        collected = collect_project_files(temp_project_dir)
        assert 'gds/user_project_wrapper.gds.gz' in collected
    
    def test_collect_rejects_multiple_gds_types(self, temp_project_dir):
        """Test that multiple GDS types raise an error."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        (verilog_dir / 'user_defines.v').write_text('// test')
        
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'user_project_wrapper.gds').write_text('digital')
        (gds_dir / 'user_analog_project_wrapper.gds').write_text('analog')
        
        with pytest.raises(FileNotFoundError) as exc_info:
            collect_project_files(temp_project_dir)
        assert 'Multiple project types' in str(exc_info.value)
    
    def test_collect_rejects_compressed_and_uncompressed(self, temp_project_dir):
        """Test that both compressed and uncompressed versions raise an error."""
        project_root = Path(temp_project_dir)
        verilog_dir = project_root / 'verilog' / 'rtl'
        verilog_dir.mkdir(parents=True, exist_ok=True)
        (verilog_dir / 'user_defines.v').write_text('// test')
        
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'user_project_wrapper.gds').write_text('uncompressed')
        (gds_dir / 'user_project_wrapper.gds.gz').write_text('compressed')
        
        with pytest.raises(FileNotFoundError) as exc_info:
            collect_project_files(temp_project_dir)
        assert 'compressed and uncompressed' in str(exc_info.value).lower()

    def test_collect_openframe_without_user_defines_v(self, temp_project_dir):
        """Test that openframe projects do not require user_defines.v."""
        project_root = Path(temp_project_dir)
        gds_dir = project_root / 'gds'
        gds_dir.mkdir(parents=True, exist_ok=True)
        (gds_dir / 'openframe_project_wrapper.gds').write_text('gds')
        # No verilog/rtl/user_defines.v

        collected = collect_project_files(temp_project_dir)
        assert 'gds/openframe_project_wrapper.gds' in collected
        assert collected['verilog/rtl/user_defines.v'] is None


class TestEnsureCfDirectory:
    """Test ensure_cf_directory function."""
    
    def test_creates_cf_directory(self, temp_project_dir):
        """Test that ensure_cf_directory creates .cf directory."""
        cf_dir = ensure_cf_directory(temp_project_dir)
        
        assert cf_dir.exists()
        assert cf_dir.is_dir()
        assert cf_dir.name == '.cf'
    
    def test_creates_nested_cf_directory(self, temp_project_dir):
        """Test that ensure_cf_directory creates nested .cf directory."""
        nested_dir = Path(temp_project_dir) / 'nested' / 'path'
        cf_dir = ensure_cf_directory(str(nested_dir))
        
        assert cf_dir.exists()
        assert (nested_dir / '.cf').exists()


class TestCalculateSha256:
    """Test calculate_sha256 function."""
    
    def test_calculates_hash(self, temp_project_dir):
        """Test that SHA256 hash is calculated correctly."""
        test_file = Path(temp_project_dir) / 'test.txt'
        test_file.write_text('test content')
        
        hash_value = calculate_sha256(str(test_file))
        
        assert len(hash_value) == 64  # SHA256 produces 64 hex characters
        assert isinstance(hash_value, str)
    
    def test_hash_is_deterministic(self, temp_project_dir):
        """Test that same content produces same hash."""
        test_file = Path(temp_project_dir) / 'test.txt'
        test_file.write_text('test content')
        
        hash1 = calculate_sha256(str(test_file))
        hash2 = calculate_sha256(str(test_file))
        
        assert hash1 == hash2
    
    def test_hash_differs_for_different_content(self, temp_project_dir):
        """Test that different content produces different hashes."""
        file1 = Path(temp_project_dir) / 'test1.txt'
        file1.write_text('content 1')
        
        file2 = Path(temp_project_dir) / 'test2.txt'
        file2.write_text('content 2')
        
        hash1 = calculate_sha256(str(file1))
        hash2 = calculate_sha256(str(file2))
        
        assert hash1 != hash2


class TestProjectJson:
    """Test project.json loading and saving."""
    
    def test_load_project_json(self, temp_project_dir):
        """Test loading project.json."""
        json_file = Path(temp_project_dir) / 'project.json'
        json_file.write_text(json.dumps({"project": {"name": "test"}}))
        
        data = load_project_json(str(json_file))
        
        assert data['project']['name'] == 'test'
    
    def test_save_project_json(self, temp_project_dir):
        """Test saving project.json."""
        json_file = Path(temp_project_dir) / 'project.json'
        data = {"project": {"name": "test", "type": "digital"}}
        
        save_project_json(str(json_file), data)
        
        assert json_file.exists()
        loaded = json.loads(json_file.read_text())
        assert loaded == data
    
    def test_save_and_load_roundtrip(self, temp_project_dir):
        """Test that save and load work together."""
        json_file = Path(temp_project_dir) / 'project.json'
        original_data = {
            "project": {
                "name": "test_project",
                "type": "digital"
            },
            "version": "1.0.0"
        }
        
        save_project_json(str(json_file), original_data)
        loaded_data = load_project_json(str(json_file))
        
        assert loaded_data == original_data


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

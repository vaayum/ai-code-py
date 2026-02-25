import pytest
import shutil
from pathlib import Path
from aicoder.ingestor import CodebaseIngestor

def test_fingerprint_pruning(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.py").write_text("def a(): pass")
    (root / "b.py").write_text("def b(): pass")
    
    ingestor = CodebaseIngestor(root)
    new_chunks = ingestor.ingest(quiet=True)
    assert new_chunks > 0
    assert "a.py" in ingestor._fingerprints
    assert "b.py" in ingestor._fingerprints
    
    # Delete a file
    (root / "b.py").unlink()
    
    # Re-ingest
    new_chunks = ingestor.ingest(quiet=False)
    print(f"\nDebug: new_chunks={new_chunks}, fingerprints={ingestor._fingerprints}")
    assert new_chunks == 0
    assert "a.py" in ingestor._fingerprints
    assert "b.py" not in ingestor._fingerprints

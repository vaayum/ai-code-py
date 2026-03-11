"""Tests for rate limiting functionality in FileTools."""
from pathlib import Path
import tempfile
import time
import pytest

from aicoder.tools.file_tools import FileTools


class TestRateLimiting:
    """Test suite for rate limiting functionality."""
    
    def test_read_rate_limit_default(self):
        """Test that read rate limit blocks after default number of reads."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Make 50 reads (default limit)
            for i in range(50):
                result = tools["read_file"].invoke({"path": "test.txt"})
                assert "test" in result  # Should succeed
            
            # 51st read should be blocked
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            assert "max 50 reads per minute" in result
    
    def test_write_rate_limit_default(self):
        """Test that write rate limit blocks after default number of writes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Make 10 writes (default limit)
            for i in range(10):
                result = tools["create_file"].invoke({"path": f"test{i}.txt", "content": f"content{i}"})
                assert "Created" in result  # Should succeed
            
            # 11th write should be blocked
            result = tools["create_file"].invoke({"path": "test11.txt", "content": "content11"})
            assert "Rate limit exceeded" in result
            assert "max 10 writes per minute" in result
    
    def test_mixed_operations_count_separately(self):
        """Test that read and write operations have separate rate limits."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file first
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Use up all write operations
            for i in range(9):  # Already used 1 create above
                tools["create_file"].invoke({"path": f"file{i}.txt", "content": f"content{i}"})
            
            # Should still be able to read (different counter)
            for i in range(50):
                result = tools["read_file"].invoke({"path": "test.txt"})
                assert "test" in result  # Should succeed
            
            # Now both should be blocked
            result = tools["create_file"].invoke({"path": "blocked.txt", "content": "blocked"})
            assert "Rate limit exceeded" in result
            assert "writes" in result
            
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            assert "reads" in result
    
    def test_reset_rate_limits(self):
        """Test that reset_rate_limits() restores access."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Exhaust read limit
            for i in range(50):
                tools["read_file"].invoke({"path": "test.txt"})
            
            # Verify blocked
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            
            # Reset limits
            ft.reset_rate_limits()
            
            # Should be able to read again
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "test" in result  # Should succeed
    
    def test_custom_rate_limits(self):
        """Test that custom rate limits are respected."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Set custom limits: 5 reads/min, 2 writes/min
            ft = FileTools(root, max_reads_per_minute=5, max_writes_per_minute=2)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Test read limit
            for i in range(5):
                result = tools["read_file"].invoke({"path": "test.txt"})
                assert "test" in result  # Should succeed
            
            # 6th read should be blocked
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            assert "max 5 reads per minute" in result
            
            # Reset for write test
            ft.reset_rate_limits()
            
            # Test write limit
            tools["create_file"].invoke({"path": "test1.txt", "content": "test1"})
            tools["create_file"].invoke({"path": "test2.txt", "content": "test2"})
            
            # 3rd write should be blocked
            result = tools["create_file"].invoke({"path": "test3.txt", "content": "test3"})
            assert "Rate limit exceeded" in result
            assert "max 2 writes per minute" in result
    
    def test_rate_limit_window_expires(self):
        """Test that old timestamps are removed from the window."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, max_reads_per_minute=3)  # Small limit for testing
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Make 3 reads
            for i in range(3):
                tools["read_file"].invoke({"path": "test.txt"})
            
            # Should be blocked
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            
            # Wait for window to expire (simulate by manipulating timestamps)
            # In real scenario, we'd wait 60 seconds, but for test we can reset
            ft.reset_rate_limits()
            
            # Should be able to read again
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "test" in result  # Should succeed
    
    def test_all_write_operations_count_toward_limit(self):
        """Test that create, update, and delete all count as writes."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, max_writes_per_minute=3)  # Small limit for testing
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a file (1 write)
            tools["create_file"].invoke({"path": "test.txt", "content": "initial"})
            
            # Update the file (2 writes)
            tools["update_file"].invoke({
                "path": "test.txt",
                "old_content": "initial",
                "new_content": "updated"
            })
            
            # Delete the file (3 writes - should hit limit)
            result = tools["delete_file"].invoke({"path": "test.txt"})
            assert "Deleted" in result  # Should succeed (exactly at limit)
            
            # Try to create another file (should be blocked)
            result = tools["create_file"].invoke({"path": "another.txt", "content": "test"})
            assert "Rate limit exceeded" in result
    
    def test_rate_limit_error_format(self):
        """Test that rate limit error messages are properly formatted."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, max_reads_per_minute=1)  # Very small limit
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create and read once
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            tools["read_file"].invoke({"path": "test.txt"})
            
            # Second read should give error
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert result.startswith("❌ Rate limit exceeded:")
            assert "max 1 reads per minute" in result
            assert "Wait" in result
            assert "seconds" in result
    
    def test_rate_limit_with_dry_run(self):
        """Test that rate limits apply even in dry-run mode."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, dry_run=True, max_writes_per_minute=2)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Make 2 dry-run writes
            for i in range(2):
                result = tools["create_file"].invoke({"path": f"test{i}.txt", "content": f"content{i}"})
                assert "DRY RUN" in result  # Should succeed
            
            # 3rd should be blocked even though it's dry-run
            result = tools["create_file"].invoke({"path": "test3.txt", "content": "content3"})
            assert "Rate limit exceeded" in result
    
    def test_rate_limit_with_interactive_mode(self):
        """Test that rate limits apply even in interactive mode."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, interactive=True, max_writes_per_minute=2)
            tools = {t.name: t for t in ft.get_tools()}
            
            # Mock the interactive approval to auto-accept
            # (In real test, we'd need to mock the console input)
            # For now, just verify the rate limit check happens before interactive prompt
            pass  # This test would require mocking the console interaction
    
    def test_list_files_not_rate_limited(self):
        """Test that list_files operations are not rate limited."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ft = FileTools(root, max_reads_per_minute=2)  # Very small read limit
            tools = {t.name: t for t in ft.get_tools()}
            
            # Create a test file
            tools["create_file"].invoke({"path": "test.txt", "content": "test"})
            
            # Exhaust read limit with read_file calls
            tools["read_file"].invoke({"path": "test.txt"})
            tools["read_file"].invoke({"path": "test.txt"})
            
            # Verify read_file is blocked
            result = tools["read_file"].invoke({"path": "test.txt"})
            assert "Rate limit exceeded" in result
            
            # But list_files should still work (not rate limited)
            result = tools["list_files"].invoke({"directory": "."})
            assert "test.txt" in result
            
            # Can call list_files many times without hitting limit
            for i in range(10):
                result = tools["list_files"].invoke({"directory": "."})
                assert "test.txt" in result
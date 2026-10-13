import pytest
from unittest.mock import MagicMock, patch
import json
import tempfile
import os
import sys

# Add root directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.verify_credentials import (
    UserVerificationResult,
    verify_single_user,
    load_credentials_file,
    verify_all_credentials,
    render_table,
    render_summary,
    main
)


# --- Unit Tests ---

def test_verify_single_user_success():
    raw_cred = {"email": "valid_user@tau.ac.il", "password": "good_password", "owner_id": "12345"}
    
    with patch("tools.verify_credentials.TauClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.login.return_value = True
        instance.get_csrf_token.return_value = "token_xyz"
        
        result = verify_single_user(1, raw_cred, check_bookings=False)
        
        assert result.is_valid is True
        assert result.status == "VALID"
        assert result.csrf_token_found is True
        assert result.active_bookings_count is None
        assert "Authenticated successfully" in result.details
        assert result.email == "valid_user@tau.ac.il"
        assert result.owner_id == "12345"


def test_verify_single_user_with_bookings():
    raw_cred = {"email": "booking_user@tau.ac.il", "password": "good_password", "owner_id": "12345"}
    
    with patch("tools.verify_credentials.TauClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.login.return_value = True
        instance.get_csrf_token.return_value = "token_xyz"
        instance.get_user_bookings.return_value = [{"id": "1"}, {"id": "2"}]
        
        result = verify_single_user(1, raw_cred, check_bookings=True)
        
        assert result.is_valid is True
        assert result.active_bookings_count == 2
        assert "2 active booking(s)" in result.details


def test_verify_single_user_failed_login():
    raw_cred = {"email": "bad_user@tau.ac.il", "password": "wrong_password", "owner_id": "99999"}
    
    with patch("tools.verify_credentials.TauClient") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.login.return_value = False
        instance.last_login_error = "We could not match your username or password"
        
        result = verify_single_user(2, raw_cred)
        
        assert result.is_valid is False
        assert result.status == "FAILED"
        assert result.csrf_token_found is False
        assert result.active_bookings_count is None
        assert result.details == "We could not match your username or password"


def test_verify_single_user_missing_credentials():
    # Missing email
    r1 = verify_single_user(1, {"email": "", "password": "secret", "owner_id": "100"})
    assert r1.status == "ERROR"
    assert "Missing email" in r1.details

    # Missing password
    r2 = verify_single_user(2, {"email": "user@test.com", "password": "", "owner_id": "100"})
    assert r2.status == "ERROR"
    assert "Missing password" in r2.details


def test_verify_single_user_exception_handling():
    raw_cred = {"email": "crash_user@tau.ac.il", "password": "pwd", "owner_id": "100"}
    
    with patch("tools.verify_credentials.TauClient") as mock_client_cls:
        mock_client_cls.side_effect = RuntimeError("Fatal network error")
        
        result = verify_single_user(1, raw_cred)
        
        assert result.is_valid is False
        assert result.status == "ERROR"
        assert "Fatal network error" in result.details


def test_load_credentials_file():
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
        json.dump([{"email": "u1", "password": "p1", "owner_id": "1"}], tf)
        temp_path = tf.name

    try:
        data = load_credentials_file(temp_path)
        assert len(data) == 1
        assert data[0]["email"] == "u1"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # File not found
    with pytest.raises(FileNotFoundError):
        load_credentials_file("non_existent_file_path_12345.json")

    # Invalid structure (not list)
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
        json.dump({"email": "u1"}, tf)
        temp_dict_path = tf.name

    try:
        with pytest.raises(ValueError):
            load_credentials_file(temp_dict_path)
    finally:
        if os.path.exists(temp_dict_path):
            os.remove(temp_dict_path)


def test_render_table_unicode_and_plain():
    results = [
        UserVerificationResult(
            index=1,
            email="valid@test.com",
            owner_id="111",
            is_valid=True,
            status="VALID",
            csrf_token_found=True,
            active_bookings_count=1,
            latency_seconds=0.5,
            details="Authenticated successfully"
        ),
        UserVerificationResult(
            index=2,
            email="invalid@test.com",
            owner_id="222",
            is_valid=False,
            status="FAILED",
            csrf_token_found=False,
            active_bookings_count=None,
            latency_seconds=1.2,
            details="Bad password"
        )
    ]

    # Plain ASCII
    table_plain = render_table(results, use_unicode=False, use_color=False)
    assert "+---+" in table_plain
    assert "[VALID]" in table_plain
    assert "[FAILED]" in table_plain
    assert "valid@test.com" in table_plain
    assert "invalid@test.com" in table_plain

    # Unicode
    table_unicode = render_table(results, use_unicode=True, use_color=False)
    assert "┌" in table_unicode
    assert "✓ VALID" in table_unicode
    assert "✗ FAILED" in table_unicode


def test_render_summary():
    results = [
        UserVerificationResult(1, "u1", "1", True, "VALID", True, None, 0.5, "OK"),
        UserVerificationResult(2, "u2", "2", False, "FAILED", False, None, 0.8, "Fail"),
    ]
    summary = render_summary(results, total_elapsed=1.3, workers=2, use_color=False)
    assert "Total Accounts Checked : 2" in summary
    assert "Valid / Active Accounts: 1 (50.0%)" in summary
    assert "Failed Accounts        : 1 (50.0%)" in summary
    assert "WARNING" in summary


# --- Integration Tests ---

def test_verify_all_credentials_integration():
    sample_data = [
        {"email": "user1", "password": "p1", "owner_id": "101"},
        {"email": "user2", "password": "p2", "owner_id": "102"},
        {"email": "user3", "password": "p3", "owner_id": "103"}
    ]
    
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
        json.dump(sample_data, tf)
        temp_path = tf.name

    try:
        with patch("tools.verify_credentials.verify_single_user") as mock_verify:
            def side_effect(idx, cred, check_bookings, timeout):
                is_val = (idx == 1)
                return UserVerificationResult(
                    index=idx,
                    email=cred["email"],
                    owner_id=cred["owner_id"],
                    is_valid=is_val,
                    status="VALID" if is_val else "FAILED",
                    csrf_token_found=is_val,
                    active_bookings_count=0 if is_val else None,
                    latency_seconds=0.1 * idx,
                    details="OK" if is_val else "Invalid credentials"
                )
            mock_verify.side_effect = side_effect

            # Test concurrent execution
            results, elapsed = verify_all_credentials(temp_path, max_workers=2)
            assert len(results) == 3
            assert [r.index for r in results] == [1, 2, 3]
            assert results[0].is_valid is True
            assert results[1].is_valid is False
            assert results[2].is_valid is False

            # Test sequential execution
            seq_results, seq_elapsed = verify_all_credentials(temp_path, max_workers=1)
            assert len(seq_results) == 3
            assert seq_results[0].email == "user1"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_cli_integration_json(capsys):
    sample_data = [
        {"email": "cli_user", "password": "p", "owner_id": "999"}
    ]
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tf:
        json.dump(sample_data, tf)
        temp_path = tf.name

    try:
        with patch("tools.verify_credentials.TauClient") as mock_client_cls:
            instance = mock_client_cls.return_value
            instance.login.return_value = True
            instance.get_csrf_token.return_value = "token_cli"
            
            test_args = ["verify_credentials.py", "--file", temp_path, "--json"]
            with patch.object(sys, "argv", test_args):
                with pytest.raises(SystemExit) as exc:
                    main()
                assert exc.value.code == 0

            captured = capsys.readouterr()
            payload = json.loads(captured.out)
            assert payload["total_accounts"] == 1
            assert payload["valid_accounts"] == 1
            assert payload["all_valid"] is True
            assert payload["results"][0]["email"] == "cli_user"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

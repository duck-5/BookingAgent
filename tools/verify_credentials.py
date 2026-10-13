#!/usr/bin/env python3
"""
Credentials Verification Tool

Verifies all user credentials configured in credentials.json against the TAU booking server
and outputs a clear, structured table with authentication status, CSRF token availability,
active reservations count, latency, and failure diagnostics.

Usage:
    python tools/verify_credentials.py
    python tools/verify_credentials.py --file path/to/credentials.json
    python tools/verify_credentials.py --check-bookings
    python tools/verify_credentials.py --json
    python tools/verify_credentials.py --plain
"""

import sys
import os
import json
import time
import argparse
import logging
import warnings
import concurrent.futures
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple

# Suppress noisy dependency warnings
warnings.filterwarnings("ignore")

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import config
from core.entities import UserCredentials
from clients.tau_client import TauClient

try:
    import colorama
    colorama.init()
    COLOR_SUPPORT = True
except Exception:
    COLOR_SUPPORT = False

# ANSI Color Codes
COLOR_GREEN = "\x1b[32m"
COLOR_RED = "\x1b[31m"
COLOR_YELLOW = "\x1b[33m"
COLOR_CYAN = "\x1b[36m"
COLOR_BOLD = "\x1b[1m"
COLOR_RESET = "\x1b[0m"


@dataclass
class UserVerificationResult:
    index: int
    email: str
    owner_id: str
    is_valid: bool
    status: str
    csrf_token_found: bool
    active_bookings_count: Optional[int]
    latency_seconds: float
    details: str


def verify_single_user(
    index: int,
    raw_cred: Dict[str, Any],
    check_bookings: bool = False,
    timeout: int = 20
) -> UserVerificationResult:
    """
    Verifies a single user's credentials against the TAU booking server.
    """
    email = str(raw_cred.get("email", "") or "").strip()
    password = str(raw_cred.get("password", "") or "")
    owner_id = str(raw_cred.get("owner_id", "") or "").strip()

    # Pre-validation checks
    if not email:
        return UserVerificationResult(
            index=index,
            email="(missing)",
            owner_id=owner_id or "N/A",
            is_valid=False,
            status="ERROR",
            csrf_token_found=False,
            active_bookings_count=None,
            latency_seconds=0.0,
            details="Missing email in credentials"
        )

    if not password:
        return UserVerificationResult(
            index=index,
            email=email,
            owner_id=owner_id or "N/A",
            is_valid=False,
            status="ERROR",
            csrf_token_found=False,
            active_bookings_count=None,
            latency_seconds=0.0,
            details="Missing password in credentials"
        )

    start_time = time.time()
    try:
        cred = UserCredentials(email=email, password=password, owner_id=owner_id)
        client = TauClient(cred)
        # Timeout override if provided
        client.session.timeout = timeout

        login_success = client.login()
        latency = time.time() - start_time

        if login_success:
            csrf_token = client.get_csrf_token()
            has_csrf = bool(csrf_token)
            
            bookings_count: Optional[int] = None
            if check_bookings:
                try:
                    today = datetime.now()
                    end_date = today + timedelta(days=8)
                    bookings = client.get_user_bookings(today, end_date)
                    bookings_count = len(bookings)
                except Exception:
                    bookings_count = 0

            msg_parts = ["Authenticated successfully"]
            if not has_csrf:
                msg_parts.append("CSRF token missing")
            if bookings_count is not None and bookings_count > 0:
                msg_parts.append(f"{bookings_count} active booking(s)")

            return UserVerificationResult(
                index=index,
                email=email,
                owner_id=owner_id or "N/A",
                is_valid=True,
                status="VALID",
                csrf_token_found=has_csrf,
                active_bookings_count=bookings_count,
                latency_seconds=round(latency, 2),
                details="; ".join(msg_parts)
            )
        else:
            err_detail = client.last_login_error or "Invalid credentials or session"
            return UserVerificationResult(
                index=index,
                email=email,
                owner_id=owner_id or "N/A",
                is_valid=False,
                status="FAILED",
                csrf_token_found=False,
                active_bookings_count=None,
                latency_seconds=round(latency, 2),
                details=err_detail
            )

    except Exception as e:
        latency = time.time() - start_time
        return UserVerificationResult(
            index=index,
            email=email,
            owner_id=owner_id or "N/A",
            is_valid=False,
            status="ERROR",
            csrf_token_found=False,
            active_bookings_count=None,
            latency_seconds=round(latency, 2),
            details=f"Exception: {str(e)}"
        )


def load_credentials_file(file_path: str) -> List[Dict[str, Any]]:
    """
    Loads and validates the credentials JSON file.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Credentials file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON list of credential objects in {file_path}, got {type(data).__name__}")

    return data


def verify_all_credentials(
    credentials_path: str,
    max_workers: int = 8,
    check_bookings: bool = False,
    timeout: int = 20
) -> Tuple[List[UserVerificationResult], float]:
    """
    Verifies all credentials concurrently or sequentially.
    Returns (results, total_elapsed_time).
    """
    cred_list = load_credentials_file(credentials_path)
    total_start = time.time()

    if not cred_list:
        return [], 0.0

    results: List[UserVerificationResult] = []

    if max_workers <= 1 or len(cred_list) == 1:
        # Sequential execution
        for i, raw_cred in enumerate(cred_list, 1):
            res = verify_single_user(i, raw_cred, check_bookings=check_bookings, timeout=timeout)
            results.append(res)
    else:
        # Concurrent execution
        workers = min(max_workers, len(cred_list))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_index = {
                executor.submit(verify_single_user, i, cred, check_bookings, timeout): i
                for i, cred in enumerate(cred_list, 1)
            }
            for future in concurrent.futures.as_completed(future_to_index):
                res = future.result()
                results.append(res)

    results.sort(key=lambda r: r.index)
    total_elapsed = time.time() - total_start
    return results, total_elapsed


def render_table(
    results: List[UserVerificationResult],
    use_unicode: bool = True,
    use_color: bool = True
) -> str:
    """
    Renders a clear tabular view of verification results without external dependencies.
    """
    if not results:
        return "No credentials found to verify."

    # Column definitions: (Header, Key, Align)
    headers = [
        ("#", "index", "right"),
        ("Username / Email", "email", "left"),
        ("Owner ID", "owner_id", "left"),
        ("Login Status", "status", "center"),
        ("CSRF Token", "csrf", "center"),
        ("Active Bookings", "bookings", "center"),
        ("Latency", "latency", "right"),
        ("Details / Diagnostics", "details", "left"),
    ]

    # Format cell raw text & display text (with colors)
    rows_raw = []
    rows_colored = []

    for r in results:
        # Status display
        if r.status == "VALID":
            status_symbol = "✓ VALID" if use_unicode else "[VALID]"
            colored_status = f"{COLOR_GREEN}{status_symbol}{COLOR_RESET}" if use_color else status_symbol
        elif r.status == "FAILED":
            status_symbol = "✗ FAILED" if use_unicode else "[FAILED]"
            colored_status = f"{COLOR_RED}{status_symbol}{COLOR_RESET}" if use_color else status_symbol
        else:
            status_symbol = "! ERROR" if use_unicode else "[ERROR]"
            colored_status = f"{COLOR_YELLOW}{status_symbol}{COLOR_RESET}" if use_color else status_symbol

        # CSRF display
        if r.csrf_token_found:
            csrf_text = "✓ Found" if use_unicode else "Yes"
            colored_csrf = f"{COLOR_GREEN}{csrf_text}{COLOR_RESET}" if use_color else csrf_text
        else:
            csrf_text = "✗ Missing" if use_unicode else "No"
            colored_csrf = f"{COLOR_RED}{csrf_text}{COLOR_RESET}" if use_color else csrf_text

        # Bookings display
        if r.active_bookings_count is not None:
            bookings_text = str(r.active_bookings_count)
        else:
            bookings_text = "-"

        latency_text = f"{r.latency_seconds:.2f}s"

        row_dict_raw = {
            "index": str(r.index),
            "email": r.email,
            "owner_id": r.owner_id,
            "status": status_symbol,
            "csrf": csrf_text,
            "bookings": bookings_text,
            "latency": latency_text,
            "details": r.details,
        }

        row_dict_colored = {
            "index": str(r.index),
            "email": r.email,
            "owner_id": r.owner_id,
            "status": colored_status,
            "csrf": colored_csrf,
            "bookings": bookings_text,
            "latency": latency_text,
            "details": r.details,
        }

        rows_raw.append(row_dict_raw)
        rows_colored.append(row_dict_colored)

    # Calculate column widths based on maximum raw length
    col_widths = {}
    for h_name, key, _ in headers:
        max_len = len(h_name)
        for row in rows_raw:
            val_len = len(str(row[key]))
            if val_len > max_len:
                max_len = val_len
        col_widths[key] = max_len + 2  # 2 padding spaces

    # Table border styles
    if use_unicode:
        TL, TM, TR = "┌", "┬", "┐"
        ML, MM, MR = "├", "┼", "┤"
        BL, BM, BR = "└", "┴", "┘"
        H, V = "─", "│"
    else:
        TL, TM, TR = "+", "+", "+"
        ML, MM, MR = "+", "+", "+"
        BL, BM, BR = "+", "+", "+"
        H, V = "-", "|"

    def build_border(left, mid, right):
        parts = [H * col_widths[key] for _, key, _ in headers]
        return left + mid.join(parts) + right

    def format_row(data_row, is_header=False):
        cells = []
        for h_name, key, align in headers:
            w = col_widths[key]
            text = h_name if is_header else data_row[key]
            raw_text = h_name if is_header else rows_raw[data_row["_idx"]][key]
            raw_len = len(raw_text)
            pad = w - raw_len

            if align == "center":
                left_pad = pad // 2
                right_pad = pad - left_pad
                cell_str = (" " * left_pad) + text + (" " * right_pad)
            elif align == "right":
                cell_str = (" " * (pad - 1)) + text + " "
            else:  # left
                cell_str = " " + text + (" " * (pad - 1))

            cells.append(cell_str)
        return V + V.join(cells) + V

    lines = []
    lines.append(build_border(TL, TM, TR))
    lines.append(format_row({}, is_header=True))
    lines.append(build_border(ML, MM, MR))

    for idx, colored_row in enumerate(rows_colored):
        colored_row["_idx"] = idx
        lines.append(format_row(colored_row, is_header=False))

    lines.append(build_border(BL, BM, BR))
    return "\n".join(lines)


def render_summary(
    results: List[UserVerificationResult],
    total_elapsed: float,
    workers: int,
    use_color: bool = True
) -> str:
    """
    Renders a summary section of the verification run.
    """
    total = len(results)
    valid_count = sum(1 for r in results if r.is_valid)
    failed_count = sum(1 for r in results if not r.is_valid and r.status == "FAILED")
    error_count = sum(1 for r in results if r.status == "ERROR")

    valid_pct = (valid_count / total * 100) if total > 0 else 0.0
    failed_pct = (failed_count / total * 100) if total > 0 else 0.0

    sep = "=" * 80
    lines = [
        sep,
        "VERIFICATION SUMMARY",
        sep,
        f"Total Accounts Checked : {total}",
        f"Valid / Active Accounts: {valid_count} ({valid_pct:.1f}%)",
        f"Failed Accounts        : {failed_count} ({failed_pct:.1f}%)",
    ]
    if error_count > 0:
        lines.append(f"Format/System Errors   : {error_count}")

    lines.append(f"Total Execution Time   : {total_elapsed:.2f}s ({workers} worker threads)")

    if valid_count == total and total > 0:
        status_msg = "SUCCESS - All user credentials are verified and active."
        if use_color:
            status_msg = f"{COLOR_GREEN}{status_msg}{COLOR_RESET}"
    elif valid_count > 0:
        status_msg = f"WARNING - {total - valid_count} of {total} accounts failed authentication!"
        if use_color:
            status_msg = f"{COLOR_YELLOW}{status_msg}{COLOR_RESET}"
    else:
        status_msg = "FAILURE - All accounts failed authentication!"
        if use_color:
            status_msg = f"{COLOR_RED}{status_msg}{COLOR_RESET}"

    lines.append(f"Overall Status         : {status_msg}")
    lines.append(sep)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Verify all user credentials from credentials.json against the TAU server."
    )
    parser.add_argument(
        "-f", "--file",
        default=config.CREDENTIALS_FILE,
        help=f"Path to credentials JSON file (default: {config.CREDENTIALS_FILE})"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=8,
        help="Number of concurrent worker threads (default: 8; 1 for sequential)"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=20,
        help="Timeout in seconds per network request (default: 20)"
    )
    parser.add_argument(
        "-b", "--check-bookings",
        action="store_true",
        help="Also query and display active university bookings for valid accounts"
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable Unicode characters and colors (standard ASCII output)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results in JSON format"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed internal logs during verification"
    )

    args = parser.parse_args()

    # Configure logging verbosity
    log_level = logging.DEBUG if args.verbose else logging.ERROR
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger("clients.tau_client").setLevel(log_level)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.ERROR)

    # Determine unicode and color support
    use_unicode = not args.plain and (sys.stdout.encoding or "").lower().startswith("utf")
    use_color = COLOR_SUPPORT and not args.plain and sys.stdout.isatty()

    try:
        results, total_elapsed = verify_all_credentials(
            credentials_path=args.file,
            max_workers=args.workers,
            check_bookings=args.check_bookings,
            timeout=args.timeout
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Error reading credentials: {e}", file=sys.stderr)
        sys.exit(2)

    if args.json:
        output_payload = {
            "credentials_file": args.file,
            "total_accounts": len(results),
            "valid_accounts": sum(1 for r in results if r.is_valid),
            "failed_accounts": sum(1 for r in results if not r.is_valid),
            "execution_time_seconds": round(total_elapsed, 2),
            "all_valid": all(r.is_valid for r in results),
            "results": [asdict(r) for r in results]
        }
        print(json.dumps(output_payload, indent=2))
    else:
        table = render_table(results, use_unicode=use_unicode, use_color=use_color)
        summary = render_summary(results, total_elapsed, args.workers, use_color=use_color)
        print("\n" + table)
        print("\n" + summary + "\n")

    # Exit code: 0 if all valid, 1 if any failed
    all_valid = len(results) > 0 and all(r.is_valid for r in results)
    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Convenience entry point for tools/verify_credentials.py
"""
import sys
import os

# Ensure tools directory can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.verify_credentials import main

if __name__ == "__main__":
    main()

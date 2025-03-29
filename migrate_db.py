#!/usr/bin/env python3

"""
Script to run database migrations manually.
Run this script to update the database schema before starting the application.

Usage:
  python migrate_db.py
"""

import logging
from app.db_migration import run_migrations

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    print("Running database migrations...")
    run_migrations()
    print("Database migrations completed.")

#!/usr/bin/env python3

import sqlite3
import os
import logging
from app.config import settings

logger = logging.getLogger(__name__)

def run_migrations():
    """
    Run database migrations to add missing columns or make other schema changes.
    """
    logger.info("Running database migrations...")
    
    # Parse the DATABASE_URL to get the SQLite database path
    db_url = settings.database_url
    if not db_url.startswith("sqlite:///"):
        logger.warning(f"Non-SQLite database detected: {db_url}. Migrations may need to be adapted.")
        return
    
    # Extract the database path from the URL
    db_path = db_url.replace("sqlite:///", "")
    if not os.path.exists(db_path):
        logger.error(f"Database file not found at {db_path}")
        return
    
    # Connect to the database
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if the processing_logs table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='processing_logs';")
        if not cursor.fetchone():
            logger.info("Creating processing_logs table...")
            cursor.execute("""
                CREATE TABLE processing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER,
                    step_name VARCHAR,
                    status VARCHAR,
                    message TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (file_id) REFERENCES files (id)
                );
            """)
            conn.commit()
        
        # Check if the task_id column exists in processing_logs
        cursor.execute("PRAGMA table_info(processing_logs);")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'task_id' not in columns:
            logger.info("Adding task_id column to processing_logs table...")
            cursor.execute("ALTER TABLE processing_logs ADD COLUMN task_id VARCHAR;")
            conn.commit()
            logger.info("Created task_id column in processing_logs")
            
            # Create an index on task_id for faster lookups
            cursor.execute("CREATE INDEX idx_processing_logs_task_id ON processing_logs (task_id);")
            conn.commit()
            logger.info("Created index on task_id column")
        
        logger.info("Database migrations completed successfully.")
    except Exception as e:
        logger.error(f"Error during database migration: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    run_migrations()

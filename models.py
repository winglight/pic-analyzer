from datetime import datetime
import json
import sqlite3
from contextlib import contextmanager

DATABASE_PATH = 'tasks.db'

def init_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP,
            result TEXT,
            error TEXT,
            telegram_sent BOOLEAN DEFAULT FALSE
        )
        ''')
        conn.commit()

@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

class TaskDB:
    @staticmethod
    def create_task(task_id: str, file_path: str) -> None:
        with get_db() as conn:
            conn.execute(
                'INSERT INTO tasks (id, file_path, status, created_at) VALUES (?, ?, ?, ?)',
                (task_id, file_path, 'processing', datetime.now())
            )
            conn.commit()

    @staticmethod
    def update_task(task_id, status, result=None, error=None, telegram_sent=None):
        """Update task status and result"""
        with get_db() as db:
            cursor = db.cursor()
            cursor.execute('''
                UPDATE tasks 
                SET status = ?, 
                    result = ?, 
                    error = ?,
                    telegram_sent = ?,
                    updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (status, json.dumps(result) if result else None, 
                error, telegram_sent, task_id))
            db.commit()

    @staticmethod
    def get_tasks(status: str = None, page: int = 1, per_page: int = 10) -> tuple:
        offset = (page - 1) * per_page
        with get_db() as conn:
            if status:
                query = '''
                    SELECT * FROM tasks 
                    WHERE status = ? 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                '''
                params = (status, per_page, offset)
                count_query = 'SELECT COUNT(*) FROM tasks WHERE status = ?'
                count_params = (status,)
            else:
                query = '''
                    SELECT * FROM tasks 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                '''
                params = (per_page, offset)
                count_query = 'SELECT COUNT(*) FROM tasks'
                count_params = ()

            tasks = [dict(row) for row in conn.execute(query, params).fetchall()]
            total = conn.execute(count_query, count_params).fetchone()[0]
            
            return tasks, total
        
    @staticmethod
    def get_task(task_id):
        """Get single task by id"""
        with get_db() as db:
            cursor = db.cursor()
            cursor.execute('''
                SELECT id, status, result, error, file_path, telegram_sent,
                    created_at, updated_at 
                FROM tasks 
                WHERE id = ?
            ''', (task_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0],
                    'status': row[1],
                    'result': json.loads(row[2]) if row[2] else None,
                    'error': row[3],
                    'file_path': row[4],
                    'telegram_sent': row[5],
                    'created_at': row[6],
                    'updated_at': row[7]
                }
            return None
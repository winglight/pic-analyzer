from datetime import datetime
import json
import sqlite3
from contextlib import contextmanager

DATABASE_PATH = 'tasks.db'

def init_db():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            result TEXT,
            error TEXT
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
                'INSERT INTO tasks (task_id, file_path, status, created_at) VALUES (?, ?, ?, ?)',
                (task_id, file_path, 'processing', datetime.now())
            )
            conn.commit()

    @staticmethod
    def update_task(task_id: str, status: str, result: str = None, error: str = None) -> None:
        with get_db() as conn:
            conn.execute(
                'UPDATE tasks SET status = ?, result = ?, error = ? WHERE task_id = ?',
                (status, result and json.dumps(result), error, task_id)
            )
            conn.commit()

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
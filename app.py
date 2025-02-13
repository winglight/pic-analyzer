from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import os
from datetime import datetime, timedelta
import secrets
from dotenv import load_dotenv
import base64
import json
import asyncio
import aiohttp
from multiprocessing import Process, Manager
import uuid
import gc
from models import init_db, TaskDB
from telegram.ext import ApplicationBuilder

load_dotenv()  # Load environment variables

OLLAMA_API_URL = os.getenv('OLLAMA_API_URL')
MODEL = os.getenv('MODEL')
PROMPT_GREEN = os.getenv('PROMPT_GREEN')
PROMPT_RED = os.getenv('PROMPT_RED')
UPLOAD_DIR = os.getenv('UPLOAD_DIR')
VALID_TOKEN = os.getenv('VALID_TOKEN')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
# 修复CORS配置，使用列表指定来源
CORS(app, resources={r"/*": {
    "origins": ["https://www.tradingview.com"],  # 使用列表形式
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Authorization"]
}})

manager = Manager()
tasks_status = manager.dict()

init_db()

async def call_ollama_api(base64_image, prompt):
    """
    Async function to call Ollama API
    """
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "images": [base64_image]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            OLLAMA_API_URL,
            json=payload,
            headers={'Content-Type': 'application/json'}
        ) as response:
            content = await response.text()
            return json.loads(json.loads(content)["response"])

async def process_image(base64_image):
    """
    Process image with both green and red prompts
    """
    tasks = [
        call_ollama_api(base64_image, PROMPT_GREEN),
        call_ollama_api(base64_image, PROMPT_RED)
    ]
    green_result, red_result = await asyncio.gather(*tasks)
    return {'green': green_result, 'red': red_result}

async def send_telegram_message(result, file_path):
    """Send analysis results to Telegram if conditions are met"""
    try:
        # Parse green and red results
        green_result = json.loads(result['green'])
        red_result = json.loads(result['red'])
        
        # Check conditions
        conditions = {
            'condition1': green_result.get('condition1', False),
            'condition2': green_result.get('condition2', False),
            'condition3': green_result.get('condition3', False)
        }
        
        # Count true conditions
        true_conditions = sum(conditions.values())
        
        # Only send message if 2 or more conditions are true
        if true_conditions >= 2:
            app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
            
            # Prepare message text
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            message = f"🔄 New Analysis Result ({timestamp})\n\n"
            message += "✅ Conditions Met:\n"
            for condition, is_true in conditions.items():
                emoji = "✓" if is_true else "✗"
                message += f"{emoji} {condition}\n"
            message += f"\n📊 Green Analysis:\n{json.dumps(green_result, indent=2)}\n\n"
            message += f"📊 Red Analysis:\n{json.dumps(red_result, indent=2)}\n\n"
            message += f"🖼 Image: {os.path.basename(file_path)}"
            
            # Send message and image
            async with app:
                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
                with open(file_path, 'rb') as photo:
                    await app.bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=photo)
            return True
        else:
            print(f"Message not sent: Only {true_conditions} conditions met")
            return False
            
    except Exception as e:
        print(f"Failed to send Telegram message: {str(e)}")
        return False

def cleanup_old_tasks():
    """清理超过30分钟的旧任务"""
    current_time = datetime.now()
    expired_tasks = [
        task_id for task_id, task in tasks_status.items()
        if current_time - datetime.fromisoformat(task['created_at']) > timedelta(minutes=30)
    ]
    for task_id in expired_tasks:
        if task_id in tasks_status:
            del tasks_status[task_id]

def run_async_task(task_id, base64_image, file_path):
    """在新进程中运行异步任务"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(process_image(base64_image))
        TaskDB.update_task(task_id, 'completed', result=result)
        # Send telegram message
        loop.run_until_complete(send_telegram_message(result, file_path))
    except Exception as e:
        TaskDB.update_task(task_id, 'failed', error=str(e))
    finally:
        loop.close()
        gc.collect()  # 清理内存

@app.route('/upload', methods=['POST', 'OPTIONS'])
def upload_screenshot():
    if request.method == 'OPTIONS':
        response = jsonify({'message': 'Preflight request'})
        response.headers.add('Access-Control-Allow-Origin', 'https://www.tradingview.com')
        response.headers.add('Access-Control-Allow-Headers', 'Authorization')
        return response, 200

    try:
        # Validate token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'No token provided'}), 401
        
        token = auth_header.split(' ')[1]
        if token != VALID_TOKEN:
            return jsonify({'error': 'Invalid token'}), 401

        # Validate file
        if 'screenshot' not in request.files:
            return jsonify({'error': 'No file provided'}), 400

        file = request.files['screenshot']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Save file
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        random_hex = secrets.token_hex(4)
        filename = f'screenshot_{timestamp}_{random_hex}.png'
        file_path = os.path.join(UPLOAD_DIR, filename)
        file.save(file_path)

        # Read and encode file
        with open(file_path, 'rb') as f:
            base64_image = base64.b64encode(f.read()).decode('utf-8')

        # Save task to database
        task_id = str(uuid.uuid4())
        TaskDB.create_task(task_id, file_path)

        # Start processing in new process
        cleanup_old_tasks()  # 清理旧任务
        process = Process(target=run_async_task, args=(task_id, base64_image, file_path))
        process.daemon = True  # 设置为守护进程
        process.start()

        # Return immediate response with task ID
        return jsonify({
            'task_id': task_id,
            'status': 'processing',
            'message': 'Image processing started'
        })

    except Exception as e:
        response = jsonify({'error': str(e)})
        response.headers.add('Access-Control-Allow-Origin', 'https://www.tradingview.com')
        return response, 500

@app.route('/task/<task_id>', methods=['GET'])
def check_task_status(task_id):
    """检查任务状态的路由"""
    if task_id not in tasks_status:
        return jsonify({'error': 'Task not found'}), 404
    
    task = tasks_status[task_id]
    return jsonify({
        'status': task['status'],
        'result': task.get('result') if task['status'] == 'completed' else None,
        'error': task.get('error') if task['status'] == 'failed' else None
    })

@app.route('/tasks', methods=['GET'])
def list_tasks():
    """List tasks with pagination and status filter"""
    try:
        status = request.args.get('status')
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))

        tasks, total = TaskDB.get_tasks(status, page, per_page)
        
        return jsonify({
            'tasks': tasks,
            'total': total,
            'page': page,
            'per_page': per_page,
            'total_pages': (total + per_page - 1) // per_page
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/image/<path:file_path>', methods=['GET'])
def get_image(file_path):
    """Return image file by file path"""
    try:
        full_path = os.path.join(UPLOAD_DIR, os.path.basename(file_path))
        if not os.path.exists(full_path):
            return jsonify({'error': 'Image not found'}), 404
        return send_file(full_path, mimetype='image/png')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5005, debug=True)
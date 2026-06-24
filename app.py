import os
import re
import threading
import psycopg2
import mysql.connector
import logging
import subprocess
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

from flask import Flask, render_template, send_from_directory, flash, redirect, url_for, request, jsonify, session, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import safe_join
from flask_wtf.csrf import CSRFProtect

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from flask_apscheduler import APScheduler

from services.whatsapp_client import send_whatsapp_notification

# IMPORTANDO AS CONSULTAS SEPARADAS
import database.queries as queries

load_dotenv()

app = Flask(__name__)

if not os.getenv('SECRET_KEY'):
    raise RuntimeError("A variável SECRET_KEY precisa estar definida no arquivo .env!")
app.secret_key = os.getenv('SECRET_KEY')

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=False,  # Mude para True se usar HTTPS em produção
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2)
)

csrf = CSRFProtect(app)

BACKUP_HOUR = os.getenv('BACKUP_HOUR', '15')
BACKUP_MINUTE = os.getenv('BACKUP_MINUTE', '39')

env_backup_path = os.getenv('BACKUP_DIR', os.path.join(os.getcwd(), "backups"))
BACKUP_DIR = os.path.abspath(env_backup_path)

if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

raw_key = os.getenv('ENCRYPTION_KEY')
if not raw_key:
    raise RuntimeError("A variável ENCRYPTION_KEY precisa estar definida no arquivo .env!")

ENCRYPTION_KEY = raw_key.encode()
cipher_suite = Fernet(ENCRYPTION_KEY)

DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'database': os.getenv('DB_NAME'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASS'),
    'port': os.getenv('DB_PORT', '5432')
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("backup.log"), logging.StreamHandler()]
)

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

active_manual_backups = {}
backup_lock = threading.Lock()

DB_CACHE = {}
DB_CACHE_TTL = 300


@scheduler.task('interval', id='reap_zombie_processes', minutes=10)
def reap_zombie_processes():
    with backup_lock:
        to_remove = []
        for db_id, backup_data in active_manual_backups.items():
            proc = backup_data["process"]
            if proc.poll() is not None:
                proc.communicate()
                to_remove.append(db_id)
                logging.info(f"🧹 Processo abandonado do banco {backup_data['db_name']} coletado e limpo com sucesso.")

        for db_id in to_remove:
            active_manual_backups.pop(db_id, None)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def is_valid_db_name(name):
    return re.match(r'^[a-zA-Z0-9_-]+$', name) is not None


def encrypt_password(password):
    return cipher_suite.encrypt(password.encode()).decode()


def decrypt_password(encrypted_password):
    return cipher_suite.decrypt(encrypted_password.encode()).decode()


def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def clean_old_backups():
    now = datetime.now()
    limite = now - timedelta(days=3)
    for root, dirs, files in os.walk(BACKUP_DIR):
        for file in files:
            if file.endswith((".backup", ".backup.gz", ".sql", ".sql.gz")):
                fp = os.path.join(root, file)
                if datetime.fromtimestamp(os.path.getmtime(fp)) < limite:
                    try:
                        os.remove(fp)
                    except OSError as e:
                        logging.error(f"Erro ao deletar arquivo antigo {fp}: {e}")


def get_db_connection(target_db=None):
    config = DB_CONFIG.copy()
    if target_db:
        config['database'] = target_db
    return psycopg2.connect(**config)


def init_db():
    queries.init_database_structure(get_db_connection, DB_CONFIG['database'])


def load_servers():
    return queries.get_all_backup_servers(get_db_connection, decrypt_password)


def add_server_to_db(srv):
    if srv['db_type'] == 'postgres':
        with psycopg2.connect(
                host=srv['host'], port=srv['port'], user=srv['user'],
                password=srv['password'], database="postgres", connect_timeout=3
        ) as test_conn:
            pass
    elif srv['db_type'] == 'mysql':
        with mysql.connector.connect(
                host=srv['host'], port=int(srv['port']), user=srv['user'],
                password=srv['password'], connect_timeout=3
        ) as test_conn:
            pass

    encrypted_pw = encrypt_password(srv['password'])
    queries.insert_backup_server(
        get_db_connection,
        srv['host'], srv['port'], srv['user'], encrypted_pw, srv['db_type']
    )
    DB_CACHE.clear()


def remove_server_from_db(srv_id):
    queries.delete_backup_server(get_db_connection, srv_id)
    DB_CACHE.clear()


def get_databases_from_server(srv):
    cache_key = f"{srv['host']}_{srv['port']}"
    now = time.time()

    if cache_key in DB_CACHE and (now - DB_CACHE[cache_key]['timestamp'] < DB_CACHE_TTL):
        return DB_CACHE[cache_key]['data']

    result = []
    try:
        if srv['db_type'] == 'postgres':
            # 1. TENTA TRATAR COMO PGBOUNCER PRIMEIRO (Banco virtual 'pgbouncer')
            try:
                # Conexão sem o 'with' para evitar que inicie uma transação e envie 'BEGIN'
                conn = psycopg2.connect(
                        host=srv['host'], port=srv['port'], user=srv['user'],
                        password=srv['password'], database="pgbouncer", connect_timeout=3
                )
                conn.autocommit = True  # Isto impede o envio do comando BEGIN

                try:
                    with conn.cursor() as cur:
                        cur.execute("SHOW DATABASES;")
                        raw_bouncer_dbs = cur.fetchall()
                        result = [row[0] for row in raw_bouncer_dbs if row[0] not in ('postgres', 'pgbouncer')]
                        logging.info(f"Bancos descobertos via PgBouncer em {srv['host']}:{srv['port']}")
                finally:
                    conn.close() # Garante o fecho da conexão corretamente

            except Exception as bouncer_err:
                logging.info(f"Não foi possível listar via PgBouncer em {srv['host']} ({bouncer_err}). Tentando catálogo nativo...")
                result = []

            # 2. SE NÃO RETORNOU NADA, FAZ O FALLBACK PARA O POSTGRES NATIVO
            if not result:
                with psycopg2.connect(
                        host=srv['host'], port=srv['port'], user=srv['user'],
                        password=srv['password'], database="postgres", connect_timeout=3
                ) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false AND datname NOT IN ('postgres','browser_db');")
                        result = [row[0] for row in cur.fetchall()]
                        logging.info(f"Bancos descobertos via catálogo nativo em {srv['host']}:{srv['port']}")

        elif srv['db_type'] == 'mysql':
            with mysql.connector.connect(
                    host=srv['host'], port=int(srv['port']), user=srv['user'],
                    password=srv['password'], connect_timeout=3
            ) as conn:
                with conn.cursor() as cur:
                    cur.execute("SHOW DATABASES;")
                    raw_dbs = cur.fetchall()
                    system_dbs = ('information_schema', 'mysql', 'performance_schema', 'sys')
                    result = [row[0] for row in raw_dbs if row[0] not in system_dbs]

        DB_CACHE[cache_key] = {'timestamp': now, 'data': result}
    except Exception as e:
        logging.error(f"Erro ao listar DBs em {srv['host']} ({srv['db_type']}): {e}")

    return result


def ejecutar_backup_unico(item):
    db_name = item['db']
    if not is_valid_db_name(db_name):
        logging.error(f"Nome do banco inválido ignorado: {db_name}")
        return False

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_folder = os.path.join(BACKUP_DIR, db_name)
    os.makedirs(db_folder, exist_ok=True)

    env = os.environ.copy()

    if item['db_type'] == 'postgres':
        backup_filepath = os.path.join(db_folder, f"{db_name}_{timestamp}_AUTO.backup")
        env['PGPASSWORD'] = item['password']
        cmd = ['pg_dump', '-h', item['host'], '-p', str(item['port']), '-U', item['user'], '-F', 'c', '-f', backup_filepath, db_name]
    else:
        backup_filepath = os.path.join(db_folder, f"{db_name}_{timestamp}_AUTO.sql")
        env['MYSQL_PWD'] = item['password']
        cmd = ['mysqldump', '-h', item['host'], '-P', str(item['port']), '-u', item['user'], f"--result-file={backup_filepath}", db_name]

    try:
        start_time = time.time()
        subprocess.run(cmd, env=env, check=True)
        duration = time.time() - start_time
        logging.info(f"Sucesso: {db_name} ({item['host']}) | Tempo: {duration:.2f}s")

        msg = f"*Backup Automático Concluído ({item['db_type'].upper()})*\n\n*Banco:* {db_name}\n*Host:* {item['host']}\n*Duração:* {duration:.2f}s"
        send_whatsapp_notification(msg)
        return True
    except Exception as e:
        logging.error(f"Erro no backup automático de {db_name}: {e}")
        if os.path.exists(backup_filepath):
            os.remove(backup_filepath)
        msg = f"*Falha no Backup Automático*\n\n*Banco:* {db_name}\n*Host:* {item['host']}\n*Erro:* {str(e)}"
        send_whatsapp_notification(msg)
        return False


def processar_backups_em_lotes():
    with app.app_context():
        logging.info("INICIANDO BACKUP AUTOMÁTICO DIÁRIO")
        servers = load_servers()
        lista_tarefas = []

        for srv in servers:
            dbs = get_databases_from_server(srv)
            for db in dbs:
                lista_tarefas.append({
                    'host': srv['host'], 'port': srv['port'], 'user': srv['user'],
                    'password': srv['password'], 'db': db, 'db_type': srv['db_type']
                })

        max_workers = 4
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futuros = [executor.submit(ejecutar_backup_unico, item) for item in lista_tarefas]
            for futuro in as_completed(futuros):
                futuro.result()

        clean_old_backups()


@scheduler.task('cron', id='do_backup_diario', hour=BACKUP_HOUR, minute=BACKUP_MINUTE)
def scheduled_backup():
    processar_backups_em_lotes()


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        try:
            user = queries.get_user_by_username(get_db_connection, username)

            if user and check_password_hash(user['password_hash'], password):
                session['user_id'] = user['id']
                session['username'] = user['username']
                return redirect(url_for('index'))
            else:
                flash("Usuário ou senha incorretos.", "danger")
        except Exception as e:
            logging.error(f"Erro no login: {e}")
            flash("Erro interno ao processar autenticação.", "danger")

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash("Sessão encerrada com sucesso.", "info")
    return redirect(url_for('login'))


@app.route('/register-user', methods=['POST'])
@login_required
def register_user():
    username = request.form.get('new_username', '').strip()
    password = request.form.get('new_password', '')

    if not username or not password:
        flash("Preencha todos os campos para efetuar o cadastro.", "danger")
        return redirect(url_for('index'))

    try:
        if queries.check_user_exists(get_db_connection, username):
            flash(f"O usuário '{username}' já está cadastrado.", "danger")
        else:
            password_hash = generate_password_hash(password)
            queries.insert_user(get_db_connection, username, password_hash)
            flash(f"Usuário '{username}' cadastrado com sucesso!", "success")
    except Exception as e:
        logging.error(f"Erro ao cadastrar usuário: {e}")
        flash("Erro interno ao salvar novo usuário.", "danger")

    return redirect(url_for('index'))


@app.route('/change-password', methods=['POST'])
@login_required
def change_password():
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        flash("Todos os campos são obrigatórios para alterar a senha.", "danger")
        return redirect(url_for('index'))

    if new_password != confirm_password:
        flash("A nova senha e a confirmação não coincidem.", "danger")
        return redirect(url_for('index'))

    user_id = session['user_id']
    try:
        user = queries.get_user_password_hash_by_id(get_db_connection, user_id)

        if not user or not check_password_hash(user['password_hash'], current_password):
            flash("Senha atual incorreta.", "danger")
            return redirect(url_for('index'))

        new_password_hash = generate_password_hash(new_password)
        queries.update_user_password(get_db_connection, user_id, new_password_hash)

        flash("Sua senha foi alterada com sucesso!", "success")
    except Exception as e:
        logging.error(f"Erro ao alterar a própria senha: {e}")
        flash("Erro interno ao tentar atualizar a senha.", "danger")

    return redirect(url_for('index'))


@app.route('/')
@login_required
def index():
    servers = load_servers()
    all_dbs = []

    for srv in servers:
        dbs = get_databases_from_server(srv)
        for db in dbs:
            all_dbs.append({
                'server_label': f"{srv['host']}:{srv['port']}",
                'db_name': db,
                'db_type': srv['db_type'],
                'id': f"{srv['id']}|{db}"
            })

    backups_list = []
    for root, dirs, files in os.walk(BACKUP_DIR):
        for file in files:
            if file.endswith((".backup", ".backup.gz", ".sql", ".sql.gz")):
                fp = os.path.join(root, file)
                rel_path = os.path.relpath(fp, BACKUP_DIR).replace("\\", "/")
                tipo_ext = 'mysql' if file.endswith(('.sql', '.sql.gz')) else 'postgres'
                backups_list.append({
                    'name': file, 'path': rel_path, 'db': os.path.basename(root),
                    'size': format_size(os.path.getsize(fp)), 'db_type': tipo_ext,
                    'date': datetime.fromtimestamp(os.path.getmtime(fp)).strftime('%d/%m/%Y %H:%M'),
                    'mtime': os.path.getmtime(fp)
                })
    backups_list.sort(key=lambda x: x['mtime'], reverse=True)
    return render_template('index.html', dbs=all_dbs, backups=backups_list, servers=servers)


@app.route('/add-server', methods=['POST'])
@login_required
def add_server():
    new_srv = {
        'host': request.form.get('host'),
        'port': request.form.get('port', '5432'),
        'user': request.form.get('user'),
        'password': request.form.get('password'),
        'db_type': request.form.get('db_type', 'postgres')
    }
    try:
        add_server_to_db(new_srv)
        flash("Servidor adicionado com sucesso!", "success")
    except Exception as e:
        flash(f"{e}", "danger")
    return redirect(url_for('index'))


@app.route('/remove-server/<int:id>')
@login_required
def remove_server(id):
    try:
        remove_server_from_db(id)
        flash("Servidor removido.", "info")
    except Exception as e:
        flash(f"Erro: {e}", "danger")
    return redirect(url_for('index'))


@app.route('/api/backup-single', methods=['POST'])
@login_required
def api_backup_single():
    db_id = request.json.get('id')
    if not db_id:
        return jsonify({"status": "error", "message": "ID não fornecido"}), 400

    with backup_lock:
        if db_id in active_manual_backups:
            return jsonify({"status": "error", "message": "Este banco já possui um backup em execução."}), 400

    try:
        server_id, db = db_id.split('|')
        if not is_valid_db_name(db):
            return jsonify({"status": "error", "message": "Nome de banco de dados inválido ou suspeito."}), 400

        srv = queries.get_backup_server_by_id(get_db_connection, server_id)

        if not srv:
            return jsonify({"status": "error", "message": "Servidor não encontrado."}), 404

        password = decrypt_password(srv['password'])
        host = srv['host']
        port = srv['port']
        user = srv['user']
        db_type = srv['db_type']

        db_folder = os.path.join(BACKUP_DIR, db)
        os.makedirs(db_folder, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        env = os.environ.copy()

        if db_type == 'postgres':
            backup_filepath = os.path.join(db_folder, f"{db}_{timestamp}.backup")
            env['PGPASSWORD'] = password
            cmd = ['pg_dump', '-h', host, '-p', str(port), '-U', user, '-F', 'c', '-f', backup_filepath, db]
        else:
            backup_filepath = os.path.join(db_folder, f"{db}_{timestamp}.sql")
            env['MYSQL_PWD'] = password
            cmd = ['mysqldump', '-h', host, '-P', str(port), '-u', user, f"--result-file={backup_filepath}", db]

        start_time = time.time()
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace')

        with backup_lock:
            active_manual_backups[db_id] = {
                "process": proc, "filepath": backup_filepath, "start_time": start_time,
                "db_name": db, "host": host, "db_type": db_type
            }

        return jsonify({"status": "started", "message": f"Backup de {db} iniciado."})
    except Exception as e:
        logging.error(f"Erro ao iniciar backup manual: {e}")
        return jsonify({"status": "error", "message": f"Erro ao iniciar: {str(e)}"}), 500


@app.route('/api/backup-status', methods=['POST'])
@login_required
def api_backup_status():
    db_id = request.json.get('id')

    with backup_lock:
        if not db_id or db_id not in active_manual_backups:
            return jsonify({"status": "idle"})
        backup_data = active_manual_backups[db_id]

    proc = backup_data["process"]
    poll_result = proc.poll()

    if poll_result is None:
        return jsonify({"status": "running"})

    with backup_lock:
        active_manual_backups.pop(db_id, None)

    stdout, stderr = proc.communicate()

    if poll_result == 0:
        duration = time.time() - backup_data["start_time"]
        msg = f"⚡ *Backup Manual Concluído ({backup_data['db_type'].upper()})*\n\n*Banco:* {backup_data['db_name']}\n*Host:* {backup_data['host']}\n*Duração:* {duration:.2f}s"
        send_whatsapp_notification(msg)
        return jsonify({"status": "success", "message": "Concluído!"})
    else:
        if os.path.exists(backup_data["filepath"]):
            os.remove(backup_data["filepath"])
        msg = f"❌ *Falha no Backup Manual*\n\n*Banco:* {backup_data['db_name']}\n*Host:* {backup_data['host']}\n*Erro:* {stderr}"
        send_whatsapp_notification(msg)
        return jsonify({"status": "error", "message": f"Falha: {stderr}"})


@app.route('/api/backup-cancel', methods=['POST'])
@login_required
def api_backup_cancel():
    db_id = request.json.get('id')

    with backup_lock:
        if not db_id or db_id not in active_manual_backups:
            return jsonify({"status": "error", "message": "Nenhum backup ativo encontrado."}), 400
        backup_data = active_manual_backups.pop(db_id)

    proc = backup_data["process"]
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except:
        try: proc.kill()
        except: pass

    if os.path.exists(backup_data["filepath"]):
        os.remove(backup_data["filepath"])

    msg = f"*Backup Manual Cancelado*\n\n*Banco:* {backup_data['db_name']}\n*Host:* {backup_data['host']}"
    send_whatsapp_notification(msg)
    return jsonify({"status": "cancelled", "message": "Interrompido com sucesso."})


@app.route('/download/<path:filename>')
@login_required
def download_file(filename):
    safe_target = safe_join(BACKUP_DIR, filename)

    if safe_target is None or not os.path.isfile(safe_target) or not safe_target.startswith(BACKUP_DIR):
        abort(403)

    directory = os.path.dirname(safe_target)
    file = os.path.basename(safe_target)

    return send_from_directory(directory, file, as_attachment=True)


if __name__ == '__main__':
    init_db()
    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)

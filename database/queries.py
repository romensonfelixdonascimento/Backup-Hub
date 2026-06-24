# queries.py
import logging
import os
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from werkzeug.security import generate_password_hash
import secrets

def init_database_structure(get_db_connection, db_name):
    """Inicializa o banco de dados principal e cria as tabelas necessárias."""
    conn = None
    try:
        # 1. Garante que o banco de dados principal existe
        conn = get_db_connection(target_db='postgres')
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (db_name,))
            if not cur.fetchone():
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
        conn.close()

        # 2. Conecta no banco recém-criado/existente para criar tabelas
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Tabela: backup_servers
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'backup_servers');")
            if not cur.fetchone()[0]:
                cur.execute("""
                    CREATE TABLE backup_servers (
                        id SERIAL PRIMARY KEY,
                        host TEXT NOT NULL,
                        port TEXT NOT NULL DEFAULT '5432',
                        db_user TEXT NOT NULL,
                        db_password TEXT NOT NULL,
                        db_type TEXT NOT NULL DEFAULT 'postgres',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()
            else:
                cur.execute("SELECT COLUMN_NAME FROM information_schema.columns WHERE table_name='backup_servers' AND column_name='db_type';")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE backup_servers ADD COLUMN db_type TEXT NOT NULL DEFAULT 'postgres';")
                    conn.commit()

            # Tabela: users
            cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'users');")
            if not cur.fetchone()[0]:
                logging.info("Criando tabela 'users' para controle de acesso...")
                cur.execute("""
                    CREATE TABLE users (
                        id SERIAL PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.commit()

                # Usuário Admin padrão estrito
                default_user = "admin"

                # Tenta pegar a senha do .env; se não existir, gera uma aleatória
                temp_password = os.getenv("ADMIN_DEFAULT_PASSWORD") or secrets.token_hex(6)
                default_password_hash = generate_password_hash(temp_password)

                cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s);", (default_user, default_password_hash))
                conn.commit()

                if os.getenv("ADMIN_DEFAULT_PASSWORD"):
                    logging.info("✅ Usuário padrão criado! Login: admin | Senha: [Definida via .env]")
                else:
                    logging.warning(f"⚠️ Usuário padrão criado! Login: admin | Senha Temporária: {temp_password}")
                    logging.warning("Por favor, altere esta senha imediatamente após o primeiro login.")

    except Exception as e:
        logging.error(f"Erro crítico ao rodar migrações/init_db: {e}")
        raise e
    finally:
        if conn:
            conn.close()


def get_all_backup_servers(get_db_connection, decrypt_fn):
    """Busca todos os servidores salvos e decriptografa suas senhas."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, host, port, db_user as user, db_password as password, db_type
                FROM backup_servers
                ORDER BY id ASC
            """)
            rows = cur.fetchall()
            for row in rows:
                row['password'] = decrypt_fn(row['password'])
            return rows
    except Exception as e:
        logging.error(f"Erro ao carregar servidores do banco: {e}")
        return []
    finally:
        if conn:
            conn.close()


def get_backup_server_by_id(get_db_connection, server_id):
    """Busca os dados de um servidor específico a partir do ID."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT host, port, db_user as user, db_password as password, db_type
                FROM backup_servers
                WHERE id = %s
            """, (server_id,))
            return cur.fetchone()
    finally:
        if conn:
            conn.close()


def insert_backup_server(get_db_connection, host, port, user, encrypted_password, db_type):
    """Salva um novo servidor de backup no banco de dados."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO backup_servers (host, port, db_user, db_password, db_type)
                VALUES (%s, %s, %s, %s, %s)
            """, (host, port, user, encrypted_password, db_type))
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()


def delete_backup_server(get_db_connection, server_id):
    """Remove um servidor do banco de dados pelo ID."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM backup_servers WHERE id = %s", (server_id,))
            conn.commit()
    finally:
        if conn:
            conn.close()


def get_user_by_username(get_db_connection, username):
    """Busca um usuário pelo username."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, username, password_hash FROM users WHERE username = %s;", (username,))
            return cur.fetchone()
    finally:
        if conn:
            conn.close()


def check_user_exists(get_db_connection, username):
    """Verifica se um determinado username já está em uso."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s;", (username,))
            return cur.fetchone() is not None
    finally:
        if conn:
            conn.close()


def insert_user(get_db_connection, username, password_hash):
    """Cria um novo usuário no sistema."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s);", (username, password_hash))
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()


def get_user_password_hash_by_id(get_db_connection, user_id):
    """Retorna o hash da senha de um usuário específico pelo ID."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT password_hash FROM users WHERE id = %s;", (user_id,))
            return cur.fetchone()
    finally:
        if conn:
            conn.close()


def update_user_password(get_db_connection, user_id, new_password_hash):
    """Atualiza a senha do usuário informado."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s;", (new_password_hash, user_id))
            conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()
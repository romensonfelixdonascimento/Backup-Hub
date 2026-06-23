# 🐘 Backup-Hub

O **Backup-Hub** é uma solução robusta e centralizada para gerenciamento, execução e monitoramento de backups de múltiplos servidores PostgreSQL. Com uma interface web (Dashboard) moderna que suporta alternância de temas (Dark/Light), a ferramenta permite orquestrar rotinas automáticas paralelas e disparar backups manuais sob demanda com acompanhamento em tempo real e notificações automáticas via WhatsApp (via API WAHA).

![PostgreSQL](https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white)
![Bootstrap](https://img.shields.io/badge/Bootstrap-563D7C?style=for-the-badge&logo=bootstrap&logoColor=white)
![License](https://img.shields.io/badge/License-AGPL%20v3-blue.svg?style=for-the-badge)

---

## 🚀 Funcionalidades

- **Multi-server management:** Gerencie múltiplos servidores PostgreSQL em um único painel.
- **Descoberta automática de bancos:** Lista automaticamente todos os bancos disponíveis ao registrar um servidor.
- **Fila de execução manual:** Execute backups em sequência com acompanhamento de progresso em tempo real.
- **Persistência de estado:** Estado da fila salvo no `localStorage`, evitando perda ao atualizar a página.
- **Backups automáticos agendados:** Execução paralela com `Flask-APScheduler` e `ThreadPoolExecutor`.
- **Notificações via WhatsApp:** Integração com API WAHA para alertas de sucesso, falha ou cancelamento.
- **Limpeza automática:** Remove backups locais com mais de 3 dias.
- **Segurança:** Credenciais criptografadas com `cryptography.fernet`.

---

## 🛠️ Tecnologias

### Backend
- Python 3.10+
- Flask
- Flask-APScheduler
- Psycopg2
- Cryptography (Fernet)
- pg_dump (subprocess)

### Frontend
- HTML5 / CSS3
- Bootstrap 5.3
- JavaScript (ES6+)

---

## ⚙️ Pré-requisitos

- Python 3.10+
- PostgreSQL client (`pg_dump`)
- Mysql-client
- (Opcional) Instância WAHA para WhatsApp API

---

## 🔧 Instalação

### 1. Clone o repositório
```bash
git clone [https://github.com/romensonfelixdonascimento/Backup-Hub.git](https://github.com/romensonfelixdonascimento/Backup-Hub.git)
cd Backup-Hub

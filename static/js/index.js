const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');

function updateDefaultPort() {
    const type = document.getElementById('db_type_select').value;
    const portInput = document.getElementById('db_port_input');
    portInput.value = (type === 'postgres') ? '5432' : '3306';
}

const themeToggleBtn = document.getElementById('themeToggleBtn');
const htmlElement = document.documentElement;
const savedTheme = localStorage.getItem('theme') || 'dark';
htmlElement.setAttribute('data-bs-theme', savedTheme);
themeToggleBtn.innerHTML = savedTheme === 'dark' ? '☀️' : '🌙';

themeToggleBtn.addEventListener('click', () => {
    const currentTheme = htmlElement.getAttribute('data-bs-theme');
    const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
    htmlElement.setAttribute('data-bs-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    themeToggleBtn.innerHTML = newTheme === 'dark' ? '☀️' : '🌙';
});

let checkInterval = null;
let currentBackupQueue = [];
let currentQueueIndex = 0;
let globalErrors = [];

const btnRun = document.getElementById('btnRunBackup');
const btnCancel = document.getElementById('btnCancelBackup');
const progressContainer = document.getElementById('progressContainer');
const progressBar = document.getElementById('progressBar');
const progressText = document.getElementById('progressText');
const progressPctText = document.getElementById('progressPctText');

document.addEventListener('DOMContentLoaded', function() {
    const savedQueue = localStorage.getItem('active_backup_queue');
    const savedIndex = localStorage.getItem('active_backup_index');
    if (savedQueue && savedIndex) {
        currentBackupQueue = JSON.parse(savedQueue);
        currentQueueIndex = parseInt(savedIndex);
        globalErrors = JSON.parse(localStorage.getItem('active_backup_errors') || '[]');
        btnRun.disabled = true;
        progressContainer.style.display = 'block';
        btnCancel.style.display = 'block';
        monitorarBackupAtual();
    }
});

document.getElementById('backupForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const checkboxes = document.querySelectorAll('input[name="databases"]:checked');
    if (checkboxes.length === 0) {
        alert('Selecione ao menos um banco para backup.');
        return;
    }

    btnRun.disabled = true;
    progressContainer.style.display = 'block';
    btnCancel.style.display = 'block';

    currentBackupQueue = [];
    checkboxes.forEach(chk => {
        const dbName = chk.nextElementSibling.querySelector('strong').innerText;
        currentBackupQueue.push({ id: chk.value, name: dbName });
    });

    currentQueueIndex = 0;
    globalErrors = [];
    atualizarCacheLocalStorage();
    dispararProximoDaFila();
});

async function dispararProximoDaFila() {
    if (currentQueueIndex >= currentBackupQueue.length) {
        finalizarProcessoFila();
        return;
    }
    const itemAtual = currentBackupQueue[currentQueueIndex];
    progressText.innerText = `Iniciando [${currentQueueIndex + 1}/${currentBackupQueue.length}]: ${itemAtual.name}...`;
    atualizarBarraVisual();

    try {
        const response = await fetch('/api/backup-single', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken // Envio do CSRF via Header
            },
            body: JSON.stringify({ id: itemAtual.id })
        });
        const result = await response.json();
        if (result.status === 'started') {
            monitorarBackupAtual();
        } else {
            globalErrors.push(`[${itemAtual.name}] - ${result.message}`);
            irParaProximoItem();
        }
    } catch (err) {
        globalErrors.push(`[${itemAtual.name}] - Falha de comunicação de rede.`);
        irParaProximoItem();
    }
}

function monitorarBackupAtual() {
    if (checkInterval) clearInterval(checkInterval);
    const itemAtual = currentBackupQueue[currentQueueIndex];
    progressText.innerText = `Processando [${currentQueueIndex + 1}/${currentBackupQueue.length}]: ${itemAtual.name}...`;

    checkInterval = setInterval(async () => {
        try {
            const response = await fetch('/api/backup-status', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken // Envio do CSRF via Header
                },
                body: JSON.stringify({ id: itemAtual.id })
            });
            const result = await response.json();
            if (result.status === 'success') {
                clearInterval(checkInterval);
                irParaProximoItem();
            } else if (result.status === 'error') {
                clearInterval(checkInterval);
                globalErrors.push(`[${itemAtual.name}] - ${result.message}`);
                irParaProximoItem();
            } else if (result.status === 'idle') {
                clearInterval(checkInterval);
                globalErrors.push(`[${itemAtual.name}] - Status expirado no servidor.`);
                irParaProximoItem();
            }
        } catch (err) {
            console.error(err);
        }
    }, 2000);
}

function irParaProximoItem() {
    currentQueueIndex++;
    atualizarCacheLocalStorage();
    dispararProximoDaFila();
}

btnCancel.addEventListener('click', async () => {
    if (currentQueueIndex >= currentBackupQueue.length) return;
    const itemAtual = currentBackupQueue[currentQueueIndex];
    if (!confirm(`Parar backup de "${itemAtual.name}"?`)) return;

    if (checkInterval) clearInterval(checkInterval);
    try {
        await fetch('/api/backup-cancel', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken // Envio do CSRF via Header
            },
            body: JSON.stringify({ id: itemAtual.id })
        });
    } catch (err) {}
    irParaProximoItem();
});

function atualizarBarraVisual() {
    let total = currentBackupQueue.length;
    let pct = total > 0 ? Math.round((currentQueueIndex / total) * 100) : 0;
    progressBar.style.width = pct + '%';
    progressPctText.innerText = pct + '%';
}

function atualizarCacheLocalStorage() {
    localStorage.setItem('active_backup_queue', JSON.stringify(currentBackupQueue));
    localStorage.setItem('active_backup_index', currentQueueIndex);
    localStorage.setItem('active_backup_errors', JSON.stringify(globalErrors));
}

function finalizarProcessoFila() {
    if (checkInterval) clearInterval(checkInterval);
    progressBar.style.width = '100%';
    progressPctText.innerText = '100%';
    btnRun.disabled = false;
    btnCancel.style.display = 'none';
    localStorage.clear();

    if (globalErrors.length > 0) {
        document.getElementById('errorMessagesList').innerText = globalErrors.join('\n\n');
        new bootstrap.Modal(document.getElementById('errorModal')).show();
    } else {
        setTimeout(() => { window.location.reload(); }, 1000);
    }
}

// ==========================================
// Filtro em tempo real - Bancos de Dados
// ==========================================
const searchDbInput = document.getElementById('searchDbInput');
if (searchDbInput) {
    searchDbInput.addEventListener('input', function() {
        const term = this.value.toLowerCase();
        const dbItems = document.querySelectorAll('.db-card-item');

        dbItems.forEach(item => {
            const textContent = item.innerText.toLowerCase();
            if (textContent.includes(term)) {
                item.style.display = '';
            } else {
                item.style.display = 'none';
            }
        });
    });
}

// ==========================================
// Filtro em tempo real - Backups em Storage
// ==========================================
const searchBackupInput = document.getElementById('searchBackupInput');
if (searchBackupInput) {
    searchBackupInput.addEventListener('input', function() {
        const term = this.value.toLowerCase();
        const backupItems = document.querySelectorAll('.backup-list-item');

        backupItems.forEach(item => {
            const textContent = item.innerText.toLowerCase();
            if (textContent.includes(term)) {
                item.style.display = 'flex';
            } else {
                item.style.display = 'none';
            }
        });
    });
}
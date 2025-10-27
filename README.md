# 🗨️ Sistemas Distribuídos – GroupChat XML-RPC

Um sistema de **chat distribuído** multiusuário baseado em **Python + XML-RPC**, com armazenamento em **SQLite** e frontend simples em HTML/JS.
Cada cliente se comunica com o servidor via RPC para autenticação, criação de grupos, troca de mensagens e sincronização em tempo real (via long-poll de eventos).
Inclui um easter egg que integra um **LLM motivacional remoto** (`MotivaBot`).

---

## 📁 Estrutura do projeto

```
distribuidos-groupchat/
├── server.py                  # Servidor principal XML-RPC
├── static/
│   ├── index.html             # Interface web (chat)
│   ├── client.js              # Lógica cliente (fetch XML-RPC)
│   ├── styles.css             # Estilo da interface
├── tests_rpc.py               # Testes automatizados de RPC
├── cleanup_test_users.py      # Limpeza de usuários de teste
├── Dockerfile                 # Build da imagem
├── docker-compose.yml         # Orquestra o container
└── README.md                  # Este arquivo
```

---

## ⚙️ Requisitos

* **Python ≥ 3.9**
* **Docker & Docker Compose**
* (opcional) Servidor **MotivaBot** local (`ollama`) se quiser ativar o easter egg

---

## 🚀 Execução com Docker

1️⃣ **Build e subir o servidor**

```bash
docker compose up -d --build
```

O servidor XML-RPC sobe na porta **8000**, e o front-end HTTP (estático) na **8080**.

> 🌐 Acesse o chat em
> `http://localhost:8080`

2️⃣ **Banco de dados**

* O banco `chat.db` é persistido no volume `sdchat_data` (padrão Docker).
* Você pode mudar para um *bind mount* editando o `docker-compose.yml`:

```yaml
volumes:
  - ./data:/data
```

---

## 👥 Funcionalidades principais

| Categoria                       | Descrição                                                                                           |
| ------------------------------- | --------------------------------------------------------------------------------------------------- |
| 🧑‍💻 **Autenticação**          | Cadastro e login de usuários (email + senha); email único.                                          |
| 💬 **Grupos**                   | Criação de grupos com qualquer número de participantes.                                             |
| 🕴️ **Chat 1:1**                | Feito como grupo de dois usuários (Ctrl + Clique em outro usuário).                                 |
| 📨 **Mensagens**                | Armazenadas em SQLite, com histórico e busca incremental (`get_messages_since`).                    |
| ⚡ **Atualização em tempo real** | Implementada via long-poll (`wait_events`) — 1 requisição a cada ~30 s idle.                        |
| 👋 **Eventos**                  | `message`, `group_added`, `group_removed` notificam clientes relevantes.                            |
| 💾 **Persistência**             | Tudo salvo em `chat.db` com chaves estrangeiras e `ON DELETE CASCADE`.                              |
| 🧹 **Limpeza de testes**        | Script `cleanup_test_users.py` remove usuários de teste e seus dados.                               |
| 🧪 **Testes RPC**               | `tests_rpc.py` cobre cadastro, login, grupos, mensagens e remoções.                                 |
| 🤖 **Easter Egg (MotivaBot)**   | Ao enviar `/moti texto`, o servidor chama um LLM remoto via XML-RPC e posta uma frase motivacional. |

---

## 💻 Front-end (cliente web)

* Implementado em **HTML + CSS + JS puro**.
* Usa `fetch` para enviar chamadas XML-RPC.
* Interface responsiva e clara.

### 🧭 Atalhos e dicas

| Ação                                | Atalho / comportamento                                |
| ----------------------------------- | ----------------------------------------------------- |
| **Enter**                           | Envia a mensagem.                                     |
| **Shift + Enter**                   | Quebra de linha.                                      |
| **Ctrl + Clique** em um usuário     | Abre ou cria um grupo 1:1.                            |
| **“Sair do grupo”**                 | Sai do grupo; o grupo é excluído se ficar vazio.      |
| **/moti** (ou /motivacao, /inspire) | Dispara o MotivaBot se o servidor LLM estiver online. |

---

## 🧩 Integração com o LLM (MotivaBot)

O easter egg usa um **servidor LLM** (como o `Motivational Message Server` via Ollama).
Para ativá-lo, suba o servidor motivacional na porta 9000 e defina no `docker-compose.yml`:

```yaml
environment:
  - LLM_RPC_URL=http://host.docker.internal:9000
```

No chat, digite por exemplo:

```
/moti força pra terminar o sprint!
```

e o **MotivaBot** responde com uma frase motivacional gerada pelo seu LLM.

---

## 🧪 Testes automáticos

Execute o teste completo (cobre cadastro, login, grupos e mensagens):

```bash
docker compose exec sd-chat python3 /app/tests_rpc.py
```

Saída esperada:

```
🎉 Todos os testes passaram!
```

---

## 🧹 Limpeza de usuários de teste

Para remover usuários de teste criados pelos scripts (`a_*@test.local`, `b_*@test.local`):

```bash
# dentro do container
docker compose exec sd-chat python3 /app/cleanup_test_users.py
```

ou, se estiver usando bind mount:

```bash
DB_PATH=./data/chat.db python3 cleanup_test_users.py
```

---

## 🧠 Estrutura de banco

Entidades principais:

| Tabela                 | Finalidade                                      |
| ---------------------- | ----------------------------------------------- |
| `users`                | Credenciais e nomes de usuários.                |
| `sessions`             | Tokens de login com expiração.                  |
| `conversations`        | Grupos (tudo é `type='group'`).                 |
| `conversation_members` | Relação N:N entre grupos e usuários.            |
| `messages`             | Mensagens enviadas.                             |
| `events`               | Fila de eventos para long-poll (`wait_events`). |

Chaves estrangeiras mantêm integridade e apagam dados em cascata.

---

## 🧱 Arquitetura de comunicação

```
┌────────────┐     XML-RPC (HTTP)     ┌────────────┐
│ client.js  │  <───────────────────> │ server.py  │
│ (browser)  │                       │  (Python)  │
└────────────┘                        └────────────┘
       ▲                                    ▲
       │                                    │
       │  (eventos via wait_events)         │
       ▼                                    ▼
┌────────────────────────────────────────────────────┐
│ SQLite (chat.db): users, conversations, messages   │
└────────────────────────────────────────────────────┘
```

Long-poll mantém o navegador sincronizado sem precisar de WebSocket.

---

## 🧰 Desenvolvimento local (sem Docker)

```bash
export DB_PATH=chat.db
python3 server.py
```

Depois acesse:
`http://localhost:8080`

(use um servidor estático ou `python3 -m http.server 8080` dentro da pasta `static`).
# sd-desafio05
# sd-desafio05

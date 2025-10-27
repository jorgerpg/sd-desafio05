#!/usr/bin/env python3
import os
import sqlite3
import secrets
import hashlib
import datetime
import threading
from xmlrpc.server import SimpleXMLRPCServer, SimpleXMLRPCRequestHandler
import xmlrpc.client

from socketserver import ThreadingMixIn

DB_PATH = os.environ.get("DB_PATH", "chat.db")  # Caminho do SQLite persistente
LLM_RPC_URL = os.environ.get(
    "LLM_RPC_URL", "http://localhost:9000")  # Endpoint opcional do MotivaBot


def get_db():
  """Abre uma conexão SQLite com foreign keys habilitados."""
  conn = sqlite3.connect(DB_PATH, check_same_thread=False)
  conn.execute("PRAGMA foreign_keys = ON")
  return conn


def hash_pass(password: str, salt: str) -> str:
  """Aplica SHA-256 com sal para armazenar a senha."""
  return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def now_plus_hours(h=24):
  """Retorna timestamp UTC no futuro; usado para expiração da sessão."""
  return (datetime.datetime.utcnow() + datetime.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(conn):
  """Cria todas as tabelas necessárias caso não existam."""
  conn.executescript("""
    PRAGMA foreign_keys = ON;
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT NOT NULL UNIQUE,
      name TEXT NOT NULL,
      pass_hash TEXT NOT NULL,
      salt TEXT NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
      token TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      expires_at DATETIME,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS conversations (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      type TEXT NOT NULL CHECK (type IN ('direct','group')), -- manter compat, mas só usamos 'group'
      title TEXT
    );
    CREATE TABLE IF NOT EXISTS conversation_members (
      conversation_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (conversation_id, user_id),
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      conversation_id INTEGER NOT NULL,
      sender_id INTEGER NOT NULL,
      content TEXT NOT NULL,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
      FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
    );
    -- eventos para long-poll
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      type TEXT NOT NULL,              -- 'message','group_added','group_removed'
      conversation_id INTEGER,
      message_id INTEGER,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, id);
  """)
  conn.commit()


# ---------- Broker simples em memória para acordar long-polls ----------
class EventBroker:
  """Pequeno broker em memória responsável por acordar long-polls."""

  def __init__(self):
    self.conds = {}           # user_id -> threading.Condition dedicado
    self.lock = threading.Lock()

  def _cond_for(self, user_id):
    """Retorna (ou cria) a condition associada ao usuário."""
    with self.lock:
      if user_id not in self.conds:
        self.conds[user_id] = threading.Condition()
      return self.conds[user_id]

  def notify_user(self, user_id):
    """Acorda todos os long-polls aguardando eventos para o usuário."""
    cond = self._cond_for(user_id)
    with cond:
      cond.notify_all()

  def wait_for_user(self, user_id, timeout):
    """Bloqueia até que alguém chame notify_user ou o timeout expire."""
    cond = self._cond_for(user_id)
    with cond:
      cond.wait(timeout=timeout)


BROKER = EventBroker()


class ChatService:
  """Implementa toda a lógica de negócio exposta via XML-RPC."""

  def __init__(self, conn):
    self.conn = conn
    self.lock = threading.Lock()
    self.broker = BROKER

  # ---------- Helpers de evento ----------
  def _add_event(self, user_id, ev_type, conversation_id=None, message_id=None):
    """Registra um evento no banco e acorda quem está em long-poll."""
    self.conn.execute(
        "INSERT INTO events(user_id,type,conversation_id,message_id) VALUES (?,?,?,?)",
        (user_id, ev_type, conversation_id, message_id)
    )
    self.broker.notify_user(user_id)

  def _fanout_event_message(self, conversation_id, sender_id, message_id):
    """Enfileira eventos 'message' para todos os membros do grupo."""
    """Dispara evento 'message' para todos os membros exceto o remetente."""
    cur = self.conn.cursor()
    cur.execute("""SELECT user_id FROM conversation_members
                   WHERE conversation_id=? AND active=1""", (conversation_id,))
    for (uid,) in cur.fetchall():
      # remetente não recebe evento (cliente busca delta localmente)
      if uid == sender_id:
        continue
      self._add_event(uid, 'message', conversation_id, message_id)

  def _fanout_event_group_added(self, conversation_id, user_ids):
    """Dispara 'group_added' para usuários recém-adicionados."""
    """Notifica usuários adicionados a um novo grupo."""
    for uid in set(user_ids):
      self._add_event(uid, 'group_added', conversation_id, None)

  def _fanout_event_group_removed(self, conversation_id, user_ids):
    """Dispara 'group_removed' para quem perdeu acesso ao grupo."""
    """Notifica usuários quando um grupo deixa de existir."""
    for uid in set(user_ids):
      self._add_event(uid, 'group_removed', conversation_id, None)

  # ---------- Auth ----------
  def register_user(self, email, name, password):
    """Cadastro básico com validação de e-mail único."""
    with self.lock, self.conn:
      salt = secrets.token_hex(8)
      try:
        self.conn.execute(
            "INSERT INTO users(email,name,pass_hash,salt) VALUES (?,?,?,?)",
            (email, name, hash_pass(password, salt), salt)
        )
      except sqlite3.IntegrityError:
        return {"ok": False, "error": "EMAIL_IN_USE"}
    return {"ok": True}

  def login(self, email, password):
    """Valida credenciais e retorna token de sessão."""
    with self.lock:
      cur = self.conn.cursor()
      cur.execute(
          "SELECT id, pass_hash, salt FROM users WHERE email=?", (email,))
      row = cur.fetchone()
      if not row:
        return {"ok": False, "error": "INVALID_CREDENTIALS"}
      uid, pass_hash_db, salt = row
      if hash_pass(password, salt) != pass_hash_db:
        return {"ok": False, "error": "INVALID_CREDENTIALS"}
      token = secrets.token_hex(16)
      self.conn.execute(
          "INSERT INTO sessions(token,user_id,expires_at) VALUES (?,?,?)",
          (token, uid, now_plus_hours(24))
      )
      self.conn.commit()
      return {"ok": True, "token": token, "user_id": uid}

  def _auth(self, token):
    """Resgata o usuário autenticado a partir do token."""
    cur = self.conn.cursor()
    cur.execute("""SELECT s.user_id
                   FROM sessions s
                   WHERE s.token=? AND (s.expires_at IS NULL OR s.expires_at > datetime('now'))""", (token,))
    r = cur.fetchone()
    if not r:
      raise ValueError("UNAUTHORIZED")
    return r[0]

  # ---------- Users ----------

  def list_users(self, token):
    """Lista todos os usuários (id, nome, email)."""
    self._auth(token)
    cur = self.conn.cursor()
    cur.execute("SELECT id, name, email FROM users ORDER BY name")
    return [{"id": r[0], "name": r[1], "email": r[2]} for r in cur.fetchall()]

  # ---------- Grupos ----------
  def create_group(self, token, title, member_ids):
    """Cria grupo com o autor e demais selecionados, evitando duplicados."""
    """Cria um grupo incluindo quem solicitou e todos os selecionados."""
    me = self._auth(token)
    members = set(member_ids) | {me}
    with self.lock, self.conn:
      cur = self.conn.cursor()
      cur.execute(
          "INSERT INTO conversations(type,title) VALUES ('group',?)", (title,))
      cid = cur.lastrowid
      self.conn.executemany(
          "INSERT INTO conversation_members(conversation_id,user_id,active) VALUES (?,?,1)",
          [(cid, uid) for uid in members]
      )
      self._fanout_event_group_added(cid, members)
      self.conn.commit()
      return {"ok": True, "conversation_id": cid}

  def ensure_pair_group(self, token, other_user_id):
    """Garante um grupo 1:1 ativo entre 'me' e 'other_user_id' (sem duplicar).
       Se não existir, cria com título 'Chat: NomeA & NomeB'."""
    me = self._auth(token)
    cur = self.conn.cursor()
    # procura grupo com exatamente 2 membros ativos: me e other
    cur.execute("""
      SELECT c.id
      FROM conversations c
      JOIN conversation_members m1 ON m1.conversation_id=c.id AND m1.user_id=? AND m1.active=1
      JOIN conversation_members m2 ON m2.conversation_id=c.id AND m2.user_id=? AND m2.active=1
      WHERE c.type='group'
      AND (SELECT COUNT(*) FROM conversation_members cm
           WHERE cm.conversation_id=c.id AND cm.active=1)=2
      LIMIT 1
    """, (me, other_user_id))
    row = cur.fetchone()
    if row:
      return {"ok": True, "conversation_id": row[0], "created": False}

    # cria novo grupo 1:1
    cur.execute("SELECT name FROM users WHERE id=?", (me,))
    my_name = cur.fetchone()[0]
    cur.execute("SELECT name FROM users WHERE id=?", (other_user_id,))
    other_name = cur.fetchone()[0]
    title = f"Chat: {my_name} & {other_name}"

    with self.lock, self.conn:
      cur.execute(
          "INSERT INTO conversations(type,title) VALUES ('group',?)", (title,))
      cid = cur.lastrowid
      self.conn.executemany(
          "INSERT INTO conversation_members(conversation_id,user_id,active) VALUES (?,?,1)",
          [(cid, me), (cid, other_user_id)]
      )
      self._fanout_event_group_added(cid, {me, other_user_id})
      self.conn.commit()
    return {"ok": True, "conversation_id": cid, "created": True}

  def send_group_message(self, token, conversation_id, content):
    """Insere mensagem, entregando eventos e suportando o comando /motivacao."""
    me = self._auth(token)
    with self.lock, self.conn:
      cur = self.conn.cursor()
      cur.execute("""SELECT 1 FROM conversation_members
                     WHERE conversation_id=? AND user_id=? AND active=1""",
                  (conversation_id, me))
      if not cur.fetchone():
        return {"ok": False, "error": "NOT_A_MEMBER"}
        # --- Easter egg: comandos que disparam o LLM ---

      text = (content or "").strip()
      is_cmd = text.lower().startswith("/motivacao")

      if is_cmd:
        # Extrai o prompt após o comando
        parts = text.split(" ", 1)
        prompt = parts[1].strip() if len(parts) > 1 else ""
        if not prompt:
          prompt = "Faça uma frase motivacional curtinha para o time."

        # 1) (opcional) registra a mensagem do usuário (para dar contexto no histórico)
        cur.execute(
            "INSERT INTO messages(conversation_id,sender_id,content) VALUES (?,?,?)",
            (conversation_id, me, content)
        )
        user_mid = cur.lastrowid
        self._fanout_event_message(conversation_id, me, user_mid)

        # 2) chama o LLM e posta como bot
        bot_uid = self._get_or_create_bot_user()
        reply = self._try_llm_motivation(prompt)
        cur.execute(
            "INSERT INTO messages(conversation_id,sender_id,content) VALUES (?,?,?)",
            (conversation_id, bot_uid, reply)
        )
        bot_mid = cur.lastrowid
        self._fanout_event_message(conversation_id, bot_uid, bot_mid)

        self.conn.commit()
        return {"ok": True, "llm": True}

      # --- fluxo normal (sem comando) ---
      cur.execute(
          "INSERT INTO messages(conversation_id,sender_id,content) VALUES (?,?,?)",
          (conversation_id, me, content)
      )
      mid = cur.lastrowid
      self._fanout_event_message(conversation_id, me, mid)
      self.conn.commit()
      return {"ok": True}

  def list_my_conversations(self, token):
    """Lista conversas onde o usuário ainda está ativo."""
    me = self._auth(token)
    cur = self.conn.cursor()
    cur.execute("""
      SELECT c.id, c.type, c.title,
             (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) as message_count
      FROM conversations c
      JOIN conversation_members cm ON cm.conversation_id=c.id AND cm.user_id=? AND cm.active=1
      WHERE c.type='group'
      ORDER BY c.id DESC
    """, (me,))
    return [{"id": r[0], "type": r[1], "title": r[2], "message_count": r[3]} for r in cur.fetchall()]

  def get_messages(self, token, conversation_id, limit=100, offset=0):
    """Retorna histórico completo limitado/paginado."""
    me = self._auth(token)
    cur = self.conn.cursor()
    cur.execute("""SELECT 1 FROM conversation_members
                   WHERE conversation_id=? AND user_id=? AND active=1""",
                (conversation_id, me))
    if not cur.fetchone():
      return {"ok": False, "error": "NOT_A_MEMBER"}
    cur.execute("""
      SELECT m.id, m.sender_id, u.name, m.content, m.created_at
      FROM messages m
      JOIN users u ON u.id=m.sender_id
      WHERE m.conversation_id=?
      ORDER BY m.id DESC
      LIMIT ? OFFSET ?""", (conversation_id, limit, offset))
    msgs = [{"id": r[0], "sender_id": r[1], "sender_name": r[2], "content": r[3], "created_at": r[4]}
            for r in cur.fetchall()]
    return {"ok": True, "messages": list(reversed(msgs))}

  def get_messages_since(self, token, conversation_id, after_id):
    """Busca somente mensagens novas a partir de um ID."""
    me = self._auth(token)
    cur = self.conn.cursor()
    cur.execute("""SELECT 1 FROM conversation_members
                   WHERE conversation_id=? AND user_id=? AND active=1""",
                (conversation_id, me))
    if not cur.fetchone():
      return {"ok": False, "error": "NOT_A_MEMBER"}

    cur.execute("""
      SELECT m.id, m.sender_id, u.name, m.content, m.created_at
      FROM messages m
      JOIN users u ON u.id = m.sender_id
      WHERE m.conversation_id=? AND m.id > ?
      ORDER BY m.id ASC
    """, (conversation_id, after_id))
    msgs = [{"id": r[0], "sender_id": r[1], "sender_name": r[2], "content": r[3], "created_at": r[4]}
            for r in cur.fetchall()]
    return {"ok": True, "messages": msgs}

  def leave_group(self, token, conversation_id):
    """Remove o usuário do grupo e apaga a conversa se ficar vazia."""
    me = self._auth(token)
    with self.lock, self.conn:
      # membros ativos ANTES (para notificar remoção ao apagar)
      cur = self.conn.cursor()
      cur.execute("""SELECT user_id FROM conversation_members
                     WHERE conversation_id=? AND active=1""", (conversation_id,))
      members_before = [row[0] for row in cur.fetchall()]

      # marca como inativo
      self.conn.execute("""UPDATE conversation_members
                           SET active=0
                           WHERE conversation_id=? AND user_id=?""", (conversation_id, me))

      # verifica se ficou vazio
      cur = self.conn.cursor()
      cur.execute("""SELECT COUNT(*) FROM conversation_members
                     WHERE conversation_id=? AND active=1""", (conversation_id,))
      if cur.fetchone()[0] == 0:
        self.conn.execute(
            "DELETE FROM conversations WHERE id=?", (conversation_id,))
        self._fanout_event_group_removed(conversation_id, members_before)

      self.conn.commit()
      return {"ok": True}

  # ---------- Long-poll de eventos ----------

  def wait_events(self, token, after_event_id, timeout_ms=30000):
    """Loop de long-poll que retorna assim que houver eventos novos."""
    me = self._auth(token)
    t_end = datetime.datetime.utcnow() + datetime.timedelta(milliseconds=int(timeout_ms))

    def read_events():
      cur = self.conn.cursor()
      cur.execute("""
        SELECT id, type, conversation_id, message_id, created_at
        FROM events
        WHERE user_id=? AND id > ?
        ORDER BY id ASC
      """, (me, after_event_id))
      return [{
          "id": r[0], "type": r[1], "conversation_id": r[2],
          "message_id": r[3], "created_at": r[4]
      } for r in cur.fetchall()]

    evs = read_events()
    if evs:
      return {"ok": True, "events": evs}

    while datetime.datetime.utcnow() < t_end:
      remaining = (t_end - datetime.datetime.utcnow()).total_seconds()
      if remaining <= 0:
        break
      self.broker.wait_for_user(me, min(remaining, 5.0))
      evs = read_events()
      if evs:
        return {"ok": True, "events": evs}

    return {"ok": True, "events": []}

  def _get_or_create_bot_user(self):
    """Garante um usuário 'MotivaBot' para postar respostas do LLM."""
    cur = self.conn.cursor()
    cur.execute("SELECT id FROM users WHERE email=?", ("bot@local",))
    row = cur.fetchone()
    if row:
      return row[0]
    # cria
    salt = secrets.token_hex(8)
    self.conn.execute(
        "INSERT INTO users(email,name,pass_hash,salt) VALUES (?,?,?,?)",
        ("bot@local", "MotivaBot", hash_pass("bot", salt), salt)
    )
    self.conn.commit()
    cur.execute("SELECT id FROM users WHERE email=?", ("bot@local",))
    return cur.fetchone()[0]

  def _try_llm_motivation(self, user_text: str) -> str:
    """Chama o servidor LLM (XML-RPC) para gerar a frase motivacional."""
    try:
      peer = xmlrpc.client.ServerProxy(LLM_RPC_URL, allow_none=True)
      # o projeto antigo expõe generate_message(user_input: str) -> str
      return str(peer.generate_message(user_text)).strip()
    except Exception as e:
      return f"(MotivaBot) não consegui falar com o LLM agora: {e.__class__.__name__}"


def serve(host="0.0.0.0", port=8000):
  """Inicializa o servidor XML-RPC com CORS habilitado."""
  class RequestHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ('/RPC2',)

    def end_headers(self):
      # Permite que o front-end rode fora do servidor original.
      self.send_header('Access-Control-Allow-Origin', '*')
      self.send_header('Access-Control-Allow-Headers', 'content-type')
      self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
      super().end_headers()

    def do_OPTIONS(self):
      self.send_response(200)
      self.end_headers()

  class ThreadedXMLRPCServer(ThreadingMixIn, SimpleXMLRPCServer):
    daemon_threads = True
    allow_reuse_address = True

  conn = get_db()
  ensure_schema(conn)
  service = ChatService(conn)

  with ThreadedXMLRPCServer((host, port), requestHandler=RequestHandler, allow_none=True) as server:
    server.register_introspection_functions()
    # Métodos expostos (apenas grupos)
    server.register_function(service.register_user, 'register_user')
    server.register_function(service.login, 'login')
    server.register_function(service.list_users, 'list_users')

    server.register_function(service.create_group, 'create_group')
    server.register_function(service.ensure_pair_group,
                             'ensure_pair_group')

    server.register_function(service.send_group_message, 'send_group_message')
    server.register_function(
        service.list_my_conversations, 'list_my_conversations')
    server.register_function(service.get_messages, 'get_messages')
    server.register_function(service.get_messages_since, 'get_messages_since')
    server.register_function(service.leave_group, 'leave_group')

    server.register_function(service.wait_events, 'wait_events')

    print(f"[RPC] XML-RPC ativo em http://{host}:{port}/RPC2")
    server.serve_forever()


if __name__ == "__main__":
  serve()

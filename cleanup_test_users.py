#!/usr/bin/env python3
"""
Remove usuários de teste criados pelos scripts automáticos (a_*.@test.local, b_*.@test.local)
e todos os dados associados (sessions, mensagens, conversas via CASCADE).
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get("DB_PATH", "chat.db")


def cleanup_test_users(db_path: str):
  if not os.path.exists(db_path):
    print(f"[ERRO] Banco de dados '{db_path}' não encontrado.")
    sys.exit(1)

  conn = sqlite3.connect(db_path)
  conn.execute("PRAGMA foreign_keys = ON")

  cur = conn.cursor()

  # conta antes
  cur.execute(
      "SELECT COUNT(*) FROM users WHERE email LIKE 'a_%@test.local' OR email LIKE 'b_%@test.local'")
  count_before = cur.fetchone()[0]
  if count_before == 0:
    print("Nenhum usuário de teste encontrado — nada para limpar.")
    conn.close()
    return

  print(f"Removendo {count_before} usuários de teste...")

  with conn:
    cur.execute(
        "DELETE FROM users WHERE email LIKE 'a_%@test.local' OR email LIKE 'b_%@test.local'")

  cur.execute(
      "SELECT COUNT(*) FROM users WHERE email LIKE 'a_%@test.local' OR email LIKE 'b_%@test.local'")
  count_after = cur.fetchone()[0]

  print(f"Limpeza concluída. Restantes: {count_after}")
  conn.close()


if __name__ == "__main__":
  print(f"Usando banco: {DB_PATH}")
  cleanup_test_users(DB_PATH)

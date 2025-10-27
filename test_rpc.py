#!/usr/bin/env python3
import sys
import time
import argparse
import xmlrpc.client
from dataclasses import dataclass

# -------- helpers --------


def die(msg: str):
  print(f"❌ {msg}")
  sys.exit(1)


def ok(msg: str):
  print(f"✅ {msg}")


def info(msg: str):
  print(f"ℹ️  {msg}")


def assert_true(cond, msg_ok, msg_fail):
  if not cond:
    die(msg_fail)
  ok(msg_ok)


def find_conversation(convs, cid):
  return next((c for c in convs if c["id"] == cid), None)


def find_first(convs, typ):
  return next((c for c in convs if c["type"] == typ), None)

# -------- main --------


@dataclass
class UserSession:
  email: str
  name: str
  password: str
  token: str = ""
  user_id: int = -1


def main():
  parser = argparse.ArgumentParser(
      description="Teste de integração XML-RPC do SD Chat")
  parser.add_argument("--endpoint", default="http://localhost:8000/RPC2",
                      help="URL do endpoint XML-RPC (default: http://localhost:8000/RPC2)")
  args = parser.parse_args()

  server = xmlrpc.client.ServerProxy(args.endpoint, allow_none=True)

  # Identificadores únicos para evitar conflitos com execuções anteriores
  ts = int(time.time())
  userA = UserSession(email=f"a_{ts}@test.local",
                      name="Alice Test", password="passA!123")
  userB = UserSession(email=f"b_{ts}@test.local",
                      name="Bob Test",   password="passB!123")

  info(f"Endpoint: {args.endpoint}")
  info(f"Usuários de teste: {userA.email} / {userB.email}")

  # --- register_user ---
  r = server.register_user(userA.email, userA.name, userA.password)
  assert_true(r.get("ok") is True, "Cadastro A ok",
              "Falha ao cadastrar usuário A")

  r = server.register_user(userB.email, userB.name, userB.password)
  assert_true(r.get("ok") is True, "Cadastro B ok",
              "Falha ao cadastrar usuário B")

  # tentar duplicar email do A
  r = server.register_user(userA.email, userA.name, userA.password)
  assert_true(r.get("ok") is False and r.get("error") == "EMAIL_IN_USE",
              "Rejeição de e-mail duplicado ok", "Cadastro deveria falhar com EMAIL_IN_USE")

  # --- login ---
  r = server.login(userA.email, userA.password)
  assert_true(r.get("ok") is True and "token" in r,
              "Login A ok", "Falha no login A")
  userA.token, userA.user_id = r["token"], r["user_id"]

  r = server.login(userB.email, userB.password)
  assert_true(r.get("ok") is True and "token" in r,
              "Login B ok", "Falha no login B")
  userB.token, userB.user_id = r["token"], r["user_id"]

  # --- list_users ---
  users = server.list_users(userA.token)
  emails = {u["email"] for u in users}
  assert_true(userA.email in emails and userB.email in emails,
              "list_users contém A e B", "list_users não retornou os usuários de teste")

  # --- group: create_group / send_group_message / list_my_conversations / get_messages / leave_group ---
  r = server.create_group(userA.token, f"GrupoTeste_{ts}", [userB.user_id])
  assert_true(r.get("ok") is True and "conversation_id" in r,
              "Grupo criado", "Falha ao criar grupo")
  group_cid = r["conversation_id"]

  # A envia no grupo
  r = server.send_group_message(userA.token, group_cid, "Olá do A no grupo")
  assert_true(r.get("ok") is True, "A enviou no grupo",
              "A não conseguiu enviar no grupo")

  # B precisa descobrir o ID do grupo via list_my_conversations
  convsB = server.list_my_conversations(userB.token)
  group_in_B = find_conversation(
      convsB, group_cid) or find_first(convsB, "group")
  assert_true(group_in_B is not None,
              "Grupo aparece nas conversas de B", "Grupo não foi listado para B")

  # B envia no grupo
  r = server.send_group_message(userB.token, group_cid, "Olá do B no grupo")
  assert_true(r.get("ok") is True, "B enviou no grupo",
              "B não conseguiu enviar no grupo")

  # Validar histórico
  msgsA = server.get_messages(userA.token, group_cid, 50, 0)
  assert_true(msgsA.get("ok") is True, "get_messages (grupo) ok",
              "get_messages falhou (grupo)")
  texts = [m["content"] for m in msgsA["messages"]]
  assert_true(any("Olá do A" in t for t in texts) and any("Olá do B" in t for t in texts),
              "Mensagens A e B presentes no grupo", "Mensagens esperadas não encontradas no grupo")

  # Saída do grupo (A sai, depois B sai -> grupo deve sumir)
  r = server.leave_group(userA.token, group_cid)
  assert_true(r.get("ok") is True, "A saiu do grupo",
              "Falha ao sair do grupo (A)")

  r = server.leave_group(userB.token, group_cid)
  assert_true(r.get("ok") is True, "B saiu do grupo",
              "Falha ao sair do grupo (B)")

  # Grupo deve ter sido deletado (ninguém ativo)
  convsA_final = server.list_my_conversations(userA.token)
  convsB_final = server.list_my_conversations(userB.token)
  assert_true(find_conversation(convsA_final, group_cid) is None and
              find_conversation(convsB_final, group_cid) is None,
              "Grupo removido após todos saírem", "Grupo ainda aparece após todos saírem")

  # dispara o easter egg num grupo de teste:
  r = server.create_group(userA.token, f"Easter_{ts}", [userB.user_id])
  cid = r["conversation_id"]
  server.send_group_message(
      userA.token, cid, "/motivacao vamos nessa, preciso de um gás!")
  time.sleep(1.0)
  msgs = server.get_messages(userA.token, cid, 50, 0)
  print("Últimas mensagens:", [m["content"] for m in msgs["messages"]][-3:])

  print("\n🎉 Todos os testes passaram!")


if __name__ == "__main__":
  main()

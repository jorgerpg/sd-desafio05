// ===== Config =====
const STORAGE_KEY_RPC = 'sdchat_rpc_url';
const DEFAULT_RPC_URL = deriveDefaultRpcUrl();
let RPC_URL = null;
let PREFILLED_RPC_URL = DEFAULT_RPC_URL;
try{
  const stored = localStorage.getItem(STORAGE_KEY_RPC);
  if (stored){
    PREFILLED_RPC_URL = normalizeRpcUrl(stored);
  }
}catch(_e){}

// Estado global
let TOKEN = null;
let ME = null;
let USERS = [];
let SELECTED_FOR_GROUP = new Set();

let CURRENT_CONV = null;        // {id,type,title}

// Controle da conversa aberta
let LAST_MSG_ID = 0;

// Long-poll de eventos
let LAST_EVENT_ID = 0;
let EVENTS_LOOP_ACTIVE = false;

// ===== Util =====
function $(sel){ return document.querySelector(sel); }
function log(msg){
  const el = $('#log'); if(!el) return;
  el.textContent += msg + "\n"; el.scrollTop = el.scrollHeight;
}
function showScreen(idToShow){
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('visible'));
  document.getElementById(idToShow).classList.add('visible');
}
function openModal(){ document.getElementById('modal').classList.add('show'); }
function closeModal(){ document.getElementById('modal').classList.remove('show'); }

function deriveDefaultRpcUrl(){
  const proto = location.protocol.startsWith('http') ? location.protocol : 'http:';
  const host = location.hostname || 'localhost';
  return `${proto}//${host}:8000/RPC2`;
}
function normalizeRpcUrl(raw){
  let value = (raw || '').trim();
  if (!value){
    return DEFAULT_RPC_URL;
  }
  if (!/^https?:\/\//i.test(value)){
    value = `http://${value}`;
  }
  if (!/\/rpc2$/i.test(value)){
    value = value.replace(/\/+$/, '') + '/RPC2';
  }
  return value;
}
function setRpcUrl(raw){
  const normalized = normalizeRpcUrl(raw);
  RPC_URL = normalized;
  PREFILLED_RPC_URL = normalized;
  try{
    localStorage.setItem(STORAGE_KEY_RPC, normalized);
  }catch(_e){}
  updateServerDisplays();
  return normalized;
}

// ===== Navegação inferior =====
const PANES = Array.from(document.querySelectorAll('[data-pane]'));
const NAV_BTNS = Array.from(document.querySelectorAll('[data-pane-target]'));
let ACTIVE_PANE = 'users';

const CONNECT_INPUT = $('#server_url_connect');
const CONNECT_BUTTON = $('#btn_set_server');
const CONNECT_STATUS = $('#connect_status');
const ACTIVE_SERVER = $('#active_server');
const CHANGE_SERVER_BTN = $('#btn_change_server');

function activatePane(name, opts = {}){
  ACTIVE_PANE = name;
  PANES.forEach(p => p.classList.toggle('active', p.dataset.pane === name));
  if (!opts.skipNav){
    NAV_BTNS.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.paneTarget === name);
    });
  }
}

NAV_BTNS.forEach(btn => {
  btn.addEventListener('click', () => {
    const target = btn.dataset.paneTarget;
    if (target){
      activatePane(target);
    }
  });
});
activatePane('users');

function updateServerDisplays(){
  const current = RPC_URL || PREFILLED_RPC_URL || DEFAULT_RPC_URL;
  if (CONNECT_INPUT && document.activeElement !== CONNECT_INPUT){
    CONNECT_INPUT.value = current;
  }
  if (ACTIVE_SERVER){
    ACTIVE_SERVER.textContent = RPC_URL || '—';
  }
}
updateServerDisplays();

function setConnectStatus(msg, type='muted'){
  if (!CONNECT_STATUS) return;
  CONNECT_STATUS.textContent = msg || '';
  CONNECT_STATUS.classList.remove('error', 'success');
  if (type === 'error') CONNECT_STATUS.classList.add('error');
  if (type === 'success') CONNECT_STATUS.classList.add('success');
}
setConnectStatus('');

function ensureServerConfigured(){
  if (!RPC_URL){
    alert('Configure o servidor antes de prosseguir.');
    showScreen('view-connect');
    throw new Error('SERVER_NOT_CONFIGURED');
  }
}

async function probeServer(endpoint){
  await xmlRpcCall('system.listMethods', [], endpoint);
}

async function handleServerConnect(){
  if (!CONNECT_INPUT || !CONNECT_BUTTON) return;
  const normalized = normalizeRpcUrl(CONNECT_INPUT.value);
  CONNECT_BUTTON.disabled = true;
  setConnectStatus('Conectando...');
  try{
    await probeServer(normalized);
    setRpcUrl(normalized);
    setConnectStatus('Servidor conectado!', 'success');
    showScreen('view-login');
    updateServerDisplays();
  }catch(e){
    console.error(e);
    setConnectStatus('Não foi possível conectar: ' + (e.message || e), 'error');
  }finally{
    CONNECT_BUTTON.disabled = false;
  }
}

if (CONNECT_BUTTON){
  CONNECT_BUTTON.addEventListener('click', handleServerConnect);
}
if (CONNECT_INPUT){
  CONNECT_INPUT.value = PREFILLED_RPC_URL;
  CONNECT_INPUT.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter'){
      ev.preventDefault();
      handleServerConnect();
    }
  });
}
if (CHANGE_SERVER_BTN){
  CHANGE_SERVER_BTN.addEventListener('click', () => {
    showScreen('view-connect');
    updateServerDisplays();
    setConnectStatus('');
  });
}

// ===== XML-RPC helpers =====
function escapeXml(s){ return s.replace(/[<>&'"]/g, c=>({'<':'&lt;','>':'&gt;','&':'&amp;',"'":'&apos;','"':'&quot;'}[c])); }
function toXml(val){
  if (val === null || val === undefined) return `<value><nil/></value>`;
  if (typeof val === 'string') return `<value><string>${escapeXml(val)}</string></value>`;
  if (typeof val === 'number') return `<value><int>${val}</int></value>`;
  if (typeof val === 'boolean') return `<value><boolean>${val?1:0}</boolean></value>`;
  if (Array.isArray(val)) return `<value><array><data>${val.map(toXml).join('')}</data></array></value>`;
  if (typeof val === 'object'){
    return `<value><struct>${
      Object.entries(val).map(([k,v]) => `<member><name>${escapeXml(k)}</name>${toXml(v)}</member>`).join('')
    }</struct></value>`;
  }
  return `<value><string>${escapeXml(String(val))}</string></value>`;
}
function fromXmlValue(v){
  if(!v) return null;
  const t = v.firstElementChild;
  if(!t) return v.textContent;
  switch(t.tagName){
    case 'string': return t.textContent;
    case 'int':
    case 'i4': return parseInt(t.textContent, 10);
    case 'boolean': return t.textContent.trim()==='1';
    case 'array': return Array.from(t.querySelectorAll('data > value')).map(fromXmlValue);
    case 'struct': {
      const obj = {};
      t.querySelectorAll('member').forEach(m=>{
        const name = m.querySelector('name').textContent;
        const val = fromXmlValue(m.querySelector('value'));
        obj[name] = val;
      });
      return obj;
    }
    default: return t.textContent;
  }
}
function parseXmlRpcResponse(xmlText){
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, "text/xml");
  const fault = doc.querySelector("fault");
  if (fault) throw new Error("XML-RPC Fault");
  const value = doc.querySelector("methodResponse > params > param > value");
  return fromXmlValue(value);
}
function xmlRpcCall(method, params, endpoint){
  const target = endpoint || RPC_URL;
  if (!target) return Promise.reject(new Error('Servidor não configurado'));
  const xml = `<?xml version="1.0"?><methodCall><methodName>${method}</methodName><params>${params.map(p=>`<param>${toXml(p)}</param>`).join('')}</params></methodCall>`;
  return fetch(target, { method:'POST', headers:{'Content-Type':'text/xml'}, body: xml })
    .then(r=>r.text()).then(parseXmlRpcResponse);
}

// ===== Conversas (painel) =====
function renderConversations(convs){
  const ul = $('#convs');
  const selectedId = CURRENT_CONV ? CURRENT_CONV.id : null;
  ul.innerHTML = '';
  convs.forEach(c => {
    const li = document.createElement('li');
    li.textContent = `#${c.id} [${c.type}] ${c.title||''} (${c.message_count})`;
    if (c.id === selectedId){
      li.style.borderColor = '#3d7ff0';
      li.style.background = 'rgba(61,127,240,.12)';
    }
    li.onclick = async () => {
      CURRENT_CONV = c;
      activatePane('chat', { skipNav: true });
      await loadMsgs();
    };
    ul.appendChild(li);
  });
}
async function refreshConversationsOnce(){
  try{
    const convs = await xmlRpcCall('list_my_conversations', [TOKEN]);
    renderConversations(convs);
  }catch(e){
    log('convs refresh error: ' + e.message);
  }
}

// ===== Mensagens (conversa aberta) =====
async function loadMsgs(){
  if(!CURRENT_CONV) return;
  $('#current_conv').textContent = `Conversa atual: #${CURRENT_CONV.id} [${CURRENT_CONV.type}]`;
  try{
    const r = await xmlRpcCall('get_messages', [TOKEN, CURRENT_CONV.id, 200, 0]);
    if(!r.ok){ alert('Acesso negado'); return; }
    const box = $('#msgs');
    box.innerHTML = r.messages
      .map(m => `<div class="msg"><b>${m.sender_name}</b>: ${m.content} <small>${m.created_at}</small></div>`)
      .join('');
    box.scrollTop = box.scrollHeight;
    LAST_MSG_ID = r.messages.length ? r.messages[r.messages.length - 1].id : 0;
  }catch(e){ alert('Erro ao carregar mensagens'); log(e.message); }
}
async function fetchNewForCurrent(){
  if (!CURRENT_CONV) return;
  try{
    const r = await xmlRpcCall('get_messages_since', [TOKEN, CURRENT_CONV.id, LAST_MSG_ID]);
    if (r.ok && r.messages && r.messages.length){
      const box = $('#msgs');
      r.messages.forEach(m => {
        const div = document.createElement('div');
        div.className = 'msg';
        div.innerHTML = `<b>${m.sender_name}</b>: ${m.content} <small>${m.created_at}</small>`;
        box.appendChild(div);
        LAST_MSG_ID = Math.max(LAST_MSG_ID, m.id);
      });
      box.scrollTop = box.scrollHeight;
    }
  }catch(e){ log('fetch new error: ' + e.message); }
}

// ===== Long-poll de eventos =====
async function handleEvents(events){
  if (!events || !events.length) return;
  let needRefreshConvs = false;

  for (const ev of events){
    LAST_EVENT_ID = Math.max(LAST_EVENT_ID, ev.id);

    if (ev.type === 'message'){
      if (CURRENT_CONV && CURRENT_CONV.id === ev.conversation_id){
        await fetchNewForCurrent();
      }
      needRefreshConvs = TrueOr(needRefreshConvs);
    }
    else if (ev.type === 'group_added'){
      needRefreshConvs = TrueOr(needRefreshConvs);
    }
    else if (ev.type === 'group_removed'){
      if (CURRENT_CONV && CURRENT_CONV.id === ev.conversation_id){
        CURRENT_CONV = null;
        $('#msgs').innerHTML=''; $('#current_conv').textContent='Nenhuma conversa selecionada';
      }
      needRefreshConvs = TrueOr(needRefreshConvs);
    }
  }
  if (needRefreshConvs){
    await refreshConversationsOnce();
  }
}
function TrueOr(v){ return v || true; }

async function longPollEvents(){
  if (!TOKEN) return;
  if (EVENTS_LOOP_ACTIVE) return;
  EVENTS_LOOP_ACTIVE = true;
  while (TOKEN){
    try{
      const r = await xmlRpcCall('wait_events', [TOKEN, LAST_EVENT_ID, 30000]);
      if (r && r.ok){
        await handleEvents(r.events || []);
      }
    }catch(e){
      log('events error: ' + e.message);
      await new Promise(res => setTimeout(res, 2000));
    }
  }
  EVENTS_LOOP_ACTIVE = false;
}

// ===== Login / Cadastro =====
$('#btn_login').onclick = async () => {
  try{
    ensureServerConfigured();
  }catch(_e){
    return;
  }
  const email = $('#login_email').value.trim();
  const pass  = $('#login_pass').value;
  try{
    const r = await xmlRpcCall('login', [email, pass]);
    log('login: ' + JSON.stringify(r));
    if(!r.ok){ alert('Credenciais inválidas'); return; }
    TOKEN = r.token; ME = r.user_id;
    $('#me').textContent = `Logado como ${email} (id ${ME})`;
    LAST_EVENT_ID = 0;
    showScreen('view-chat');
    activatePane('users');
    await Promise.all([refreshUsers(), refreshConversationsOnce()]);
    longPollEvents();
  }catch(e){ alert('Erro no login'); log(e.message); }
};

$('#open-register').onclick = openModal;
document.querySelectorAll('[data-close]').forEach(el => el.onclick = closeModal);

$('#btn_reg').onclick = async () => {
  try{
    ensureServerConfigured();
  }catch(_e){
    return;
  }
  const name = $('#reg_name').value.trim();
  const email = $('#reg_email').value.trim();
  const pass  = $('#reg_pass').value;
  if(!name || !email || !pass){ alert('Preencha todos os campos.'); return; }
  try{
    const r = await xmlRpcCall('register_user', [email, name, pass]);
    log('register_user: ' + JSON.stringify(r));
    if(!r.ok && r.error === 'EMAIL_IN_USE'){ alert('E-mail já utilizado.'); return; }
    alert('Conta criada! Você já pode fazer login.');
    closeModal();
    $('#login_email').value = email;
  }catch(e){ alert('Erro no cadastro'); log(e.message); }
};

$('#btn_logout').onclick = () => {
  TOKEN = null; ME = null; USERS = []; SELECTED_FOR_GROUP.clear();
  CURRENT_CONV = null;
  LAST_MSG_ID = 0; LAST_EVENT_ID = 0; EVENTS_LOOP_ACTIVE = false;
  $('#users').innerHTML = ''; $('#selected_users').innerHTML = '';
  $('#convs').innerHTML = ''; $('#msgs').innerHTML = ''; $('#current_conv').textContent = 'Nenhuma conversa selecionada';
  showScreen('view-login');
};

// ===== Usuários / Grupo =====
async function refreshUsers(){
  try{
    const r = await xmlRpcCall('list_users', [TOKEN]);
    USERS = r;
    const ul = $('#users'); ul.innerHTML = '';
    r.forEach(u => {
      const li = document.createElement('li');
      li.textContent = `${u.name} <${u.email}>`;
      li.title = `ID ${u.id} — Clique para selecionar; Ctrl+Clique para abrir chat 1:1 (grupo).`;
      li.onclick = async (ev) => {
        if (ev.ctrlKey){
          try{
            const resp = await xmlRpcCall('ensure_pair_group', [TOKEN, u.id]);
            CURRENT_CONV = { id: resp.conversation_id, type: 'group', title: null };
            activatePane('chat', { skipNav: true });
            await Promise.all([loadMsgs(), refreshConversationsOnce()]);
          }catch(e){ alert('Erro ao abrir chat 1:1'); log(e.message); }
          return;
        }
        if (SELECTED_FOR_GROUP.has(u.id)) SELECTED_FOR_GROUP.delete(u.id);
        else SELECTED_FOR_GROUP.add(u.id);
        redrawSelected();
      };
      ul.appendChild(li);
    });
  }catch(e){ alert('Erro ao listar usuários'); log(e.message); }
}
$('#btn_list_users').onclick = refreshUsers;

function redrawSelected(){
  const wrap = $('#selected_users');
  wrap.innerHTML = '';
  SELECTED_FOR_GROUP.forEach(id => {
    const u = USERS.find(x=>x.id===id);
    if(u){
      const span = document.createElement('span');
      span.className = 'chip'; span.textContent = `${u.name}`;
      wrap.appendChild(span);
    }
  });
}

$('#btn_create_group').onclick = async () => {
  const title = $('#group_title').value.trim() || 'Grupo';
  const ids = Array.from(SELECTED_FOR_GROUP);
  if(ids.length === 0){ alert('Selecione ao menos 1 usuário.'); return; }
  try{
    const r = await xmlRpcCall('create_group', [TOKEN, title, ids]);
    log('create_group: ' + JSON.stringify(r));
    alert('Grupo criado!');
    SELECTED_FOR_GROUP.clear(); redrawSelected(); $('#group_title').value = '';
    await refreshConversationsOnce();
  }catch(e){ alert('Erro ao criar grupo'); log(e.message); }
};

// ===== Envio / ações =====
$('#btn_list_convs').onclick = () => refreshConversationsOnce();

$('#btn_send').onclick = async () => {
  const text = $('#msg_text').value;
  if (!text.trim()) return;
  try{
    if (!CURRENT_CONV){ alert('Escolha uma conversa'); return; }
    await xmlRpcCall('send_group_message', [TOKEN, CURRENT_CONV.id, text]);
    $('#msg_text').value = '';
    await fetchNewForCurrent();
    await refreshConversationsOnce();
  }catch(e){ alert('Erro ao enviar'); log(e.message); }
};

$('#msg_text').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) {
    ev.preventDefault();  // evita quebra de linha
    $('#btn_send').click(); // aciona o mesmo fluxo de envio
  }
});

$('#btn_delete_group').onclick = async () => {
  if (!CURRENT_CONV || CURRENT_CONV.type !== 'group'){ alert('Não é grupo.'); return; }
  if (!confirm('Excluir esta conversa? Se ainda houver participantes ativos você apenas sairá do grupo.')){
    return;
  }
  try{
    const r = await xmlRpcCall('leave_group', [TOKEN, CURRENT_CONV.id]);
    log('leave_group: ' + JSON.stringify(r));
    CURRENT_CONV = null;
    $('#msgs').innerHTML=''; $('#current_conv').textContent='Nenhuma conversa selecionada';
    activatePane('convs');
    await refreshConversationsOnce();
  }catch(e){ alert('Erro ao sair do grupo'); log(e.message); }
};

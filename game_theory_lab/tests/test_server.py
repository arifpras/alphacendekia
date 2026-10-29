import csv, importlib, io, json, sys, threading
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PW = 'test-pw'
H = {'x-admin-password': PW}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('GTL_DB', str(tmp_path / 'lab.db'))
    monkeypatch.setenv('GTL_ADMIN_PASSWORD', PW)
    import server
    importlib.reload(server)
    return TestClient(server.app)


def new_session(c):
    return c.post('/api/session', headers=H).json()['pin']


def join(c, pin, name='s'):
    r = c.post('/api/join', json={'pin': pin, 'nickname': name})
    assert r.status_code == 200, r.text
    return r.json()


def start(c, pin, game, config=None):
    r = c.post('/api/start', headers=H, json={'pin': pin, 'game': game, 'config': config or {}})
    assert r.status_code == 200, r.text


def submit(c, pin, who, action):
    return c.post('/api/submit', json={'pin': pin, 'secret': who['secret'], 'action': action})


def admin_state(c, pin):
    return c.get(f'/api/state?pin={pin}', headers=H).json()


def advance(c, pin):
    return c.post(f'/api/advance?pin={pin}', headers=H)


@pytest.mark.parametrize('game,action', [('auction', {'bid': 10}), ('trading', {'price': 10}), ('beauty', {'guess': 10}), ('oligopoly', {'quantity': 5})])
def test_late_joiner_cannot_submit_or_break_round(client, game, action):
    pin = new_session(client)
    a, b = join(client, pin), join(client, pin)
    start(client, pin, game)
    late = join(client, pin)
    r = submit(client, pin, late, action)
    assert r.status_code == 400 and 'after this round started' in r.json()['detail']
    assert admin_state(client, pin)['round']['expected'] == 2
    s = client.get(f'/api/state?pin={pin}&secret={late["secret"]}').json()
    assert s['round']['in_round'] is False
    assert advance(client, pin).status_code == 200
    start(client, pin, 'beauty')  # next round starts and includes the late joiner
    assert submit(client, pin, late, {'guess': 1}).status_code == 200


def test_labels_are_stored_and_names_kept_private(client):
    pin = new_session(client)
    a = join(client, pin, 'Alice Real')
    b = join(client, pin, 'Bob Real')
    assert (a['label'], b['label']) == ('Participant 01', 'Participant 02')
    roster = admin_state(client, pin)['participants_private']
    assert [(x['label'], x['nickname']) for x in roster] == [('Participant 01', 'Alice Real'), ('Participant 02', 'Bob Real')]
    start(client, pin, 'beauty')
    submit(client, pin, a, {'guess': 10}); submit(client, pin, b, {'guess': 20})
    advance(client, pin)
    results = admin_state(client, pin)['round']['results']
    assert {x['participant'] for x in results} == {'Participant 01', 'Participant 02'}
    student = client.get(f'/api/state?pin={pin}&secret={a["secret"]}').json()
    assert 'Alice Real' not in json.dumps(student) and 'Bob Real' not in json.dumps(student)


def test_concurrent_joins_respect_cap_and_unique_labels(client):
    pin = new_session(client)
    out = []
    def go():
        out.append(client.post('/api/join', json={'pin': pin, 'nickname': 'x'}))
    ts = [threading.Thread(target=go) for _ in range(30)]
    [t.start() for t in ts]; [t.join() for t in ts]
    ok = [r.json() for r in out if r.status_code == 200]
    assert len(ok) == 20
    assert len({x['label'] for x in ok}) == 20
    assert sum(r.status_code == 409 for r in out) == 10


def test_remove_participant(client):
    pin = new_session(client)
    a, b, gone = join(client, pin), join(client, pin), join(client, pin)
    start(client, pin, 'beauty')
    gone_id = [x['id'] for x in admin_state(client, pin)['participants_private'] if x['label'] == gone['label']][0]
    assert admin_state(client, pin)['round']['expected'] == 3
    assert client.post(f'/api/remove?pin={pin}&participant_id={gone_id}', headers=H).status_code == 200
    assert admin_state(client, pin)['round']['expected'] == 2
    assert submit(client, pin, gone, {'guess': 5}).status_code == 401
    assert client.get(f'/api/state?pin={pin}&secret={gone["secret"]}').status_code == 401
    advance(client, pin)
    start(client, pin, 'prisoner')
    assert admin_state(client, pin)['round']['expected'] == 2  # removed student not paired
    assert join(client, pin)['label'] == 'Participant 04'  # labels never reused
    assert client.post(f'/api/remove?pin={pin}&participant_id={gone_id}').status_code == 401


def test_admin_password_header_only_and_non_ascii(client):
    pin = new_session(client)
    assert client.get(f'/api/state?pin={pin}&admin_password={PW}').status_code == 401
    assert client.get(f'/api/state?pin={pin}', headers={'x-admin-password': 'wrong'}).status_code == 401
    # Non-ASCII passwords used to raise TypeError inside compare_digest (500).
    bad = {'x-admin-password': 'é'.encode('utf-8')}
    assert client.get(f'/api/history?pin={pin}', headers=bad).status_code == 401
    assert client.get(f'/api/history?pin={pin}', headers=H).status_code == 200
    assert client.get(f'/api/export?pin={pin}').status_code == 401


def test_no_default_password(tmp_path, monkeypatch):
    monkeypatch.setenv('GTL_DB', str(tmp_path / 'lab.db'))
    monkeypatch.delenv('GTL_ADMIN_PASSWORD', raising=False)
    import server
    importlib.reload(server)
    assert server.ADMIN_PASSWORD and server.ADMIN_PASSWORD != 'classroom-demo'
    c = TestClient(server.app)
    assert c.post('/api/session', headers={'x-admin-password': 'classroom-demo'}).status_code == 401


def test_export_includes_assignment_and_resting(client):
    pin = new_session(client)
    people = [join(client, pin) for _ in range(3)]
    start(client, pin, 'prisoner')
    for p in people:
        submit(client, pin, p, {'choice': 'C'})  # the resting one is rejected
    advance(client, pin)
    text = client.get(f'/api/export?pin={pin}', headers=H).text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 3
    assert sorted(r['role'] for r in rows) == ['', '', 'resting']
    played = [r for r in rows if r['role'] != 'resting']
    assert {r['pair_or_group'] for r in played} == {'1'}
    rest = [r for r in rows if r['role'] == 'resting'][0]
    assert rest['decision_json'] == '' and float(rest['payoff']) == 0


@pytest.mark.parametrize('n,sizes', [(2, [2]), (5, [5]), (6, [3, 3]), (10, [4, 3, 3]), (14, [4, 4, 3, 3]), (18, [4, 4, 4, 3, 3]), (20, [4, 4, 4, 4, 4])])
def test_public_goods_group_sizes(client, n, sizes):
    import server
    groups = server.layout_for('public', [{'id': str(i)} for i in range(n)])['groups']
    assert [len(g) for g in groups] == sizes


def test_ultimatum_accept_must_be_boolean(client):
    pin = new_session(client)
    a, b = join(client, pin), join(client, pin)
    start(client, pin, 'ultimatum')
    roles = {p['label']: client.get(f'/api/state?pin={pin}&secret={p["secret"]}').json()['round']['role'] for p in (a, b)}
    proposer, responder = (a, b) if roles[a['label']] == 'proposer' else (b, a)
    assert submit(client, pin, proposer, {'offer': 40}).status_code == 200
    advance(client, pin)
    assert submit(client, pin, responder, {}).status_code == 400
    assert submit(client, pin, responder, {'accept': True}).status_code == 200
    advance(client, pin)
    pts = sorted(x['points'] for x in admin_state(client, pin)['round']['results'])
    assert pts == [40, 60]


def test_old_database_is_migrated(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / 'old.db'
    c = sqlite3.connect(path)
    c.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,pin TEXT UNIQUE,created TEXT,round_num INTEGER DEFAULT 0);
    CREATE TABLE participants(id TEXT PRIMARY KEY,session_id TEXT,label TEXT,secret TEXT UNIQUE,joined TEXT);
    INSERT INTO sessions VALUES('s1','123456','2026-01-01',0);
    INSERT INTO participants VALUES('p1','s1','Old Name','sec1','2026-01-01');''')
    c.commit(); c.close()
    monkeypatch.setenv('GTL_DB', str(path))
    monkeypatch.setenv('GTL_ADMIN_PASSWORD', PW)
    import server
    importlib.reload(server)
    cl = TestClient(server.app)
    assert cl.get('/api/state?pin=123456&secret=sec1').status_code == 200
    assert join(cl, '123456')['label'] == 'Participant 02'

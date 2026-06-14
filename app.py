import os
import secrets
import hmac
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from markupsafe import escape
from pymongo import MongoClient

app = Flask(__name__)
app.secret_key = os.urandom(32)

_db = None

def get_db():
    global _db
    if _db is not None:
        return _db
    uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
    name = os.environ.get('MONGO_DB', 'spliteasy')
    c = MongoClient(uri, serverSelectionTimeoutMS=10000)
    _db = c[name]
    _db['groups'].create_index('code', unique=True)
    _db['expenses'].create_index('group_id')
    return _db

def simplify_debts(balances):
    creditors = []
    debtors = []
    for person, bal in balances.items():
        if bal > 0.01:
            creditors.append([person, round(bal, 2)])
        elif bal < -0.01:
            debtors.append([person, round(-bal, 2)])
    transactions = []
    creditors.sort(key=lambda x: -x[1])
    debtors.sort(key=lambda x: -x[1])
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        d_amt = debtors[i][1]
        c_amt = creditors[j][1]
        transfer = min(d_amt, c_amt)
        if transfer > 0.01:
            transactions.append({
                "from": debtors[i][0],
                "to": creditors[j][0],
                "amount": round(transfer, 2)
            })
        debtors[i][1] -= transfer
        creditors[j][1] -= transfer
        if debtors[i][1] < 0.01:
            i += 1
        if creditors[j][1] < 0.01:
            j += 1
    return transactions

def compute_balances(group, expenses):
    """Compute balances using per-expense participants."""
    members = group["members"]
    balances = {m: 0.0 for m in members}
    for exp in expenses:
        amount = exp["amount"]
        payer = exp["payer"]
        participants = exp.get("participants", members)  # backfill: all members
        if not participants:
            participants = members
        share = round(amount / len(participants), 2)
        # Credit the payer for the full amount they paid
        if payer in balances:
            balances[payer] += amount
        # Debit each participant their share
        for p in participants:
            if p in balances:
                balances[p] -= share
    balances = {k: round(v, 2) for k, v in balances.items()}
    return balances

BASE_TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>{{ title }} — SplitEasy</title>
<style>
  :root {
    --bg: #0f0f0f; --card: #1a1a1a; --card2: #242424;
    --border: #2a2a2a; --text: #e8e8e8; --text2: #888;
    --accent: #6c5ce7; --accent2: #a29bfe;
    --green: #00b894; --red: #ff6b6b; --yellow: #fdcb6e; --blue: #74b9ff;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
    -webkit-tap-highlight-color: transparent;
  }
  .container { max-width: 480px; margin: 0 auto; padding: 16px; padding-bottom: 100px; }
  .header { text-align: center; padding: 24px 0 16px; }
  .header h1 {
    font-size: 28px; font-weight: 800;
    background: linear-gradient(135deg, var(--accent), var(--green));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .subtitle { color: var(--text2); font-size: 14px; margin-top: 4px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; padding: 20px; margin-bottom: 12px;
  }
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: var(--text2); margin-bottom: 12px; }
  .balance-grid { display: grid; gap: 8px; }
  .balance-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 12px 16px; background: var(--card2); border-radius: 12px; font-size: 15px;
  }
  .balance-row .name { font-weight: 600; }
  .balance-row .amount { font-weight: 700; }
  .positive { color: var(--green); } .negative { color: var(--red); } .zero { color: var(--text2); }
  .settlement {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 16px; background: var(--card2); border-radius: 12px; margin-bottom: 8px; font-size: 14px;
  }
  .settlement .arrow { color: var(--accent2); font-size: 18px; }
  .settlement .amount { font-weight: 700; color: var(--yellow); margin-left: auto; }
  .expense {
    display: flex; align-items: center; gap: 10px;
    padding: 14px; background: var(--card2); border-radius: 12px; margin-bottom: 8px;
  }
  .expense .icon {
    width: 38px; height: 38px; border-radius: 10px;
    display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0;
  }
  .expense .details { flex: 1; min-width: 0; }
  .expense .desc { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .expense .meta { font-size: 11px; color: var(--text2); margin-top: 2px; }
  .expense .amount { font-weight: 700; font-size: 15px; color: var(--green); flex-shrink: 0; }
  .expense-actions { display: flex; gap: 4px; flex-shrink: 0; }
  .edit-btn, .delete-btn { background: none; border: none; font-size: 16px; cursor: pointer; padding: 4px 6px; opacity: 0.6; }
  .edit-btn:hover, .delete-btn:hover { opacity: 1; }
  .form-group { margin-bottom: 16px; }
  .form-group label { display: block; font-size: 13px; color: var(--text2); margin-bottom: 6px; font-weight: 500; }
  input, select {
    width: 100%; padding: 14px 16px; background: var(--card2);
    border: 1px solid var(--border); border-radius: 12px; color: var(--text);
    font-size: 16px; outline: none; -webkit-appearance: none;
  }
  input:focus { border-color: var(--accent); }
  .btn {
    display: block; width: 100%; padding: 16px; border: none; border-radius: 14px;
    font-size: 16px; font-weight: 700; cursor: pointer; text-align: center;
    text-decoration: none; transition: opacity 0.2s;
  }
  .btn:active { opacity: 0.8; }
  .btn-primary { background: var(--accent); color: white; }
  .btn-green { background: var(--green); color: white; }
  .btn-red { background: var(--red); color: white; }
  .btn-outline { background: transparent; border: 2px solid var(--accent); color: var(--accent2); }
  .member-chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip {
    padding: 10px 18px; border-radius: 20px; border: 2px solid var(--border);
    background: var(--card2); color: var(--text); font-size: 14px; font-weight: 600;
    cursor: pointer; transition: all 0.2s;
  }
  .chip.active { border-color: var(--accent); background: var(--accent); color: white; }
  .chip.checkbox-chip { user-select: none; }
  .tabs { display: flex; background: var(--card); border-radius: 14px; padding: 4px; margin-bottom: 16px; border: 1px solid var(--border); }
  .tab { flex: 1; padding: 12px; text-align: center; border-radius: 11px; font-size: 13px; font-weight: 600; color: var(--text2); text-decoration: none; cursor: pointer; }
  .tab.active { background: var(--accent); color: white; }
  .empty { text-align: center; padding: 40px 20px; color: var(--text2); }
  .empty .emoji { font-size: 48px; margin-bottom: 12px; }
  .empty p { font-size: 15px; }
  .total-bar {
    display: flex; justify-content: space-between; align-items: center;
    padding: 16px 20px; background: linear-gradient(135deg, var(--accent), #00b894);
    border-radius: 14px; margin-bottom: 16px;
  }
  .total-bar .label { font-size: 13px; opacity: 0.85; }
  .total-bar .value { font-size: 24px; font-weight: 800; }
  .cat-food { background: #2d1b1b; } .cat-travel { background: #1b2d2d; }
  .cat-shopping { background: #1b1b2d; } .cat-bill { background: #2d2d1b; }
  .cat-fun { background: #2d1b2d; } .cat-other { background: #1b1b1b; }
  .add-btn {
    position: fixed; bottom: 24px; right: 24px; width: 60px; height: 60px;
    border-radius: 50%; background: var(--accent); color: white; font-size: 32px;
    border: none; cursor: pointer; box-shadow: 0 4px 20px rgba(108,92,231,0.5);
    display: flex; align-items: center; justify-content: center; z-index: 50;
    text-decoration: none; line-height: 1;
  }
  .group-code {
    font-size: 32px; font-weight: 800; letter-spacing: 8px; text-align: center;
    padding: 20px; background: var(--card2); border-radius: 12px; margin: 16px 0; color: var(--accent2);
  }
  .share-box { background: var(--card2); border-radius: 12px; padding: 16px; margin-top: 12px; }
  .share-box input { background: var(--bg); font-size: 13px; padding: 10px 12px; }
  .donut-container { text-align: center; margin-bottom: 16px; }
  .donut-legend { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 8px; }
  .legend-item { display: flex; align-items: center; gap: 4px; font-size: 12px; color: var(--text2); }
  .legend-color { width: 12px; height: 12px; border-radius: 3px; }
  .error-box { background: #2d1b1b; border: 1px solid var(--red); border-radius: 12px; padding: 16px; margin-bottom: 16px; }
  .error-box p { color: var(--red); font-size: 14px; }
</style>
</head>
<body>
{{ content | safe }}
</body>
</html>'''


@app.route('/')
def index():
    html = '''
    <div class="container">
      <div class="header">
        <h1>⚡ SplitEasy</h1>
        <div class="subtitle">Split expenses with friends. Simply.</div>
      </div>
      <div class="card">
        <div class="card-title">Create a new group</div>
        <form action="/create" method="POST">
          <div class="form-group">
            <label>Group Name</label>
            <input type="text" name="group_name" placeholder="e.g. Goa Trip 🏖️" required>
          </div>
          <div class="form-group">
            <label>Your Name</label>
            <input type="text" name="member_name" placeholder="Enter your name" required>
          </div>
          <button type="submit" class="btn btn-primary">Create Group →</button>
        </form>
      </div>
      <div class="card">
        <div class="card-title">Join existing group</div>
        <form action="/join" method="POST">
          <div class="form-group">
            <label>Group Code</label>
            <input type="text" name="group_code" placeholder="Enter 6-letter code" required style="text-transform:uppercase; letter-spacing:4px; font-size:20px; text-align:center;">
          </div>
          <div class="form-group">
            <label>Your Name</label>
            <input type="text" name="member_name" placeholder="Enter your name" required>
          </div>
          <button type="submit" class="btn btn-green">Join Group →</button>
        </form>
      </div>
    </div>'''
    return render_template_string(BASE_TEMPLATE, title="SplitEasy", content=html)


@app.route('/health')
def health():
    try:
        db = get_db()
        db.command('ping')
        groups = db['groups'].count_documents({})
        return jsonify({"status": "ok", "database": "connected", "groups": groups})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/create', methods=['POST'])
def create_group():
    group_name = request.form['group_name'].strip()
    member_name = request.form['member_name'].strip()
    group_id = secrets.token_urlsafe(16)
    code = secrets.token_hex(3).upper()
    try:
        db = get_db()
        db['groups'].insert_one({
            "code": code, "id": group_id, "name": group_name,
            "members": [member_name], "created": datetime.now().isoformat()
        })
    except Exception as e:
        return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
        <div class="container"><div class="empty"><div class="emoji">⚠️</div><p>Database error: {escape(str(e))}</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 500
    return redirect(url_for('group_view', code=code))


@app.route('/join', methods=['POST'])
def join_group():
    code = request.form['group_code'].strip().upper()
    member_name = request.form['member_name'].strip()
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code})
    except Exception as e:
        return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
        <div class="container"><div class="empty"><div class="emoji">⚠️</div><p>Database error: {escape(str(e))}</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 500
    if not group:
        return render_template_string(BASE_TEMPLATE, title="Error", content='''
        <div class="container"><div class="empty"><div class="emoji">😕</div><p>Group not found. Check the code!</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 404
    if member_name not in group["members"]:
        db['groups'].update_one({"code": code}, {"$addToSet": {"members": member_name}})
    return redirect(url_for('group_view', code=code))


@app.route('/g/<code>')
def group_view(code):
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code.upper()})
    except Exception as e:
        return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
        <div class="container"><div class="empty"><div class="emoji">⚠️</div><p>Database error: {escape(str(e))}</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 500
    if not group:
        return redirect('/')
    group_id = group["id"]
    expenses = list(db['expenses'].find({"group_id": group_id}).sort("_id", 1))
    total = sum(e["amount"] for e in expenses)
    group.pop("_id", None)
    for e in expenses:
        e.pop("_id", None)

    balances = compute_balances(group, expenses)
    debts = simplify_debts(balances)

    cat_icons = {"🍽️ Food":"🍽️","✈️ Travel":"✈️","🛒 Shopping":"🛒","📱 Bills":"📱","🎉 Fun":"🎉","📦 Other":"📦"}
    cat_classes = {"🍽️ Food":"cat-food","✈️ Travel":"cat-travel","🛒 Shopping":"cat-shopping","📱 Bills":"cat-bill","🎉 Fun":"cat-fun","📦 Other":"cat-other"}
    share_url = request.url_root + 'g/' + code.upper()

    # Category totals for donut chart
    cat_totals = {}
    for e in expenses:
        cat = e.get('category', '📦 Other')
        cat_totals[cat] = cat_totals.get(cat, 0) + e["amount"]
    cat_colors = {"🍽️ Food":"#e17055","✈️ Travel":"#00b894","🛒 Shopping":"#6c5ce7","📱 Bills":"#fdcb6e","🎉 Fun":"#e84393","📦 Other":"#636e72"}

    donut_html = ""
    if cat_totals:
        # Build SVG donut
        radius = 70
        circumference = 2 * 3.14159 * radius
        offset = 0
        slices = []
        legend_items = []
        for cat, amt in sorted(cat_totals.items(), key=lambda x: -x[1]):
            pct = amt / total
            dash = pct * circumference
            gap = circumference - dash
            color = cat_colors.get(cat, "#636e72")
            slices.append(f'<circle cx="80" cy="80" r="{radius}" fill="none" stroke="{color}" stroke-width="20" stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-dashoffset="{-offset:.1f}" transform="rotate(-90 80 80)"/>')
            offset += dash
            legend_items.append(f'<div class="legend-item"><div class="legend-color" style="background:{color}"></div>{escape(cat)} ₹{amt:,.0f}</div>')
        donut_html = f'''<div class="donut-container">
            <svg width="160" height="160" viewBox="0 0 160 160">{''.join(slices)}</svg>
            <div class="donut-legend">{''.join(legend_items)}</div>
        </div>'''

    settlement_html = ""
    for d in debts:
        settlement_html += f'''<div class="settlement">
            <span style="font-weight:600">{escape(d['from'])}</span>
            <span class="arrow">→</span>
            <span style="font-weight:600">{escape(d['to'])}</span>
            <span class="amount">₹{d['amount']:,.0f}</span>
          </div>'''
    if not debts:
        settlement_html = '<div class="empty"><div class="emoji">🎉</div><p>All settled up!</p></div>'

    balance_html = ""
    for k, v in sorted(balances.items(), key=lambda x: -x[1]):
        cls = "positive" if v > 0.01 else ("negative" if v < -0.01 else "zero")
        sign = "+" if v > 0 else ""
        emoji = "🟢" if v > 0.01 else ("🔴" if v < -0.01 else "⚪")
        balance_html += f'''<div class="balance-row">
              <span class="name">{emoji} {escape(k)}</span>
              <span class="amount {cls}">{sign}₹{v:,.0f}</span>
            </div>'''

    expense_html = ""
    for e in reversed(expenses):
        cat = e.get('category', '📦 Other')
        participants = e.get('participants', group["members"])
        participant_str = ", ".join(escape(p) for p in participants)
        expense_html += f'''<div class="expense">
          <div class="icon {cat_classes.get(cat,'cat-other')}">{cat_icons.get(cat,'📦')}</div>
          <div class="details">
            <div class="desc">{escape(e['description'])}</div>
            <div class="meta">Paid by {escape(e['payer'])} · {e.get('date','')} · Split: {participant_str}</div>
          </div>
          <div class="amount">₹{e['amount']:,.0f}</div>
          <div class="expense-actions">
            <a href="/edit/{code}/{e.get('id','')}" class="edit-btn" title="Edit">✏️</a>
            <form action="/delete/{code}/{e.get('id','')}" method="POST" style="display:inline">
              <button class="delete-btn" onclick="return confirm('Delete this expense?')" title="Delete">🗑️</button>
            </form>
          </div>
        </div>'''
    if not expenses:
        expense_html = '<div class="empty"><div class="emoji">🧾</div><p>No expenses yet.<br>Add the first one!</p></div>'

    members_count = len(group['members'])
    per_person = total / members_count if members_count else 0

    html = f'''<div class="container">
      <div class="header">
        <h1>{escape(group['name'])}</h1>
        <div class="subtitle">{", ".join(escape(m) for m in group['members'])} · {len(expenses)} expenses</div>
      </div>
      <div class="tabs">
        <div class="tab active" onclick="showTab('balances',this)">Balances</div>
        <div class="tab" onclick="showTab('expenses',this)">Expenses</div>
        <div class="tab" onclick="showTab('share',this)">Share</div>
      </div>
      <div id="tab-balances">
        <div class="total-bar">
          <div><div class="label">Total Spent</div><div class="value">₹{total:,.0f}</div></div>
          <div style="text-align:right"><div class="label">Per Person</div><div class="value" style="font-size:18px">₹{per_person:,.0f}</div></div>
        </div>
        {donut_html}
        <div class="card">
          <div class="card-title">Who owes whom</div>
          {settlement_html}
        </div>
        <div class="card">
          <div class="card-title">Net Balances</div>
          <div class="balance-grid">{balance_html}</div>
        </div>
      </div>
      <div id="tab-expenses" style="display:none">{expense_html}</div>
      <div id="tab-share" style="display:none">
        <div class="card">
          <div class="card-title">Invite Friends</div>
          <p style="color:var(--text2);font-size:14px;margin-bottom:12px;">Share this link in your WhatsApp group:</p>
          <div class="share-box">
            <input type="text" value="{share_url}" readonly onclick="navigator.clipboard.writeText(this.value)" style="cursor:pointer">
          </div>
          <button class="btn btn-green" style="margin-top:12px" onclick="navigator.clipboard.writeText('{share_url}')">📋 Copy Link</button>
          <a href="https://wa.me/?text=Join our expense group! {share_url}" class="btn btn-outline" style="margin-top:8px" target="_blank">💬 Share on WhatsApp</a>
        </div>
        <div class="card">
          <div class="card-title">Group Code</div>
          <div class="group-code">{code.upper()}</div>
          <p style="text-align:center;color:var(--text2);font-size:13px;">Share this code for others to join</p>
        </div>
      </div>
      <a href="/add/{code}" class="add-btn" title="Add Expense">+</a>
    </div>
    <script>
    function showTab(name, el) {{
      document.querySelectorAll('[id^="tab-"]').forEach(e => e.style.display='none');
      document.getElementById('tab-'+name).style.display='block';
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      el.classList.add('active');
    }}
    </script>'''
    return render_template_string(BASE_TEMPLATE, title=group['name'], content=html)


@app.route('/add/<code>', methods=['GET', 'POST'])
def add_expense(code):
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code.upper()})
    except Exception as e:
        return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
        <div class="container"><div class="empty"><div class="emoji">⚠️</div><p>Database error: {escape(str(e))}</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 500
    if not group:
        return redirect('/')
    if request.method == 'POST':
        description = request.form['description'].strip()
        amount = float(request.form['amount'])
        payer = request.form['payer']
        category = request.form.get('category', '📦 Other')
        participants = request.form.getlist('participants')
        if not participants:
            return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
            <div class="container"><div class="error-box"><p>At least one participant must be selected.</p>
            <a href="/add/{code}" class="btn btn-outline" style="margin-top:12px;">Go Back</a></div></div>'''), 400
        db['expenses'].insert_one({
            "id": secrets.token_hex(8), "group_id": group["id"],
            "description": description, "amount": amount, "payer": payer,
            "category": category, "participants": participants,
            "date": datetime.now().strftime("%d %b"), "created": datetime.now().isoformat()
        })
        return redirect(url_for('group_view', code=code))

    categories = ["🍽️ Food", "✈️ Travel", "🛒 Shopping", "📱 Bills", "🎉 Fun", "📦 Other"]
    payer_chips = ""
    for i, m in enumerate(group["members"]):
        payer_chips += f'<div class="chip {"active" if i==0 else ""}" onclick="selectPayer(this)">{escape(m)}</div>'
    cat_chips = ""
    for i, c in enumerate(categories):
        cat_chips += f'<div class="chip {"active" if i==0 else ""}" onclick="selectChip(this)" data-value="{c}">{c}</div>'
    # Participant checkboxes — all pre-selected
    participant_chips = ""
    for m in group["members"]:
        participant_chips += f'<label class="chip checkbox-chip active" onclick="toggleChip(this)"><input type="checkbox" name="participants" value="{escape(m)}" checked style="display:none">{escape(m)}</label>'

    html = f'''<div class="container">
      <div class="header">
        <h1>➕ Add Expense</h1>
        <div class="subtitle">{escape(group['name'])}</div>
      </div>
      <form method="POST">
        <div class="card">
          <div class="form-group">
            <label>What was this for?</label>
            <input type="text" name="description" placeholder="e.g. Dinner at McDonald's" required autofocus>
          </div>
          <div class="form-group">
            <label>Amount (₹)</label>
            <input type="number" name="amount" placeholder="0" min="1" step="0.01" required inputmode="decimal">
          </div>
          <div class="form-group">
            <label>Category</label>
            <div class="member-chips">{cat_chips}</div>
            <input type="hidden" name="category" id="category" value="{categories[0]}">
          </div>
          <div class="form-group">
            <label>Who paid?</label>
            <div class="member-chips" id="payer-chips">{payer_chips}</div>
            <input type="hidden" name="payer" id="payer" value="{escape(group['members'][0])}">
          </div>
          <div class="form-group">
            <label>Split among</label>
            <div class="member-chips" id="participant-chips">{participant_chips}</div>
          </div>
        </div>
        <button type="submit" class="btn btn-primary" style="margin-top:8px">Add Expense ✓</button>
        <a href="/g/{code}" class="btn btn-outline" style="margin-top:8px">Cancel</a>
      </form>
    </div>
    <script>
    function selectChip(el) {{
      el.parentElement.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('category').value = el.dataset.value;
    }}
    function selectPayer(el) {{
      document.querySelectorAll('#payer-chips .chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('payer').value = el.textContent;
    }}
    function toggleChip(el) {{
      el.classList.toggle('active');
      var cb = el.querySelector('input[type=checkbox]');
      cb.checked = !cb.checked;
    }}
    </script>'''
    return render_template_string(BASE_TEMPLATE, title="Add Expense", content=html)


@app.route('/edit/<code>/<exp_id>', methods=['GET', 'POST'])
def edit_expense(code, exp_id):
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code.upper()})
    except Exception as e:
        return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
        <div class="container"><div class="empty"><div class="emoji">⚠️</div><p>Database error: {escape(str(e))}</p>
        <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div></div>'''), 500
    if not group:
        return redirect('/')
    expense = db['expenses'].find_one({"id": exp_id, "group_id": group["id"]})
    if not expense:
        return redirect(url_for('group_view', code=code))

    if request.method == 'POST':
        description = request.form['description'].strip()
        amount = float(request.form['amount'])
        payer = request.form['payer']
        category = request.form.get('category', '📦 Other')
        participants = request.form.getlist('participants')
        if not participants:
            return render_template_string(BASE_TEMPLATE, title="Error", content=f'''
            <div class="container"><div class="error-box"><p>At least one participant must be selected.</p>
            <a href="/edit/{code}/{exp_id}" class="btn btn-outline" style="margin-top:12px;">Go Back</a></div></div>'''), 400
        db['expenses'].update_one(
            {"id": exp_id},
            {"$set": {
                "description": description, "amount": amount, "payer": payer,
                "category": category, "participants": participants
            }}
        )
        return redirect(url_for('group_view', code=code))

    categories = ["🍽️ Food", "✈️ Travel", "🛒 Shopping", "📱 Bills", "🎉 Fun", "📦 Other"]
    current_participants = expense.get('participants', group["members"])
    payer_chips = ""
    for m in group["members"]:
        active = "active" if m == expense.get('payer', group["members"][0]) else ""
        payer_chips += f'<div class="chip {active}" onclick="selectPayer(this)">{escape(m)}</div>'
    cat_chips = ""
    for c in categories:
        active = "active" if c == expense.get('category', '📦 Other') else ""
        cat_chips += f'<div class="chip {active}" onclick="selectChip(this)" data-value="{c}">{c}</div>'
    participant_chips = ""
    for m in group["members"]:
        checked = "checked" if m in current_participants else ""
        active = "active" if m in current_participants else ""
        participant_chips += f'<label class="chip checkbox-chip {active}" onclick="toggleChip(this)"><input type="checkbox" name="participants" value="{escape(m)}" {checked} style="display:none">{escape(m)}</label>'

    html = f'''<div class="container">
      <div class="header">
        <h1>✏️ Edit Expense</h1>
        <div class="subtitle">{escape(group['name'])}</div>
      </div>
      <form method="POST">
        <div class="card">
          <div class="form-group">
            <label>What was this for?</label>
            <input type="text" name="description" value="{escape(expense.get('description',''))}" required autofocus>
          </div>
          <div class="form-group">
            <label>Amount (₹)</label>
            <input type="number" name="amount" value="{expense.get('amount',0)}" min="1" step="0.01" required inputmode="decimal">
          </div>
          <div class="form-group">
            <label>Category</label>
            <div class="member-chips">{cat_chips}</div>
            <input type="hidden" name="category" id="category" value="{expense.get('category','📦 Other')}">
          </div>
          <div class="form-group">
            <label>Who paid?</label>
            <div class="member-chips" id="payer-chips">{payer_chips}</div>
            <input type="hidden" name="payer" id="payer" value="{escape(expense.get('payer', group['members'][0]))}">
          </div>
          <div class="form-group">
            <label>Split among</label>
            <div class="member-chips" id="participant-chips">{participant_chips}</div>
          </div>
        </div>
        <button type="submit" class="btn btn-primary" style="margin-top:8px">Save Changes ✓</button>
        <a href="/g/{code}" class="btn btn-outline" style="margin-top:8px">Cancel</a>
      </form>
    </div>
    <script>
    function selectChip(el) {{
      el.parentElement.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('category').value = el.dataset.value;
    }}
    function selectPayer(el) {{
      document.querySelectorAll('#payer-chips .chip').forEach(c => c.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('payer').value = el.textContent;
    }}
    function toggleChip(el) {{
      el.classList.toggle('active');
      var cb = el.querySelector('input[type=checkbox]');
      cb.checked = !cb.checked;
    }}
    </script>'''
    return render_template_string(BASE_TEMPLATE, title="Edit Expense", content=html)


@app.route('/delete/<code>/<exp_id>', methods=['POST'])
def delete_expense(code, exp_id):
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code.upper()})
        if group:
            db['expenses'].delete_one({"group_id": group["id"], "id": exp_id})
    except Exception:
        pass
    return redirect(url_for('group_view', code=code))


@app.route('/api/<code>')
def api_group(code):
    try:
        db = get_db()
        group = db['groups'].find_one({"code": code.upper()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    if not group:
        return jsonify({"error": "not found"}), 404
    group_id = group["id"]
    expenses = list(db['expenses'].find({"group_id": group_id}).sort("_id", 1))
    for e in expenses:
        e.pop("_id", None)
    group_doc = {k: v for k, v in group.items() if k != "_id"}
    balances = compute_balances(group_doc, expenses)
    return jsonify({
        "group": group_doc["name"],
        "members": group_doc["members"],
        "total": sum(e["amount"] for e in expenses),
        "balances": balances,
        "settlements": simplify_debts(balances),
        "expenses": expenses
    })


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # Get token from header or form field — NEVER from query string
    token = request.headers.get('X-Admin-Token') or (request.form.get('token') if request.method == 'POST' else None)
    expected = os.environ.get('ADMIN_TOKEN', '')
    if not expected or not token or not hmac.compare_digest(token, expected):
        return render_template_string(BASE_TEMPLATE, title="Forbidden", content='''
        <div class="container"><div class="empty"><div class="emoji">🔒</div><p>Access denied.</p></div></div>'''), 403

    try:
        db = get_db()
        total_groups = db['groups'].count_documents({})
        total_expenses = db['expenses'].count_documents({})
        pipeline = [{"$group": {"_id": None, "total_volume": {"$sum": "$amount"}}}]
        volume_result = list(db['expenses'].aggregate(pipeline))
        total_volume = volume_result[0]["total_volume"] if volume_result else 0
        avg_expenses = round(total_expenses / total_groups, 1) if total_groups else 0

        groups = list(db['groups'].find())
        group_rows = ""
        for g in groups:
            g_name = escape(g.get('name', 'Unnamed'))
            g_members = len(g.get('members', []))
            g_exp_count = db['expenses'].count_documents({"group_id": g["id"]})
            g_pipeline = [{"$match": {"group_id": g["id"]}}, {"$group": {"_id": None, "total": {"$sum": "$amount"}}}]
            g_vol = list(db['expenses'].aggregate(g_pipeline))
            g_total = g_vol[0]["total"] if g_vol else 0
            group_rows += f'<tr><td>{g_name}</td><td>{g_members}</td><td>{g_exp_count}</td><td>₹{g_total:,.0f}</td></tr>'

        html = f'''<div class="container">
          <div class="header"><h1>📊 Admin Analytics</h1></div>
          <div class="card">
            <div class="card-title">Overview</div>
            <div class="balance-grid">
              <div class="balance-row"><span class="name">Total Groups</span><span class="amount zero">{total_groups}</span></div>
              <div class="balance-row"><span class="name">Total Expenses</span><span class="amount zero">{total_expenses}</span></div>
              <div class="balance-row"><span class="name">Total Volume</span><span class="amount positive">₹{total_volume:,.0f}</span></div>
              <div class="balance-row"><span class="name">Avg Expenses/Group</span><span class="amount zero">{avg_expenses}</span></div>
            </div>
          </div>
          <div class="card">
            <div class="card-title">Groups</div>
            <table style="width:100%;font-size:14px;border-collapse:collapse;">
              <tr style="color:var(--text2);text-align:left;border-bottom:1px solid var(--border);">
                <th style="padding:8px 4px;">Name</th><th style="padding:8px 4px;">Members</th><th style="padding:8px 4px;">Expenses</th><th style="padding:8px 4px;">Total</th>
              </tr>
              {group_rows}
            </table>
          </div>
        </div>'''
        return render_template_string(BASE_TEMPLATE, title="Admin", content=html)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

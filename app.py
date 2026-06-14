import os
import secrets
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from markupsafe import escape
from pymongo import MongoClient

app = Flask(__name__)
app.secret_key = os.urandom(32)

_MONGO_URI = None
_db = None

def get_db():
    global _MONGO_URI, _db
    if _db is not None:
        return _db
    uri = os.environ.get('MONGO_URI', 'mongodb://localhost:27017')
    name = os.environ.get('MONGO_DB', 'spliteasy')
    _MONGO_URI = uri
    c = MongoClient(uri, serverSelectionTimeoutMS=10000)
    _db = c[name]
    _db['groups'].create_index('code', unique=True)
    _db['expenses'].create_index('group_id')
    return _db

def simplify_debts(balances):
    """Minimize number of transactions to settle up."""
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

# ─── HTML Template ───

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
    display: flex; align-items: center; gap: 14px;
    padding: 16px; background: var(--card2); border-radius: 12px; margin-bottom: 8px;
  }
  .expense .icon {
    width: 42px; height: 42px; border-radius: 12px;
    display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0;
  }
  .expense .details { flex: 1; min-width: 0; }
  .expense .desc { font-weight: 600; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .expense .meta { font-size: 12px; color: var(--text2); margin-top: 2px; }
  .expense .amount { font-weight: 700; font-size: 16px; color: var(--green); flex-shrink: 0; }
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
  .delete-btn { background: none; border: none; color: var(--red); font-size: 18px; cursor: pointer; padding: 4px 8px; opacity: 0.6; }
  .delete-btn:hover { opacity: 1; }
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
</style>
</head>
<body>
{{ content | safe }}
</body>
</html>'''


# ─── Routes ───

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


@app.route('/create', methods=['POST'])
def create_group():
    group_name = request.form['group_name'].strip()
    member_name = request.form['member_name'].strip()
    group_id = secrets.token_urlsafe(16)
    code = secrets.token_hex(3).upper()

    db = get_db()
    db['groups'].insert_one({
        "code": code,
        "id": group_id,
        "name": group_name,
        "members": [member_name],
        "created": datetime.now().isoformat()
    })

    return redirect(url_for('group_view', code=code))


@app.route('/join', methods=['POST'])
def join_group():
    code = request.form['group_code'].strip().upper()
    member_name = request.form['member_name'].strip()

    db = get_db()
    group = db['groups'].find_one({"code": code})
    if not group:
        return render_template_string(BASE_TEMPLATE, title="Error", content='''
        <div class="container">
          <div class="empty"><div class="emoji">😕</div><p>Group not found. Check the code!</p>
          <a href="/" class="btn btn-primary" style="margin-top:20px;">Go Back</a></div>
        </div>'''), 404

    if member_name not in group["members"]:
        db['groups'].update_one({"code": code}, {"$addToSet": {"members": member_name}})

    return redirect(url_for('group_view', code=code))


@app.route('/g/<code>')
def group_view(code):
    db = get_db()
    group = db['groups'].find_one({"code": code.upper()})
    if not group:
        return redirect('/')

    group_id = group["id"]
    expenses = list(db['expenses'].find({"group_id": group_id}).sort("_id", 1))
    total = sum(e["amount"] for e in expenses)
    group.pop("_id", None)
    for e in expenses:
        e.pop("_id", None)

    # Calculate balances
    balances = {m: 0.0 for m in group["members"]}
    for exp in expenses:
        amount = exp["amount"]
        payer = exp["payer"]
        share = round(amount / len(group["members"]), 2)
        for m in group["members"]:
            if m == payer:
                balances[m] += amount - share
            else:
                balances[m] -= share
    balances = {k: round(v, 2) for k, v in balances.items()}
    debts = simplify_debts(balances)

    cat_icons = {"🍽️ Food":"🍽️","✈️ Travel":"✈️","🛒 Shopping":"🛒","📱 Bills":"📱","🎉 Fun":"🎉","📦 Other":"📦"}
    cat_classes = {"🍽️ Food":"cat-food","✈️ Travel":"cat-travel","🛒 Shopping":"cat-shopping","📱 Bills":"cat-bill","🎉 Fun":"cat-fun","📦 Other":"cat-other"}

    share_url = request.url_root + 'g/' + code.upper()

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
        expense_html += f'''<div class="expense">
          <div class="icon {cat_classes.get(cat,'cat-other')}">{cat_icons.get(cat,'📦')}</div>
          <div class="details">
            <div class="desc">{escape(e['description'])}</div>
            <div class="meta">Paid by {escape(e['payer'])} · {e.get('date','')}</div>
          </div>
          <div class="amount">₹{e['amount']:,.0f}</div>
          <form action="/delete/{code}/{e.get('id','')}" method="POST" style="display:inline">
            <button class="delete-btn" onclick="return confirm('Delete this expense?')">🗑️</button>
          </form>
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
    db = get_db()
    group = db['groups'].find_one({"code": code.upper()})
    if not group:
        return redirect('/')

    if request.method == 'POST':
        description = request.form['description'].strip()
        amount = float(request.form['amount'])
        payer = request.form['payer']
        category = request.form.get('category', '📦 Other')

        db['expenses'].insert_one({
            "id": secrets.token_hex(8),
            "group_id": group["id"],
            "description": description,
            "amount": amount,
            "payer": payer,
            "category": category,
            "date": datetime.now().strftime("%d %b"),
            "created": datetime.now().isoformat()
        })

        return redirect(url_for('group_view', code=code))

    categories = ["🍽️ Food", "✈️ Travel", "🛒 Shopping", "📱 Bills", "🎉 Fun", "📦 Other"]
    member_chips = ""
    payer_chips = ""
    for i, m in enumerate(group["members"]):
        payer_chips += f'<div class="chip {"active" if i==0 else ""}" onclick="selectPayer(this)">{escape(m)}</div>'
    for i, c in enumerate(categories):
        member_chips += f'<div class="chip {"active" if i==0 else ""}" onclick="selectChip(this)" data-value="{c}">{c}</div>'

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
            <div class="member-chips">{member_chips}</div>
            <input type="hidden" name="category" id="category" value="{categories[0]}">
          </div>
          <div class="form-group">
            <label>Who paid?</label>
            <div class="member-chips" id="payer-chips">{payer_chips}</div>
            <input type="hidden" name="payer" id="payer" value="{escape(group['members'][0])}">
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
    </script>'''

    return render_template_string(BASE_TEMPLATE, title="Add Expense", content=html)


@app.route('/delete/<code>/<exp_id>', methods=['POST'])
def delete_expense(code, exp_id):
    db = get_db()
    group = db['groups'].find_one({"code": code.upper()})
    if group:
        db['expenses'].delete_one({"group_id": group["id"], "id": exp_id})
    return redirect(url_for('group_view', code=code))


@app.route('/api/<code>')
def api_group(code):
    db = get_db()
    group = db['groups'].find_one({"code": code.upper()})
    if not group:
        return jsonify({"error": "not found"}), 404

    group_id = group["id"]
    expenses = list(db['expenses'].find({"group_id": group_id}).sort("_id", 1))
    # Remove MongoDB's _id field (not JSON serializable)
    for e in expenses:
        e.pop("_id", None)
    group_doc = {k: v for k, v in group.items() if k != "_id"}

    balances = {m: 0.0 for m in group_doc["members"]}
    for exp in expenses:
        amount = exp["amount"]
        payer = exp["payer"]
        share = round(amount / len(group_doc["members"]), 2)
        for m in group_doc["members"]:
            if m == payer:
                balances[m] += amount - share
            else:
                balances[m] -= share

    return jsonify({
        "group": group_doc["name"],
        "members": group_doc["members"],
        "total": sum(e["amount"] for e in expenses),
        "balances": {k: round(v, 2) for k, v in balances.items()},
        "settlements": simplify_debts(balances),
        "expenses": expenses
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
